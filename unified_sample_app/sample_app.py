#!/usr/bin/env python3
"""
Use a Vonage Video API existing session, generate a token,
and connect its audio to your WebSocket endpoint.
"""

import asyncio

from audio_connector_sdk.vonage_video.models.audio_connector import (
    AudioConnectorServerConfig,
)
from audio_connector_sdk.vonage_video import Video
import helper
from vonage_sdk import VonageSDK
from opentok_sdk import OpentokSDK
from multiprocessing import Process


HOST = "127.0.0.1"
PORT = 8765


# ---- main -------------------------------------------------------------------


async def main() -> None:
    helper.initialize_stream()
    await start_server()
    args = helper.read_env_variable()
    try:
        if args.application_id and args.private_key:
            # Use Vonage auth
            vonage = VonageSDK(args)
            vonage.generate_tokens()
            Process(target=vonage.start_audio_connector).start()
        elif args.api_key and args.api_secret:
            opentok = OpentokSDK(args)
            opentok.generate_tokens()
            Process(target=opentok.start_audio_connector).start()
        else:
            print("Warning: starting only the websocket server.")
    except Exception as e:
        raise SystemExit(f"Error: {e}")


    ws_uri = args.ws_uri or f"ws://{HOST}:{PORT}"
    print(f"\nSuccess! Using WebSocket URI: {ws_uri}")

    await asyncio.Event().wait()


# ---- server -------------------------------------------------------------------
async def start_server():
    video_api = Video()
    # Define the server configuration
    config = AudioConnectorServerConfig(
        host=HOST,
        port=PORT,
        on_start=helper.on_start,
        on_connect=helper.on_connect,
    )
    # Start the audio connector server
    await video_api.start_audio_connector_server(config)


if __name__ == "__main__":
    asyncio.run(main())
