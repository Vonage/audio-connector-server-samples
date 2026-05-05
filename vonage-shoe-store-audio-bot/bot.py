import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import EndTaskFrame, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.types import RunnerArguments
from pipecat.serializers.vonage import VonageFrameSerializer
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from workflow_client import ShoeWorkflowClient

load_dotenv(override=True)

AUDIO_OUT_SAMPLE_RATE: int = 24_000
VONAGE_AUDIO_PACKET_BYTES: int = 640


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _normalize_e164(raw_phone: str) -> str:
    text = raw_phone.strip()
    if not text:
        return ""

    normalized = re.sub(r"[^\d+]", "", text)
    if normalized.startswith("00"):
        normalized = "+" + normalized[2:]

    if not normalized.startswith("+"):
        return ""

    digits = normalized[1:]
    if not digits.isdigit() or len(digits) < 8 or len(digits) > 15:
        return ""

    return "+" + digits


def _spoken_phone(e164_phone: str) -> str:
    if not e164_phone:
        return ""
    digits = e164_phone[1:]
    return "plus " + " ".join(digits)


def _append_transcript_line(session_id: str, payload: dict[str, str]) -> None:
    transcript_dir = Path(_env_text("TRANSCRIPT_DIR", "transcripts"))
    transcript_dir.mkdir(parents=True, exist_ok=True)

    transcript_file = transcript_dir / f"shoe_store_{session_id}.txt"
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    lines = [
        f"[{timestamp}] customer={payload.get('customer_name', 'Unknown')} phone={payload.get('phone', '')}",
        f"intent={payload.get('intent', '')}",
        f"notes={payload.get('notes', '')}",
        "",
    ]

    with transcript_file.open("a", encoding="utf-8") as file_obj:
        file_obj.write("\n".join(lines))


async def run_bot(transport: BaseTransport, handle_sigint: bool, sample_rate: int) -> None:
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY not set. Voice agent cannot run.")
        return

    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))

    stt = OpenAIRealtimeSTTService(
        api_key=os.getenv("OPENAI_API_KEY"),
        turn_detection=None,
        settings=OpenAIRealtimeSTTService.Settings(
            model="gpt-4o-transcribe",
            prompt="Expect spoken retail requests about shoe sizes, colors, orders, and pickup windows.",
            noise_reduction="near_field",
        ),
    )

    tts = OpenAITTSService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAITTSService.Settings(
            voice="coral",
            instructions="Speak naturally, keep responses short, and do not read punctuation aloud.",
        ),
    )

    workflow = ShoeWorkflowClient()
    call_session_id = uuid.uuid4().hex[:12]
    store_name = _env_text("SHOE_STORE_NAME", "StrideRight Shoes")
    confirmed_customer_phone = ""

    def _resolved_customer_phone(candidate: str = "") -> str:
        return confirmed_customer_phone or candidate

    async def save_customer_transcript(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        payload = {
            "session_id": call_session_id,
            "customer_name": args.get("customer_name", "Unknown"),
            "phone": _resolved_customer_phone(args.get("phone", "")),
            "intent": args.get("intent", ""),
            "notes": args.get("notes", ""),
        }

        _append_transcript_line(call_session_id, payload)
        result = await workflow.save_transcript_entry(payload)
        await params.result_callback(result)

    async def confirm_customer_phone(params: FunctionCallParams) -> None:
        nonlocal confirmed_customer_phone
        args = params.arguments or {}
        raw_phone = args.get("phone", "")
        normalized_phone = _normalize_e164(raw_phone)
        if not normalized_phone:
            await params.result_callback(
                {
                    "success": False,
                    "confirmed": False,
                    "requires_country_code": True,
                    "message": "Phone must include country code in E.164 format, for example +14155552671.",
                }
            )
            return

        confirmed_customer_phone = normalized_phone
        await params.result_callback(
            {
                "success": bool(confirmed_customer_phone),
                "phone": confirmed_customer_phone,
                "confirmed": bool(confirmed_customer_phone),
                "spoken_phone": _spoken_phone(confirmed_customer_phone),
            }
        )

    async def check_shoe_inventory(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        payload = {
            "session_id": call_session_id,
            "brand": args.get("brand", ""),
            "style": args.get("style", ""),
            "size": args.get("size", ""),
            "color": args.get("color", ""),
        }
        result = await workflow.check_inventory(payload)
        await params.result_callback(result)

    async def create_shoe_order(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        if not confirmed_customer_phone:
            await params.result_callback(
                {
                    "success": False,
                    "requires_phone_confirmation": True,
                    "message": "Customer phone must be repeated with country code and confirmed before creating an order.",
                }
            )
            return

        payload = {
            "session_id": call_session_id,
            "customer_name": args.get("customer_name", "Unknown"),
            "phone": _resolved_customer_phone(args.get("phone", "")),
            "sku": args.get("sku", ""),
            "size": args.get("size", ""),
            "color": args.get("color", ""),
            "quantity": args.get("quantity", 1),
            "payment_method": args.get("payment_method", "pay-in-store"),
        }
        result = await workflow.create_order(payload)
        await params.result_callback(result)

    async def reserve_store_pickup(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        payload = {
            "session_id": call_session_id,
            "order_id": args.get("order_id", ""),
            "store_location": args.get("store_location", "Downtown"),
            "pickup_window": args.get("pickup_window", "tomorrow 4 PM to 6 PM"),
        }
        result = await workflow.book_pickup(payload)
        await params.result_callback(result)

    async def send_order_confirmation_sms(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        if not confirmed_customer_phone:
            await params.result_callback(
                {
                    "success": False,
                    "requires_phone_confirmation": True,
                    "message": "Customer phone must be confirmed before sending SMS.",
                }
            )
            return

        payload = {
            "session_id": call_session_id,
            "phone": _resolved_customer_phone(args.get("phone", "")),
            "message": args.get("message", ""),
        }
        result = await workflow.send_sms(payload)
        await params.result_callback(result)

    async def end_call(params: FunctionCallParams) -> None:
        await params.result_callback({"success": True})
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)

    llm.register_function("save_customer_transcript", save_customer_transcript)
    llm.register_function("confirm_customer_phone", confirm_customer_phone)
    llm.register_function("check_shoe_inventory", check_shoe_inventory)
    llm.register_function("create_shoe_order", create_shoe_order)
    llm.register_function("reserve_store_pickup", reserve_store_pickup)
    llm.register_function("send_order_confirmation_sms", send_order_confirmation_sms)
    llm.register_function("end_call", end_call)

    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="save_customer_transcript",
                description="Persist customer intent and key details after each major step.",
                properties={
                    "customer_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "intent": {"type": "string"},
                    "notes": {"type": "string"},
                },
                required=["customer_name", "phone", "intent", "notes"],
            ),
            FunctionSchema(
                name="confirm_customer_phone",
                description="Store the customer's phone number only after the customer confirms it is correct. The number must include a country code.",
                properties={
                    "phone": {"type": "string"},
                },
                required=["phone"],
            ),
            FunctionSchema(
                name="check_shoe_inventory",
                description="Check available SKUs by brand, style, size, and color before placing an order.",
                properties={
                    "brand": {"type": "string"},
                    "style": {"type": "string"},
                    "size": {"type": "string"},
                    "color": {"type": "string"},
                },
                required=["brand", "style", "size", "color"],
            ),
            FunctionSchema(
                name="create_shoe_order",
                description="Create a confirmed shoe order after customer accepts a specific SKU and quantity. Call this only after the customer phone has been confirmed with country code.",
                properties={
                    "customer_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "sku": {"type": "string"},
                    "size": {"type": "string"},
                    "color": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "payment_method": {"type": "string"},
                },
                required=["customer_name", "phone", "sku", "size", "color", "quantity"],
            ),
            FunctionSchema(
                name="reserve_store_pickup",
                description="Reserve pickup slot for an order at the chosen store location. Only use current or future dates and times; never use past dates.",
                properties={
                    "order_id": {"type": "string"},
                    "store_location": {"type": "string"},
                    "pickup_window": {"type": "string"},
                },
                required=["order_id", "store_location", "pickup_window"],
            ),
            FunctionSchema(
                name="send_order_confirmation_sms",
                description="Send order and pickup summary by SMS after order and pickup are finalized. Use only the confirmed customer phone number.",
                properties={
                    "phone": {"type": "string"},
                    "message": {"type": "string"},
                },
                required=["phone", "message"],
            ),
            FunctionSchema(
                name="end_call",
                description="End the call after workflow completion or if caller asks to stop.",
                properties={"reason": {"type": "string"}},
                required=["reason"],
            ),
        ]
    )

    now = datetime.now()
    today = now.date().isoformat()
    current_time = now.strftime("%I:%M %p").lstrip("0")
    catalog_summary = (
        "Available catalog: "
        "RunFast 900 black $129 (sizes 7-12), "
        "RunFast 900 white $129 (sizes 6-12), "
        "CityWalk 2 black $109 (sizes 6-11), "
        "TrailX Pro grey $149 (sizes 8-13), "
        "EasySlip navy $89 (sizes 6-10). "
        "Stores: Downtown, Eastside Mall, Westpark Center. "
        "Pickup windows: 10 AM to 1 PM or 3 PM to 6 PM."
    )
    messages = [
        {
            "role": "system",
            "content": (
                f"You are a voice shopping assistant for {store_name}. "
                f"Today is {today} and the current local time is {current_time}. Keep responses short and spoken-friendly. "
                f"{catalog_summary} "
                "Workflow: "
                "1. Greet the customer. "
                "2. Ask for name and phone number. "
                "   The phone number must include country code. If the caller gives a local number without country code, ask for the country code and restate the full number with country code. "
                "   Repeat the full number back and ask for confirmation. If the customer says it is wrong or unclear, ask again and repeat again until they confirm it is correct. "
                "   Read numbers slowly digit by digit while confirming, for example: plus 1 4 1 5 5 5 5 2 6 7 1. "
                "   Only after the customer confirms the number, call confirm_customer_phone. If the tool returns confirmed false, ask again. If the tool returns spoken_phone, use it when repeating the number. After confirmation, always use that confirmed number for all later tools. "
                "3. Ask what type of shoe, size, and preferred color. "
                "4. Call check_shoe_inventory and read up to 2 matching items with name, color, and price. "
                "   If nothing matches, suggest the closest alternative from alternatives list. "
                "5. When customer confirms an item, call create_shoe_order. "
                "   Read back the order ID and estimated ready date. "
                "6. Ask which store and preferred pickup window, then call reserve_store_pickup. "
                "   Never confirm a past date or past time. If the customer asks for a past date or past time, explain it is unavailable and offer the next available future slots. "
                "   If reserve_store_pickup returns booked false, explain the requested slot is invalid and read the future available_slots instead. "
                "   Otherwise read back the confirmed window and pickup confirmation code. "
                "7. Build an SMS message: 'StrideRight order <order_id> confirmed. "
                "   Pick up <item name> size <size> at <store>, <pickup window>. Code: <confirmation_code>.' "
                "   Then call send_order_confirmation_sms. "
                "   If sent is false, say the SMS is pending and encourage the customer to save the details. "
                "8. After confirming, offer one add-on: socks, insoles, or shoe protector spray. "
                "9. For exchange requests, ask for the prior order ID then run the same order flow. "
                "10. Call save_customer_transcript after each major step, and do not treat the phone number as final until the customer has confirmed it. "
                "11. Call end_call once all steps are done or the customer says goodbye."
            ),
        }
    ]

    context = LLMContext(messages, tools=tools)
    context_aggregator = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=sample_rate,
            audio_out_sample_rate=AUDIO_OUT_SAMPLE_RATE,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info("Vonage Audio Connector connected. Starting shoe-store workflow...")
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Vonage Audio Connector disconnected. Ending call.")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments) -> None:
    sample_rate = _env_int("VONAGE_AUDIO_RATE", 16000)

    serializer = VonageFrameSerializer(
        VonageFrameSerializer.InputParams(
            vonage_sample_rate=sample_rate,
            sample_rate=None,
        )
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            fixed_audio_packet_size=VONAGE_AUDIO_PACKET_BYTES,
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint, sample_rate)
