#
# SPDX-License-Identifier: BSD-2-Clause
#

"""
bot.py — Pipecat voice bot using JSON audio transport.

Audio arrives as JSON messages with base64-encoded PCM data instead of
raw binary frames. This bot uses a custom serializer to decode inbound
base64 audio and encode outbound audio as base64 JSON.
"""

import base64
import json
import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.runner.types import RunnerArguments
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType
from pipecat.services.openai_realtime_beta.context import OpenAIRealtimeLLMContext
from pipecat.services.openai_realtime_beta.openai import OpenAIRealtimeBetaLLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

AUDIO_OUT_SAMPLE_RATE: int = 16_000

# 640 bytes = 20ms @ 16kHz, PCM16 mono
VONAGE_AUDIO_PACKET_BYTES: int = 640

SYSTEM_INSTRUCTION = (
    "You are a friendly assistant. "
    "Your responses will be read aloud, so keep them concise and conversational. "
    "Avoid special characters or formatting. "
    "Begin by saying: Hello! This is an automated call from our Vonage audio transport demo."
)

load_dotenv(override=True)


class VonageJsonTransportSerializer(FrameSerializer):
    """Serializer for Vonage Audio Connector JSON audio transport.

    Inbound: JSON messages with base64-encoded PCM in the 'audio' field.
    Outbound: PCM audio encoded as base64 wrapped in a JSON message.
    Non-audio JSON events (e.g. websocket:connected) are ignored.
    """

    class InputParams(FrameSerializer.InputParams):
        vonage_sample_rate: int = 16000
        audio_field: str = "audio"

    def __init__(self, params: InputParams = InputParams()):
        super().__init__()
        self._params = params

    @property
    def type(self) -> FrameSerializerType:
        return FrameSerializerType.TEXT

    def serialize(self, frame: Frame) -> str | bytes | None:
        if not isinstance(frame, OutputAudioRawFrame):
            return None
        audio_b64 = base64.b64encode(frame.audio).decode()
        return json.dumps(
            {self._params.audio_field: audio_b64},
            separators=(",", ":"),
        )

    def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            # Fallback: handle raw binary if it arrives
            return InputAudioRawFrame(
                audio=data,
                sample_rate=self._params.vonage_sample_rate,
                num_channels=1,
            )

        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None

        audio_b64 = parsed.get(self._params.audio_field)
        if audio_b64 is None:
            # Non-audio event (e.g. websocket:connected) — skip
            logger.debug(f"Non-audio JSON event: {list(parsed.keys())}")
            return None

        pcm = base64.b64decode(audio_b64)
        return InputAudioRawFrame(
            audio=pcm,
            sample_rate=self._params.vonage_sample_rate,
            num_channels=1,
        )


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        logger.warning(f"{name} is not an int: {v!r}, using default {default}")
        return default


async def run_bot(transport: BaseTransport, handle_sigint: bool, sample_rate: int):
    realtime = OpenAIRealtimeBetaLLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model=os.getenv(
            "OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview-2025-06-03"
        ),
        send_transcription_frames=False,
    )

    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    context = OpenAIRealtimeLLMContext(messages)
    context_agg = realtime.create_context_aggregator(context)

    pipeline = Pipeline(
        [
            transport.input(),
            context_agg.user(),
            realtime,
            transport.output(),
            context_agg.assistant(),
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
        logger.info("Audio Connector connected (JSON transport). Sending prompt...")
        await task.queue_frames([context_agg.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Audio Connector disconnected.")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Entry point called from server.py /ws endpoint."""
    sample_rate = _env_int("VONAGE_AUDIO_RATE", 16000)

    serializer = VonageJsonTransportSerializer(
        VonageJsonTransportSerializer.InputParams(
            vonage_sample_rate=sample_rate,
            audio_field="audio",
        )
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            fixed_audio_packet_size=VONAGE_AUDIO_PACKET_BYTES,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint, sample_rate)
