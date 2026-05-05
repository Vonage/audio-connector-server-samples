import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from loguru import logger
from n8n_client import N8NClient
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

load_dotenv(override=True)

AUDIO_OUT_SAMPLE_RATE: int = 24_000
VONAGE_AUDIO_PACKET_BYTES: int = 640
# 640 bytes = 20ms @ 16kHz, PCM16 mono


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _doctor_join_url(session_id: str) -> str:
    explicit = os.getenv("DOCTOR_JOIN_URL", "").strip()
    if explicit:
        return explicit

    return f"Playground session: {session_id}"


def _env_text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _parse_bool(value: Any) -> bool:
    """Parse boolean value from tool arguments, handling bool types and string representations."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return False


def _load_n8n_env_file() -> dict[str, str]:
    env_path = Path("n8n/.env")
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_sms_config() -> tuple[str, str, str]:
    api_key = _env_text("SMS_API_KEY")
    api_secret = _env_text("SMS_API_SECRET")
    sms_from = _env_text("SMS_FROM", "Vonage APIs")

    if api_key and api_secret:
        return api_key, api_secret, sms_from

    n8n_env = _load_n8n_env_file()
    api_key = api_key or n8n_env.get("SMS_API_KEY", "")
    api_secret = api_secret or n8n_env.get("SMS_API_SECRET", "")
    sms_from = sms_from or n8n_env.get("SMS_FROM", "Vonage APIs")
    return api_key, api_secret, sms_from


async def _send_sms_direct(phone: str, message: str) -> dict[str, Any]:
    api_key, api_secret, sms_from = _resolve_sms_config()
    if not api_key or not api_secret:
        return {
            "success": False,
            "sent": False,
            "provider": "vonage-direct",
            "error": "Missing SMS_API_KEY/SMS_API_SECRET",
        }

    payload = {
        "api_key": api_key,
        "api_secret": api_secret,
        "to": phone,
        "from": sms_from,
        "text": message,
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post("https://rest.nexmo.com/sms/json", data=payload)
            response.raise_for_status()
            data = response.json() if response.text else {}
            first = (data.get("messages") or [{}])[0]
            sent = first.get("status") == "0"
            return {
                "success": sent,
                "sent": sent,
                "provider": "vonage-direct",
                "status": first.get("status", "unknown"),
                "message_uuid": first.get("message-id", ""),
                "to": first.get("to", phone),
                "from": sms_from,
                "raw": data,
            }
    except Exception as exc:
        return {
            "success": False,
            "sent": False,
            "provider": "vonage-direct",
            "error": str(exc),
        }


def _append_transcript_line(triage_session_id: str, payload: dict[str, Any]) -> None:
    transcript_dir = Path(_env_text("TRANSCRIPT_DIR", "transcripts"))
    transcript_dir.mkdir(parents=True, exist_ok=True)

    transcript_file = transcript_dir / f"triage_{triage_session_id}.txt"
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    patient_name = payload.get("patient_name", "Unknown")
    phone = payload.get("phone", "")
    entry = payload.get("entry", {}) if isinstance(payload.get("entry"), dict) else {}
    question = entry.get("question", "")
    answer = entry.get("answer", "")

    lines = [
        f"[{timestamp}] patient={patient_name} phone={phone}",
        f"Q: {question}",
        f"A: {answer}",
        "",
    ]

    with transcript_file.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _parse_appointment_datetime(date_value: str, time_value: str) -> datetime | None:
    date_text = (date_value or "").strip()

    if not date_text:
        return None

    time_text = (time_value or "").strip()

    if date_text.lower() == "next-business-day":
        base_date = date.today()
        while True:
            base_date = base_date + timedelta(days=1)
            if base_date.weekday() < 5:
                break
        try:
            parsed_time = datetime.strptime(time_text or "10:30 AM", "%I:%M %p").time()
        except ValueError:
            return None
        return datetime.combine(base_date, parsed_time)

    parsed_date = None
    for date_fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            parsed_date = datetime.strptime(date_text, date_fmt).date()
            break
        except ValueError:
            continue

    if not parsed_date:
        return None

    parsed_time = None
    for time_fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            parsed_time = datetime.strptime(time_text, time_fmt).time()
            break
        except ValueError:
            continue

    if not parsed_time:
        return None

    return datetime.combine(parsed_date, parsed_time)


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
            prompt="Expect medical terms, symptoms, dates, and appointment scheduling language.",
            noise_reduction="near_field",
        ),
    )

    tts = OpenAITTSService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAITTSService.Settings(
            voice="coral",
            instructions="Speak clearly and calmly. There may be literal \\n characters; ignore them when speaking.",
        ),
    )

    n8n = N8NClient()
    triage_session_id = uuid.uuid4().hex[:12]
    vonage_session_id = os.getenv("VONAGE_SESSION_ID", "unknown-session")
    default_patient_name = "Unknown"
    default_patient_phone = ""
    default_doctor_name = _env_text("TRIAGE_DOCTOR_NAME", "Dr. Demo")
    default_doctor_phone = _env_text("TRIAGE_DOCTOR_PHONE", "")
    default_doctor_speciality = _env_text("TRIAGE_DOCTOR_SPECIALITY", "General")

    async def save_triage_transcript(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        payload = {
            "triage_session_id": triage_session_id,
            "patient_name": args.get("patient_name") or default_patient_name,
            "phone": args.get("phone") or default_patient_phone,
            "entry": {
                "question": args.get("question", ""),
                "answer": args.get("answer", ""),
            },
        }

        _append_transcript_line(triage_session_id, payload)
        result = await n8n.save_transcript_entry(payload)
        await params.result_callback(result)

    async def lookup_appointment_availability(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        payload = {
            "triage_session_id": triage_session_id,
            "patient_name": args.get("patient_name") or default_patient_name,
            "phone": args.get("phone") or default_patient_phone,
            "preferred_date": args.get("preferred_date", ""),
            "transcript": args.get("symptoms_summary", ""),
        }
        result = await n8n.lookup_availability(payload)
        await params.result_callback(result)

    async def schedule_appointment_and_notify(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        patient_name = args.get("patient_name") or default_patient_name
        phone = args.get("phone") or default_patient_phone
        raw_date_value = args.get("date")
        raw_time_value = args.get("time")
        date_value = (
            raw_date_value.strip()
            if isinstance(raw_date_value, str) and raw_date_value.strip()
            else "next-business-day"
        )
        time_value = (
            raw_time_value.strip()
            if isinstance(raw_time_value, str) and raw_time_value.strip()
            else "10:30 AM"
        )
        symptoms_summary = args.get("symptoms_summary", "")
        phone_confirmed = _parse_bool(args.get("phone_confirmed", False))

        if not phone_confirmed:
            await params.result_callback(
                {
                    "success": False,
                    "requires_phone_confirmation": True,
                    "message": "Phone number must be explicitly confirmed by the patient before scheduling.",
                }
            )
            return

        requested_datetime = _parse_appointment_datetime(date_value, time_value)
        if not requested_datetime:
            await params.result_callback(
                {
                    "success": False,
                    "requires_new_datetime": True,
                    "message": (
                        "Invalid date/time format. Use a real future date and time, "
                        "for example date=YYYY-MM-DD and time like 10:30 AM."
                    ),
                }
            )
            return

        now_local = datetime.now()
        min_booking_buffer = timedelta(minutes=5)
        if requested_datetime < now_local + min_booking_buffer:
            await params.result_callback(
                {
                    "success": False,
                    "requires_new_datetime": True,
                    "message": "Appointment time must be at least 5 minutes in the future. Please provide a valid future date and time.",
                    "now": now_local.strftime("%Y-%m-%d %I:%M %p"),
                    "requested": requested_datetime.strftime("%Y-%m-%d %I:%M %p"),
                }
            )
            return

        schedule_result = await n8n.schedule_appointment(
            {
                "triage_session_id": triage_session_id,
                "patient_name": patient_name,
                "phone": phone,
                "date": date_value,
                "time": time_value,
                "transcript": symptoms_summary,
            }
        )

        appointment_id = schedule_result.get("appointment_id", f"APT-{triage_session_id.upper()}")
        appointment_date = schedule_result.get("date", date_value)
        appointment_time = schedule_result.get("time", time_value)
        doctor_join_url = _doctor_join_url(vonage_session_id)

        sms_message = (
            f"Appointment confirmed for {patient_name}. "
            f"ID: {appointment_id}. Date: {appointment_date}. Time: {appointment_time}."
        )

        sms_result = await n8n.send_sms(
            {
                "triage_session_id": triage_session_id,
                "phone": phone,
                "message": sms_message,
                "appointment_id": appointment_id,
                "date": appointment_date,
                "time": appointment_time,
            }
        )
        sms_sent = bool(sms_result.get("sent") or sms_result.get("success")) and not bool(
            sms_result.get("fallback")
        )
        if not sms_sent:
            fallback_sms_result = await _send_sms_direct(phone, sms_message)
            if fallback_sms_result.get("sent"):
                sms_result = fallback_sms_result
                sms_sent = True

        doctor_notify_result = await n8n.notify_doctor(
            {
                "triage_session_id": triage_session_id,
                "patient_name": patient_name,
                "phone": phone,
                "doctor_name": args.get("doctor_name") or default_doctor_name,
                "doctor_phone": args.get("doctor_phone") or default_doctor_phone,
                "doctor_speciality": args.get("doctor_speciality") or default_doctor_speciality,
                "appointment_id": appointment_id,
                "date": appointment_date,
                "time": appointment_time,
                "doctor_join_url": doctor_join_url,
            }
        )

        doctor_phone = args.get("doctor_phone") or default_doctor_phone
        doctor_sms_message = (
            f"New triage appointment: {patient_name}, {appointment_date} {appointment_time}. "
            f"Join URL: {doctor_join_url}"
        )
        doctor_sms_result: dict[str, Any] = {
            "success": False,
            "sent": False,
            "skipped": not bool(doctor_phone),
            "reason": "Doctor phone not configured" if not doctor_phone else "",
        }
        doctor_sms_sent = False
        if doctor_phone:
            doctor_sms_result = await n8n.send_sms(
                {
                    "triage_session_id": triage_session_id,
                    "phone": doctor_phone,
                    "message": doctor_sms_message,
                    "appointment_id": appointment_id,
                    "date": appointment_date,
                    "time": appointment_time,
                }
            )
            doctor_sms_sent = bool(
                doctor_sms_result.get("sent") or doctor_sms_result.get("success")
            ) and not bool(doctor_sms_result.get("fallback"))

            if not doctor_sms_sent:
                fallback_doctor_sms_result = await _send_sms_direct(
                    doctor_phone, doctor_sms_message
                )
                if fallback_doctor_sms_result.get("sent"):
                    doctor_sms_result = fallback_doctor_sms_result
                    doctor_sms_sent = True

        doctor_notified = bool(
            doctor_notify_result.get("notified") or doctor_notify_result.get("success")
        ) and not bool(doctor_notify_result.get("fallback"))
        if doctor_phone:
            doctor_notified = doctor_notified and doctor_sms_sent

        if not sms_sent:
            logger.warning(f"SMS not confirmed as sent. Response: {sms_result}")
        if not doctor_notified:
            logger.warning(f"Doctor notification not confirmed. Response: {doctor_notify_result}")
            if doctor_phone:
                logger.warning(f"Doctor SMS not confirmed as sent. Response: {doctor_sms_result}")

        await params.result_callback(
            {
                "success": True,
                "triage_session_id": triage_session_id,
                "appointment": {
                    "appointment_id": appointment_id,
                    "date": appointment_date,
                    "time": appointment_time,
                },
                "sms": sms_result,
                "sms_sent": sms_sent,
                "doctor_notification": doctor_notify_result,
                "doctor_sms": doctor_sms_result,
                "doctor_notified": doctor_notified,
                "doctor_join_url": doctor_join_url,
            }
        )

    async def end_call(params: FunctionCallParams) -> None:
        await params.result_callback({"success": True})
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)

    llm.register_function("save_triage_transcript", save_triage_transcript)
    llm.register_function("lookup_appointment_availability", lookup_appointment_availability)
    llm.register_function("schedule_appointment_and_notify", schedule_appointment_and_notify)
    llm.register_function("end_call", end_call)

    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="save_triage_transcript",
                description="Save a transcript entry in N8N when the caller answers a triage question.",
                properties={
                    "patient_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
                required=["patient_name", "phone", "question", "answer"],
            ),
            FunctionSchema(
                name="lookup_appointment_availability",
                description="Look up appointment slots in N8N after collecting triage summary and preferred date.",
                properties={
                    "patient_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "preferred_date": {"type": "string"},
                    "symptoms_summary": {"type": "string"},
                },
                required=["patient_name", "phone", "preferred_date", "symptoms_summary"],
            ),
            FunctionSchema(
                name="schedule_appointment_and_notify",
                description=(
                    "Schedule appointment in N8N, then send SMS to patient and send doctor join URL. "
                    "Call this only after patient confirms date/time and phone number. "
                    "Do not call this for past times."
                ),
                properties={
                    "patient_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "phone_confirmed": {"type": "boolean"},
                    "date": {"type": "string"},
                    "time": {"type": "string"},
                    "symptoms_summary": {"type": "string"},
                },
                required=[
                    "patient_name",
                    "phone",
                    "phone_confirmed",
                    "date",
                    "time",
                    "symptoms_summary",
                ],
            ),
            FunctionSchema(
                name="end_call",
                description="End the call once appointment and notifications are completed or user says goodbye.",
                properties={"reason": {"type": "string"}},
                required=["reason"],
            ),
        ]
    )

    today = date.today().isoformat()
    messages = [
        {
            "role": "system",
            "content": (
                "You are an AI voice triage nurse for a clinic. Keep responses short and spoken-friendly. "
                f"Today's date is {today}. "
                "Collect patient name, phone (with country code), symptoms summary, and preferred date. "
                "Ask one question at a time. "
                "When patient gives a phone number, repeat the number digit-by-digit and ask for explicit confirmation. "
                "If phone number is not confirmed, ask for phone number again until confirmed. "
                "Do not proceed to scheduling unless phone is confirmed. "
                "After each triage answer, call save_triage_transcript. "
                "When enough triage context is collected, call lookup_appointment_availability and read available slots. "
                "Appointments can be booked for any future date/time, including later today, not only tomorrow or next business day. "
                "Never schedule an appointment for a past date/time. If requested time is in the past, ask for a future date/time. "
                "Once patient confirms date and time, call schedule_appointment_and_notify. "
                "When calling schedule_appointment_and_notify, set phone_confirmed=true only after explicit patient confirmation. "
                "After schedule_appointment_and_notify, read appointment confirmation. "
                "Only say SMS and doctor notification were sent if sms_sent=true and doctor_notified=true in tool result; "
                "otherwise clearly say notifications are pending and should be retried. "
                "Finally call end_call."
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
        logger.info("Vonage Audio Connector connected. Starting triage...")
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Vonage Audio Connector disconnected. Ending call.")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments) -> None:
    """
    Entry point for the FastAPI /ws endpoint.
    Vonage Audio Connector will connect as the WebSocket client.
    """
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
