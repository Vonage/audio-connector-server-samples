#!/usr/bin/env python3
"""
Use a Vonage Video API existing session, generate a token,
,connect its audio to your WebSocket endpoint and send back the received audio.
"""

import asyncio

from audio_connector_sdk.vonage_video.models.audio_connector import (
    AudioConnectorServerConfig,
    AudioConnectorServerHandle,
)
from audio_connector_sdk.vonage_video import Video
import helper
from helper import VonageAudioEchoApp
from vonage_sdk import VonageSDK
from multiprocessing import Process


# ---- main -------------------------------------------------------------------


async def main() -> None:
    echo_app = VonageAudioEchoApp()
    audio_connector_server = await start_server(echo_app)
    try:
        args = helper.read_env_variable()

        if args.application_id and args.private_key:
            vonage = VonageSDK(args)
            vonage.generate_tokens()
            Process(target=vonage.start_audio_connector).start()
        else:
            raise SystemExit("Error: Missing authentication.")

        # Wait for user input or interruption
        print(
            "\nSuccess! Your Video session should now stream audio to/from:",
            args.ws_uri,
        )
        print(
            "The server will only echo back audio after detecting that speech has ended."
        )
        print("Press Enter to stop the echo server...\n")
        await asyncio.to_thread(input)

    except KeyboardInterrupt:
        print("\nShutting down echo server...")
    except Exception as e:
        raise SystemExit(f"Error: {e}")
    finally:
        # Clean up resources
        echo_app.cleanup()
        await audio_connector_server.stop()


# ---- server -------------------------------------------------------------------
async def start_server(echo_app) -> AudioConnectorServerHandle:
    video_api = Video()
    # Define the server configuration
    config = AudioConnectorServerConfig(
        host="127.0.0.1",
        port=8765,
        on_start=echo_app.on_start,
        on_connect=echo_app.on_connect,
        on_stop=echo_app.on_stop,
    )
    # Start the audio connector server
    return await video_api.start_audio_connector_server(config)


if __name__ == "__main__":
    asyncio.run(main())
