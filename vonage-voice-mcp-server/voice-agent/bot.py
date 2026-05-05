#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Vonage Voice API MCP-only tool server agent.

A voice agent that can:
- Answer questions from knowledge resources served by MCP tool server
- Book/cancel appointments via an external MCP tool server
- Create support tickets via MCP tool server
"""

import os
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
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
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

# Pipeline/TTS run at 24 kHz. Vonage supports 8k/16k on Voice WebSocket; serializer resamples.
AUDIO_OUT_SAMPLE_RATE: int = 24_000
VONAGE_AUDIO_PACKET_BYTES: int = 640

MAX_BOOKING_DAYS: int = 30

MESSAGE_INVALID_OR_PAST_DATE: str = "Invalid or past date. Please choose a date from today onwards."
MESSAGE_BEYOND_MONTH: str = (
    "We can only book appointments up to one month from today. "
    "Please choose a date within the next month."
)

_vad_analyzer: SileroVADAnalyzer | None = None
_vad_warmup_lock = threading.Lock()

load_dotenv(override=True)


def warmup() -> None:
    """Load heavy models at server startup for fast first call."""
    global _vad_analyzer
    if _vad_analyzer is not None:
        return

    with _vad_warmup_lock:
        if _vad_analyzer is None:
            logger.info("Warming up: loading Silero VAD...")
            _vad_analyzer = SileroVADAnalyzer()
            logger.info("Silero VAD ready.")


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        logger.warning(f"{name} is not an int: {v!r}, using default {default}")
        return default


def _parse_date(date_str: str) -> date | None:
    """Parse date string (today, tomorrow, or YYYY-MM-DD)."""
    d = (date_str or "").lower().strip()
    today = date.today()
    if d in ("today", "now"):
        return today
    if d == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def _booking_window_ok(appt_date: date) -> Optional[str]:
    today = date.today()
    if appt_date < today:
        return MESSAGE_INVALID_OR_PAST_DATE
    cutoff = today + timedelta(days=MAX_BOOKING_DAYS)
    if appt_date > cutoff:
        return MESSAGE_BEYOND_MONTH
    return None


def _mcp_base_url() -> str:
    base = os.getenv("MCP_BASE_URL", "http://127.0.0.1:8010")
    return base.rstrip("/")


class MCPTimeoutError(Exception):
    """Raised when an MCP HTTP request times out."""


async def _mcp_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{_mcp_base_url()}{path}"
    default_timeout = 10.0
    raw_timeout = os.getenv("MCP_HTTP_TIMEOUT")
    timeout = default_timeout
    if raw_timeout is not None:
        try:
            parsed_timeout = float(raw_timeout)
            if parsed_timeout > 0:
                timeout = parsed_timeout
            else:
                logger.warning(
                    "MCP_HTTP_TIMEOUT must be > 0; got {value!r}. Using default {default}s.",
                    value=raw_timeout,
                    default=default_timeout,
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid MCP_HTTP_TIMEOUT value {value!r}. Using default {default}s.",
                value=raw_timeout,
                default=default_timeout,
            )
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error(
                "MCP POST timeout after {timeout}s for {url}: {error}",
                timeout=timeout,
                url=url,
                error=exc,
            )
            raise MCPTimeoutError(f"MCP request timed out after {timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code is not None and 400 <= status_code < 500:
                logger.error(
                    "MCP POST client error {status} for {url}: {error}",
                    status=status_code,
                    url=url,
                    error=exc,
                )
            elif status_code is not None and 500 <= status_code < 600:
                logger.error(
                    "MCP POST server error {status} for {url}: {error}",
                    status=status_code,
                    url=url,
                    error=exc,
                )
            else:
                logger.error(
                    "MCP POST HTTP error for {url}: {error}",
                    url=url,
                    error=exc,
                )
            raise
        return r.json()


# -------------------------
# Knowledge tool via MCP server
# -------------------------
async def retrieve_kb(params: FunctionCallParams) -> None:
    """Retrieve relevant KB snippets for a user query via MCP tool server."""
    args = params.arguments or {}
    query = (args.get("query") or "").strip()
    k_raw = args.get("k")
    try:
        k = int(k_raw) if k_raw is not None else 4
    except (TypeError, ValueError):
        logger.warning(f"Invalid 'k' value: {k_raw!r}, defaulting to 4")
        k = 4

    if not query:
        await params.result_callback({"snippets": [], "message": "Empty query."})
        return

    try:
        data = await _mcp_post("/tools/kb/search", {"query": query, "k": k})
        await params.result_callback(data)
    except MCPTimeoutError as e:
        logger.warning("MCP KB search timeout: {}", e)
        await params.result_callback(
            {
                "success": False,
                "snippets": [],
                "message": "Knowledge service timed out. Please try again in a moment.",
            }
        )
    except Exception as e:
        logger.exception("MCP KB search error: {}", e)
        await params.result_callback(
            {"success": False, "snippets": [], "message": "Failed to retrieve KB."}
        )


# -------------------------
# MCP calendar tools
# -------------------------
async def mcp_find_slots(params: FunctionCallParams) -> None:
    args = params.arguments or {}
    date_str = args.get("date", "today")
    tz = args.get("timezone", "Asia/Kolkata")
    raw_duration = args.get("duration_mins", 30)
    try:
        duration_mins = int(raw_duration)
    except (TypeError, ValueError):
        await params.result_callback(
            {
                "success": False,
                "available_slots": [],
                "message": "Invalid duration. Please provide the appointment length in whole minutes.",
            }
        )
        return

    appt_date = _parse_date(date_str)
    if not appt_date:
        await params.result_callback(
            {"success": False, "available_slots": [], "message": "Invalid date."}
        )
        return

    msg = _booking_window_ok(appt_date)
    if msg:
        await params.result_callback({"success": False, "available_slots": [], "message": msg})
        return

    payload = {
        "date": appt_date.isoformat(),
        "timezone": tz,
        "duration_mins": duration_mins,
    }

    try:
        data = await _mcp_post("/tools/calendar/find_slots", payload)
        await params.result_callback(data)
    except MCPTimeoutError as e:
        logger.warning("MCP find_slots timeout: {}", e)
        await params.result_callback(
            {
                "success": False,
                "available_slots": [],
                "message": "Scheduling service timed out. Please try again shortly.",
            }
        )
    except Exception as e:
        logger.exception("MCP find_slots error: {}", e)
        await params.result_callback(
            {
                "success": False,
                "available_slots": [],
                "message": "Could not reach scheduling system right now. Please try again later.",
            }
        )


async def mcp_book_appointment(params: FunctionCallParams) -> None:
    args = params.arguments or {}
    name = (args.get("name") or "Caller").strip()
    date_str = args.get("date", "today")
    time_slot = (args.get("time_slot") or "").strip()
    reason = (args.get("reason") or "General consultation").strip()
    phone = (args.get("phone") or "").strip()
    tz = args.get("timezone", "Asia/Kolkata")

    appt_date = _parse_date(date_str)
    if not appt_date:
        await params.result_callback({"success": False, "message": "Invalid date."})
        return

    msg = _booking_window_ok(appt_date)
    if msg:
        await params.result_callback({"success": False, "message": msg})
        return

    payload = {
        "name": name,
        "date": appt_date.isoformat(),
        "time_slot": time_slot,
        "reason": reason,
        "phone": phone,
        "timezone": tz,
    }

    try:
        data = await _mcp_post("/tools/calendar/book", payload)
        await params.result_callback(data)
    except MCPTimeoutError as e:
        logger.warning("MCP book timeout: {}", e)
        await params.result_callback(
            {
                "success": False,
                "message": "Booking service timed out. Please try again in a moment.",
            }
        )
    except Exception as e:
        logger.exception("MCP book error: {}", e)
        await params.result_callback(
            {"success": False, "message": "Booking failed due to a system error."}
        )


async def mcp_cancel_appointment(params: FunctionCallParams) -> None:
    args = params.arguments or {}
    booking_id = (args.get("booking_id") or "").strip()
    name = (args.get("name") or "").strip()
    date_str = args.get("date", "")

    payload = {"booking_id": booking_id, "name": name, "date": date_str}

    try:
        data = await _mcp_post("/tools/calendar/cancel", payload)
        await params.result_callback(data)
    except MCPTimeoutError as e:
        logger.warning("MCP cancel timeout: {}", e)
        await params.result_callback(
            {
                "success": False,
                "message": "Cancellation service timed out. Please try again shortly.",
            }
        )
    except Exception as e:
        logger.exception("MCP cancel error: {}", e)
        await params.result_callback(
            {"success": False, "message": "Cancellation failed due to a system error."}
        )


# -------------------------
# MCP ticket tool
# -------------------------
async def mcp_create_ticket(params: FunctionCallParams) -> None:
    args = params.arguments or {}
    caller_name = (args.get("caller_name") or "Caller").strip()
    phone = (args.get("phone") or "").strip()
    summary = (args.get("summary") or "").strip()
    transcript = (args.get("transcript") or "").strip()
    priority = (args.get("priority") or "normal").strip()

    if not summary:
        await params.result_callback({"success": False, "message": "Missing ticket summary."})
        return

    payload = {
        "caller_name": caller_name,
        "phone": phone,
        "summary": summary,
        "transcript": transcript,
        "priority": priority,
    }

    try:
        data = await _mcp_post("/tools/ticket/create", payload)
        await params.result_callback(data)
    except MCPTimeoutError as e:
        logger.warning("MCP ticket timeout: {}", e)
        await params.result_callback(
            {
                "success": False,
                "message": "Ticket service timed out. Please try again in a moment.",
            }
        )
    except Exception as e:
        logger.exception("MCP ticket error: {}", e)
        await params.result_callback(
            {"success": False, "message": "Could not create a ticket right now."}
        )


# -------------------------
# End call tool
# -------------------------
async def end_call(params: FunctionCallParams) -> None:
    await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)


async def run_bot(transport: BaseTransport, handle_sigint: bool, sample_rate: int) -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "Missing OPENAI_API_KEY; cannot initialize voice agent. "
            "Set OPENAI_API_KEY before accepting calls."
        )

    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))

    # Register tools
    llm.register_function("retrieve_kb", retrieve_kb)
    llm.register_function("mcp_find_slots", mcp_find_slots)
    llm.register_function("mcp_book_appointment", mcp_book_appointment)
    llm.register_function("mcp_cancel_appointment", mcp_cancel_appointment)
    llm.register_function("mcp_create_ticket", mcp_create_ticket)
    llm.register_function("end_call", end_call)

    # Tool schemas
    retrieve_kb_fn = FunctionSchema(
        name="retrieve_kb",
        description=(
            "Retrieve relevant knowledge snippets from the knowledge service (MCP tool server). "
            "Call this before answering policy/pricing/address/FAQ/troubleshooting questions. "
            "Use returned snippets to answer, and do not invent facts if snippets are empty."
        ),
        properties={
            "query": {"type": "string", "description": "User's question in natural language."},
            "k": {"type": "integer", "description": "Number of snippets to retrieve (default 4)."},
        },
        required=["query"],
    )

    mcp_find_slots_fn = FunctionSchema(
        name="mcp_find_slots",
        description=(
            "Find available appointment slots on a date by calling the scheduling system (MCP tools). "
            "Call this first when user wants to book or asks availability."
        ),
        properties={
            "date": {"type": "string", "description": "Date: today/tomorrow or YYYY-MM-DD."},
            "timezone": {"type": "string", "description": "IANA timezone (default Asia/Kolkata)."},
            "duration_mins": {
                "type": "integer",
                "description": "Appointment duration in minutes (default 30).",
            },
        },
        required=["date"],
    )

    mcp_book_fn = FunctionSchema(
        name="mcp_book_appointment",
        description=(
            "Book an appointment using the scheduling system (MCP tools). "
            "Only call AFTER mcp_find_slots confirms the time_slot is available."
        ),
        properties={
            "name": {"type": "string", "description": "Caller's name."},
            "phone": {"type": "string", "description": "Caller's phone (if known)."},
            "date": {"type": "string", "description": "Date: today/tomorrow or YYYY-MM-DD."},
            "time_slot": {
                "type": "string",
                "description": "Time slot string, must match available slot.",
            },
            "reason": {"type": "string", "description": "Reason for appointment."},
            "timezone": {"type": "string", "description": "IANA timezone (default Asia/Kolkata)."},
        },
        required=["name", "date", "time_slot", "reason"],
    )

    mcp_cancel_fn = FunctionSchema(
        name="mcp_cancel_appointment",
        description=(
            "Cancel an appointment using the scheduling system (MCP tools). "
            "Prefer booking_id if available; otherwise use name + date."
        ),
        properties={
            "booking_id": {"type": "string", "description": "Booking ID if known."},
            "name": {"type": "string", "description": "Caller name (if booking_id not known)."},
            "date": {"type": "string", "description": "Date YYYY-MM-DD (if booking_id not known)."},
        },
        required=[],
    )

    mcp_ticket_fn = FunctionSchema(
        name="mcp_create_ticket",
        description=(
            "Create a support ticket via the ticket system (MCP tools). "
            "Use when KB does not have the answer or user requests human follow-up."
        ),
        properties={
            "caller_name": {"type": "string", "description": "Caller's name."},
            "phone": {"type": "string", "description": "Caller's phone if available."},
            "summary": {"type": "string", "description": "Short summary of the issue/request."},
            "transcript": {
                "type": "string",
                "description": "Short transcript or notes of conversation.",
            },
            "priority": {"type": "string", "description": "low/normal/high."},
        },
        required=["summary"],
    )

    end_call_fn = FunctionSchema(
        name="end_call",
        description="End the call when the caller is done or says goodbye.",
        properties={"reason": {"type": "string", "description": "Reason for ending the call."}},
        required=["reason"],
    )

    tools = ToolsSchema(
        standard_tools=[
            retrieve_kb_fn,
            mcp_find_slots_fn,
            mcp_book_fn,
            mcp_cancel_fn,
            mcp_ticket_fn,
            end_call_fn,
        ]
    )

    stt = OpenAISTTService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-transcribe",
        prompt="Expect appointment booking, availability, cancellations, pricing, policies, FAQs, and troubleshooting.",
    )

    tts = OpenAITTSService(
        api_key=os.getenv("OPENAI_API_KEY"),
        voice="coral",
        instructions="There may be literal '\\n' characters; ignore them when speaking.",
    )

    today = date.today()
    tomorrow = today + timedelta(days=1)
    today_str = today.strftime("%A, %B %d, %Y")
    tomorrow_str = tomorrow.strftime("%A, %B %d, %Y")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a friendly voice assistant for a service desk that can answer questions and book appointments. "
                "Your responses will be read aloud, so keep them concise and conversational. Avoid special characters or formatting. "
                f"Today's date is {today_str}. Tomorrow is {tomorrow_str}. "
                "Appointments can be booked from today up to one month ahead. "
                "Key rules:\n"
                "1) If the caller asks about policies, pricing, address, hours, FAQs, or troubleshooting, CALL retrieve_kb first and answer using the snippets. "
                "Do not invent facts. If snippets are empty, say you don't have that info and offer to create a support ticket.\n"
                "2) If the caller wants to check availability, CALL mcp_find_slots.\n"
                "3) If the caller wants to book a time, first CALL mcp_find_slots for that date. "
                "If the requested time is not available, clearly say it's unavailable and offer alternatives. "
                "Only ask for name and reason after a slot is confirmed available. "
                "Then CALL mcp_book_appointment.\n"
                "4) If the caller wants to cancel, CALL mcp_cancel_appointment. "
                "If booking_id is unknown, ask for date and name.\n"
                "5) If the caller wants human follow-up or you cannot answer, CALL mcp_create_ticket.\n"
                "6) When the caller is done, CALL end_call.\n"
                "Begin by saying: Hello! Thanks for calling. How can I help you today?"
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
        logger.info("Agent connected. Waiting for caller...")
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Caller disconnected. Ending call.")
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

    if _vad_analyzer is None:
        warmup()

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            fixed_audio_packet_size=VONAGE_AUDIO_PACKET_BYTES,
            vad_analyzer=_vad_analyzer,
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint, sample_rate)
