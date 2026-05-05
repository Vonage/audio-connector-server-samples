#
# SPDX-License-Identifier: BSD-2-Clause
#

import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.runner.types import WebSocketRunnerArguments
from pipecat.serializers.vonage import VonageFrameSerializer

# OpenAI Realtime (speech↔speech)
from pipecat.services.openai_realtime_beta.context import OpenAIRealtimeLLMContext
from pipecat.services.openai_realtime_beta.openai import OpenAIRealtimeBetaLLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

# Telephony-friendly output
AUDIO_OUT_SAMPLE_RATE: int = 16_000

# 640 bytes = 20ms @ 16kHz, PCM16 mono
VONAGE_AUDIO_PACKET_BYTES: int = 640

SYSTEM_INSTRUCTION = (
    "You are a friendly assistant. "
    "Your responses will be read aloud, so keep them concise and conversational. "
    "Avoid special characters or formatting. "
    "Always respond in ENGLISH only. "
    "Begin by saying: Hello! This is an automated call from our Vonage chatbot demo."
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
        model=os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview-2025-06-03"),
        send_transcription_frames=False,
    )

    # Seed system context
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    context = OpenAIRealtimeLLMContext(messages)
    context_agg = realtime.create_context_aggregator(context)

    pipeline = Pipeline(
        [
            transport.input(),
            context_agg.user(),  # send system prompt once
            realtime,  # speech in → speech out
            transport.output(),
            context_agg.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=sample_rate,
            audio_out_sample_rate=AUDIO_OUT_SAMPLE_RATE,  # must be 16000 for 640-byte packets
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info("Vonage Audio Connector connected. Sending system instruction...")
        await task.queue_frames([context_agg.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Vonage Audio Connector disconnected. Ending call.")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


async def bot(runner_args: WebSocketRunnerArguments):
    """
    Called from server.py websocket endpoint:
      runner_args = WebSocketRunnerArguments(websocket=websocket, body={})
      await bot(runner_args)
    """
    # Must match audioRate used in /connect (connect_audio_to_websocket)
    sample_rate = _env_int("VONAGE_AUDIO_RATE", 16000)

    serializer = VonageFrameSerializer(
        VonageFrameSerializer.InputParams(
            vonage_sample_rate=sample_rate,
            sample_rate=None,  # use frame.audio_in_sample_rate
        )
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            fixed_audio_packet_size=VONAGE_AUDIO_PACKET_BYTES,  # 640 bytes (20ms @ 16k)
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint, sample_rate)
