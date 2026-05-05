import argparse
from dotenv import load_dotenv
import os
import asyncio


stream = None


def initialize_stream():
    import sounddevice as sd

    global stream
    CHANNELS = 1
    SAMPLE_RATE = 16000
    try:
        if stream is None:
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
            )
            stream.start()
    except Exception as e:
        print(f"An error occurred: {e}")
    print("Audio output stream initialized.")


async def play_stream(message):
    import numpy as np

    global stream
    CHANNELS = 1

    try:
        if not isinstance(message, bytes):
            print("message received:", message)
        else:
            audio_data = np.frombuffer(message, dtype=np.int16)
            if audio_data.size % CHANNELS == 0:
                audio_data = audio_data.reshape(-1, CHANNELS)
            stream.write(audio_data)
    except Exception as e:
        print(f"An error occurred: {e}")


async def stream_microphone(connection_obj):
    import sounddevice as sd

    with sd.InputStream(
        samplerate=16000, channels=1, blocksize=1024, dtype="int16"
    ) as stream:
        while True:
            bytes_per_sample = int(16000 * 20 / 1000)  # Assuming 16-bit PCM
            data, _ = stream.read(bytes_per_sample)

            audio_data = data.tobytes()
            try:
                await connection_obj.send_audio_buffer(audio_data)
                # await asyncio.sleep(0.01)  # Sleep for 10ms to yield control
            except Exception as e:
                print(f"Error sending audio buffer: {e}")


async def on_message(message):
    # asyncio.create_task(play_stream(message))
    return message


async def on_disconnect():
    print("Client disconnected")


async def on_error(error):
    print(f"Error occurred: {error}")


async def on_start():
    print("Server started")


async def on_stop():
    print("Server stopped")


async def on_connect(client):
    print(f"Client connected: {client.info()}")
    # Set per-connection handlers
    client.set_handler(
        on_message=on_message, on_disconnect=on_disconnect, on_error=on_error
    )
    asyncio.create_task(stream_microphone(client))


def read_env_variable():
    # Load environment variables from .env file
    load_dotenv(dotenv_path=".env")

    p = argparse.ArgumentParser(
        description="Create a session and connect its audio to a WebSocket."
    )
    # Vonage Auth
    p.add_argument(
        "--application-id", default=os.getenv("APPLICATION_ID"), required=False
    )
    p.add_argument("--private-key", default=os.getenv("PRIVATE_KEY"), required=False)

    # Opentok Auth
    p.add_argument("--api-key", default=os.getenv("API_KEY"), required=False)
    p.add_argument("--api-secret", default=os.getenv("API_SECRET"), required=False)

    # Where to connect
    p.add_argument(
        "--ws-uri", default=os.getenv("WS_URI"), help="wss://...", required=False
    )
    p.add_argument(
        "--audio-rate", type=int, default=int(os.getenv("VONAGE_AUDIO_RATE", "16000"))
    )
    p.add_argument("--bidirectional", action="store_true", default=True)

    # An existing session which needs to be connected to audio connector server
    p.add_argument("--session-id", default=os.getenv("SESSION_ID"))

    # Optional streams and headers (to pass to the WS)
    p.add_argument(
        "--streams",
        default=os.getenv("VONAGE_STREAMS"),
        help="Comma-separated stream IDs",
    )
    p.add_argument(
        "--header",
        action="append",
        help="Extra header(s) for WS, e.g. --header X-Foo=bar (repeatable)",
    )

    # Optional: choose API base.
    p.add_argument("--api-base", default=os.getenv("API_URL"), required=False)
    args = p.parse_args()

    # Validate inputs
    missing = [
        k
        for k, v in {
            "application-id": args.application_id,
            "private-key": args.private_key,
            "ws-uri": args.ws_uri,
            "session-id": args.session_id,
        }.items()
        if not v
    ]
    if args.api_key and args.api_secret:
        missing = [k for k in missing if k not in ["application-id", "private-key"]]

    if missing:
        print(f"Missing args/env: {', '.join(missing)}")

    return args
