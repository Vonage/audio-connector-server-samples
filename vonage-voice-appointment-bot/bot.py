#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Vonage Voice API appointment booking agent.

A voice agent that helps callers check availability, book appointments,
and cancel appointments over the phone using Pipecat and Vonage Voice API.
"""

import os
from datetime import date, timedelta
from typing import Any

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
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

# Pipeline/TTS run at 24 kHz for normal speed and quality. VonageFrameSerializer resamples
# output to vonage_sample_rate (16 kHz) for the Voice WebSocket (Vonage supports 8k/16k only).
AUDIO_OUT_SAMPLE_RATE: int = 24_000
VONAGE_AUDIO_PACKET_BYTES: int = 640

# Available time slots (demo)
AVAILABLE_SLOTS = ["9:00 AM", "10:00 AM", "11:00 AM", "2:00 PM", "3:00 PM", "4:00 PM"]

# Bookings allowed only from today up to this many days ahead
MAX_BOOKING_DAYS: int = 30

MESSAGE_INVALID_OR_PAST_DATE: str = "Invalid or past date. Please choose today or a future date."
MESSAGE_BEYOND_MONTH: str = (
    "We can only book appointments up to one month from today. "
    "Please choose a date within the next month."
)

# In-memory appointment storage (demo - resets on restart)
_appointments: dict[str, dict[str, Any]] = {}

# Shared VAD instance, created at warmup so first call is not delayed by model load
_vad_analyzer: SileroVADAnalyzer | None = None

load_dotenv(override=True)


def warmup() -> None:
    """Load heavy models at server startup so the first WebSocket connection is fast."""
    global _vad_analyzer
    if _vad_analyzer is None:
        logger.info("Warming up bot: loading Silero VAD...")
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
    """Parse date string (e.g. today, tomorrow, or YYYY-MM-DD)."""
    d = date_str.lower().strip()
    today = date.today()
    if d in ("today", "now"):
        return today
    if d == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def _appointment_key(name: str, appt_date: date) -> str:
    """Unique key per (name, date). Intentionally one appointment per person per day in this simple bot."""
    return f"{name}|{appt_date.isoformat()}"


async def check_availability(params: FunctionCallParams) -> None:
    """Check available appointment slots for a given date."""
    args = params.arguments or {}
    date_str = args.get("date", "today")
    appt_date = _parse_date(date_str)
    if not appt_date or appt_date < date.today():
        await params.result_callback(
            {
                "available_slots": [],
                "message": MESSAGE_INVALID_OR_PAST_DATE,
            }
        )
        return
    cutoff = date.today() + timedelta(days=MAX_BOOKING_DAYS)
    if appt_date > cutoff:
        await params.result_callback(
            {
                "available_slots": [],
                "message": MESSAGE_BEYOND_MONTH,
            }
        )
        return

    # Get already booked slots for that date
    booked = {
        a["time_slot"]
        for a in _appointments.values()
        if _parse_date(a.get("date", "")) == appt_date
    }
    available = [s for s in AVAILABLE_SLOTS if s not in booked]
    await params.result_callback(
        {
            "date": appt_date.isoformat(),
            "available_slots": available,
            "message": f"Available slots for {date_str}: {', '.join(available) if available else 'none'}.",
        }
    )


async def book_appointment(params: FunctionCallParams) -> None:
    """Book an appointment."""
    args = params.arguments or {}
    name = args.get("name", "Caller").strip()
    date_str = args.get("date", "today")
    time_slot = args.get("time_slot", "")
    reason = args.get("reason", "General consultation")

    appt_date = _parse_date(date_str)
    if not appt_date or appt_date < date.today():
        await params.result_callback(
            {
                "success": False,
                "message": MESSAGE_INVALID_OR_PAST_DATE,
            }
        )
        return
    if appt_date > date.today() + timedelta(days=MAX_BOOKING_DAYS):
        await params.result_callback(
            {
                "success": False,
                "message": MESSAGE_BEYOND_MONTH,
            }
        )
        return

    if time_slot not in AVAILABLE_SLOTS:
        await params.result_callback(
            {
                "success": False,
                "message": f"Invalid time slot. Available: {', '.join(AVAILABLE_SLOTS)}.",
            }
        )
        return

    key = _appointment_key(name, appt_date)
    if key in _appointments:
        await params.result_callback(
            {
                "success": False,
                "message": f"You already have an appointment on {appt_date}. Please cancel it first if you'd like to book a different time.",
            }
        )
        return

    # Check if slot is taken by someone else (not atomic with write below; demo in-memory only)
    for a in _appointments.values():
        if _parse_date(a.get("date", "")) == appt_date and a.get("time_slot") == time_slot:
            await params.result_callback(
                {
                    "success": False,
                    "message": f"Sorry, {time_slot} is no longer available. Please choose another slot.",
                }
            )
            return

    _appointments[key] = {
        "name": name,
        "date": appt_date.isoformat(),
        "time_slot": time_slot,
        "reason": reason,
    }
    await params.result_callback(
        {
            "success": True,
            "message": f"Your appointment is confirmed for {appt_date.strftime('%A, %B %d')} at {time_slot}. Reason: {reason}.",
        }
    )


async def cancel_appointment(params: FunctionCallParams) -> None:
    """Cancel an existing appointment."""
    args = params.arguments or {}
    name = args.get("name", "Caller").strip()
    date_str = args.get("date", "today")

    appt_date = _parse_date(date_str)
    if not appt_date:
        await params.result_callback({"success": False, "message": "Invalid date."})
        return

    key = _appointment_key(name, appt_date)
    if key not in _appointments:
        await params.result_callback(
            {"success": False, "message": f"No appointment found for {name} on {date_str}."}
        )
        return

    del _appointments[key]
    await params.result_callback(
        {"success": True, "message": f"Your appointment for {date_str} has been cancelled."}
    )


async def end_call(params: FunctionCallParams) -> None:
    """End the call when the user is done."""
    await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)


async def run_bot(transport: BaseTransport, handle_sigint: bool, sample_rate: int) -> None:
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning(
            "OPENAI_API_KEY is not set; skipping bot initialization (e.g. CI/lint). "
            "Set OPENAI_API_KEY to run the appointment bot."
        )
        return
    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))

    llm.register_function("check_availability", check_availability)
    llm.register_function("book_appointment", book_appointment)
    llm.register_function("cancel_appointment", cancel_appointment)
    llm.register_function("end_call", end_call)

    check_availability_fn = FunctionSchema(
        name="check_availability",
        description="Check available appointment slots for a given date. Dates must be from today up to one month ahead; dates beyond that return a message that we cannot book that far. Call this first when the caller wants to book a specific date/time.",
        properties={
            "date": {
                "type": "string",
                "description": "The date to check. Use 'today', 'tomorrow', or YYYY-MM-DD. Only dates within the next month are bookable.",
            },
        },
        required=["date"],
    )
    book_appointment_fn = FunctionSchema(
        name="book_appointment",
        description="Book an appointment. Only call after check_availability confirms the requested time_slot is in available_slots. Do not call if the slot is not available.",
        properties={
            "name": {"type": "string", "description": "Caller's name."},
            "date": {"type": "string", "description": "Date: 'today', 'tomorrow', or YYYY-MM-DD."},
            "time_slot": {
                "type": "string",
                "enum": AVAILABLE_SLOTS,
                "description": "The time slot. Must be one of the available slots.",
            },
            "reason": {"type": "string", "description": "Reason for the appointment."},
        },
        required=["name", "date", "time_slot", "reason"],
    )
    cancel_appointment_fn = FunctionSchema(
        name="cancel_appointment",
        description="Cancel an existing appointment. Use when the caller wants to cancel.",
        properties={
            "name": {"type": "string", "description": "Caller's name."},
            "date": {"type": "string", "description": "Date of the appointment to cancel."},
        },
        required=["name", "date"],
    )
    end_call_fn = FunctionSchema(
        name="end_call",
        description="End the call when the caller says they are done or goodbye.",
        properties={
            "reason": {"type": "string", "description": "Brief reason for ending the call."},
        },
        required=["reason"],
    )

    tools = ToolsSchema(
        standard_tools=[
            check_availability_fn,
            book_appointment_fn,
            cancel_appointment_fn,
            end_call_fn,
        ]
    )

    stt = OpenAISTTService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-transcribe",
        prompt=(
            "Expect words about scheduling appointments, checking availability, and cancellations."
        ),
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
                "You are a friendly appointment booking assistant. "
                "Your responses will be read aloud, so keep them concise and conversational. "
                "Avoid special characters or formatting. "
                f"Today's date is {today_str}. Tomorrow is {tomorrow_str}. Use these dates when the caller asks what day it is or when they say 'today' or 'tomorrow'. "
                "Appointments can be booked for any date from today up to one month ahead. If the caller asks for a date more than a month from today, say we cannot book that far ahead as of now and ask them to choose a date within the next month. "
                "You can: check available slots, book appointments, and cancel appointments. "
                "Available time slots are: 9:00 AM, 10:00 AM, 11:00 AM, 2:00 PM, 3:00 PM, 4:00 PM. "
                "When the caller is done, call end_call to end the conversation. "
                "Booking flow: When the caller wants to book a specific date and time, first call check_availability for that date. "
                "If the requested time slot is NOT in the returned available_slots, tell them clearly that slot is not available and suggest they choose another time or ask for available slots. Do NOT ask for name or reason when the slot is unavailable. "
                "Only ask for name and reason when the slot is available (i.e. it appears in available_slots). "
                "If book_appointment returns success false (e.g. slot no longer available, invalid slot), simply relay that message and do not ask for name or reason again. "
                "Begin by saying: 'Hello! This is the appointment booking line. How can I help you today?' "
            ),
        },
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
        logger.info("Appointment booking agent connected. Waiting for caller...")
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Caller disconnected. Ending call.")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point for the FastAPI /ws endpoint. Vonage Voice API connects here."""
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
