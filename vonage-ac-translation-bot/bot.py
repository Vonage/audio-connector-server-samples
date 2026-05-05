import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.runner.types import WebSocketRunnerArguments
from pipecat.serializers.vonage import VonageFrameSerializer
from pipecat.services.openai_realtime_beta.context import OpenAIRealtimeLLMContext
from pipecat.services.openai_realtime_beta.openai import OpenAIRealtimeBetaLLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

AUDIO_OUT_SAMPLE_RATE: int = 16_000
VONAGE_AUDIO_PACKET_BYTES: int = 640

load_dotenv(override=True)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"{name} is not an int: {value!r}, using default {default}")
        return default


def _build_system_instruction() -> str:
    source_language = os.getenv("SOURCE_LANGUAGE", "Hindi")
    target_language = os.getenv("TARGET_LANGUAGE", "English")
    return (
        "You are a strict real-time interpreter for a one-way audio relay. "
        f"Translate only from {source_language} to {target_language}. "
        "Rules: "
        f"1) If speaker uses {source_language}, translate to {target_language}. "
        f"2) If speaker uses {target_language}, do not translate and stay silent. "
        f"3) If speaker uses another language, translate to {target_language}. "
        "4) Output only translated speech; never include explanations or labels. "
        "5) Preserve meaning, names, and numbers exactly; do not summarize."
    )


async def run_bot(transport: BaseTransport, handle_sigint: bool, sample_rate: int):
    realtime = OpenAIRealtimeBetaLLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model=os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview-2025-06-03"),
        send_transcription_frames=False,
    )

    messages = [{"role": "system", "content": _build_system_instruction()}]
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

    openai_connection_failed = False

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info("Vonage Audio Connector connected. Sending translation system instruction...")
        await task.queue_frames([context_agg.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Vonage Audio Connector disconnected. Ending call.")
        await task.cancel()

    @task.event_handler("on_pipeline_error")
    async def on_pipeline_error(_task, frame):
        nonlocal openai_connection_failed
        if openai_connection_failed:
            return

        error_text = str(getattr(frame, "error", ""))
        if "Error sending client event" in error_text or "keepalive ping timeout" in error_text:
            openai_connection_failed = True
            logger.error(
                "OpenAI realtime websocket disconnected, cancelling pipeline to avoid error flood"
            )
            await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


async def bot(runner_args: WebSocketRunnerArguments):
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
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint, sample_rate)
