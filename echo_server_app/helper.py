import argparse
from dotenv import load_dotenv
import os
import asyncio
from webrtcvad import Vad
import queue
from collections import deque
import threading
from typing import Optional


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
        }.items()
        if not v
    ]

    if missing:
        raise SystemExit(f"Missing required args/env: {', '.join(missing)}")

    return args


class VonageAudioEchoApp:
    def __init__(self) -> None:
        # VAD (Voice Activity Detection) related attributes
        self.vad: Vad = Vad(2)  # 0-3, higher = more aggressive
        self.audio_queue: queue.Queue[bytes] = queue.Queue()
        self.audio_thread: Optional[threading.Thread] = None
        self.is_publishing: bool = False
        self.stop_thread: bool = False
        self.speech_frames: deque[bytes] = deque()  # Buffer to store speech frames
        self.is_speech_active: bool = False
        self.silence_frames_count: int = 0
        self.silence_threshold: int = (
            10  # Number of silent frames before considering speech ended
        )

        self.sample_rate: int = 16000  # Default sample rate

    async def on_start(
        self,
    ):
        # Called when the audio connector server starts
        print("server - Audio connector server started.")

    async def on_stop(self):
        print("server - Audio connector server stopped")

    async def on_disconnect(self):
        print("server - Client disconnected")

    async def on_error(self, error):
        print(f"server - Error occurred: {error}")

    async def on_connect(self, client):
        # Set per-connection handlers
        client.set_handler(
            on_message=self.on_message,
            on_disconnect=self.on_disconnect,
            on_error=self.on_error,
        )
        self.on_ready_for_audio(client)

    async def on_message(self, message):
        if not isinstance(message, (bytes, bytearray)):
            print(f"Event: {message}")
        else:
            if not self.is_publishing:
                return
            self.process_audio_with_vad(message)

    def process_audio_with_vad(self, audio_data) -> None:
        """Process incoming audio with VAD and manage speech segments"""
        try:
            # Process VAD
            try:
                len(audio_data)
                is_speech = self.vad.is_speech(audio_data, self.sample_rate)
            except Exception as e:
                print(f"VAD processing error: {e}")
                is_speech = False

            if is_speech:
                # Reset silence counter
                self.silence_frames_count = 0

                # Add current frame to speech buffer
                mem_view = memoryview(audio_data).cast("h")

                self.speech_frames.append(mem_view.tobytes())

                if not self.is_speech_active:
                    self.is_speech_active = True
                    print("Speech detected - starting to buffer audio")

            else:
                # Silence detected
                if self.is_speech_active:
                    self.silence_frames_count += 1

                    # Add silence frame to maintain timing
                    mem_view = memoryview(audio_data).cast("h")
                    self.speech_frames.append(mem_view.tobytes())

                    # Check if we've had enough silence to consider speech ended
                    if self.silence_frames_count >= self.silence_threshold:
                        print(
                            f"Speech ended - queuing {len(self.speech_frames)} frames for echo"
                        )

                        # Queue all buffered speech frames for immediate echo
                        for frame in self.speech_frames:
                            self.audio_queue.put(frame)

                        # Reset for next speech segment
                        self.speech_frames.clear()
                        self.is_speech_active = False
                        self.silence_frames_count = 0

        except Exception as e:
            print(f"Error in VAD processing: {e}")

    def on_ready_for_audio(self, client) -> None:
        self.is_publishing = True

        # Start the echo thread
        self.audio_thread = threading.Thread(
            target=self.audio_echo_thread, args=(client,), daemon=False
        )
        self.audio_thread.start()

    def audio_echo_thread(self, connection_obj) -> None:
        # Run the async loop inside this thread
        try:
            asyncio.run(self._echo_loop(connection_obj))
        except Exception as e:
            print(f"Error in audio echo thread: {e}")

    async def _echo_loop(self, connection_obj) -> None:
        print("Echo thread started - ready to echo audio")
        print(f"Audio echo thread running for connection: {connection_obj.info()}")
        try:
            while not self.stop_thread:
                try:
                    audio_data = self.audio_queue.get(timeout=0.01)
                    try:
                        await connection_obj.send_audio_buffer(audio_data)
                    except Exception as e:
                        print(f"Error sending audio buffer: {e}")
                except queue.Empty:
                    await asyncio.sleep(0.01)
                except Exception as e:
                    print(f"Error injecting echo audio: {e}")
        finally:
            print("Audio echo thread stopping...")

    def stop(self) -> None:
        """Stop the echo server"""
        if self.audio_thread and self.audio_thread.is_alive():
            print("Stopping echo server thread...")
            self.stop_thread = True
            self.audio_thread.join(timeout=2.0)
            if self.audio_thread.is_alive():
                print("Warning: Echo thread did not stop cleanly")

    def cleanup(self) -> None:
        """Stop the echo server"""
        if self.audio_thread and self.audio_thread.is_alive():
            print("Stopping echo server thread...")
            self.stop_thread = True
            self.audio_thread.join(timeout=2.0)
            if self.audio_thread.is_alive():
                print("Warning: Echo thread did not stop cleanly")
