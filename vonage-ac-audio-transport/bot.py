#
# SPDX-License-Identifier: BSD-2-Clause
#

"""
bot.py — Pipecat voice bot using JSON audio transport.

Audio arrives as JSON messages with base64-encoded PCM data instead of
raw binary frames. This bot uses Pipecat's Vonage serializer to decode
inbound base64 audio and encode outbound audio as base64 JSON.
"""

import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.runner.types import RunnerArguments
from pipecat.serializers.vonage import VonageAudioTransport, VonageFrameSerializer
from pipecat.services.openai_realtime_beta.context import OpenAIRealtimeLLMContext
from pipecat.services.openai_realtime_beta.openai import OpenAIRealtimeBetaLLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

AUDIO_OUT_SAMPLE_RATE: int = 16_000

SYSTEM_INSTRUCTION = (
    "You are a friendly assistant. "
    "Your responses will be read aloud, so keep them concise and conversational. "
    "Avoid special characters or formatting. "
    "Begin by saying: Hello! This is an automated call from our Vonage audio transport demo."
)

load_dotenv(override=True)


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

    serializer = VonageFrameSerializer(
        VonageFrameSerializer.InputParams(
            audio_transport=VonageAudioTransport.JSON,
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
            # 20ms packets at 16kHz PCM16 mono.
            audio_out_10ms_chunks=2,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint, sample_rate)
