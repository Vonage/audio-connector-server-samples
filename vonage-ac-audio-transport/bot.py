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
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.runner.types import RunnerArguments
from pipecat.serializers.vonage import VonageAudioTransport, VonageFrameSerializer
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

AUDIO_OUT_SAMPLE_RATE: int = 16_000

SYSTEM_INSTRUCTION = (
    "You are a friendly assistant. "
    "Your responses will be read aloud, so keep them concise and conversational. "
    "Avoid special characters or formatting."
)

INITIAL_MESSAGE = (
    "Say exactly this greeting once, then continue the conversation normally: "
    "Hello! This is an automated call from our Vonage audio transport demo."
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
    realtime = OpenAIRealtimeLLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAIRealtimeLLMService.Settings(
            model=os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2"),
            system_instruction=SYSTEM_INSTRUCTION,
        ),
    )

    context = LLMContext([{"role": "developer", "content": INITIAL_MESSAGE}])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
        [
            transport.input(),
            user_aggregator,
            realtime,
            transport.output(),
            assistant_aggregator,
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
        await task.queue_frames([LLMRunFrame()])

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
