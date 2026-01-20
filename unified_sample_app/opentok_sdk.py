# Opentok SDK example for Audio Connector
import json
from opentok import Client
from opentok import Roles


class OpentokSDK:
    """Main Server SDK class for using OpenTok APIs."""

    def __init__(self, args):
        self.client_token = None
        self.ot = None
        self.args = args
        self.session_id = None
        self.token = None

    def generate_tokens(self):
        try:
            opentok_url = self.args.api_base or "https://api.opentok.com"
            try:
                self.ot = Client(
                    self.args.api_key, self.args.api_secret, api_url=opentok_url
                )
            except TypeError:
                # Fallback for older SDKs that don't accept api_url
                self.ot = Client(self.args.api_key, self.args.api_secret)

            self.session_id = self.args.session_id
            print(f"Using existing session: {self.session_id}")

            # Token: generate a fresh one tied to this session
            self.token = self.ot.generate_token(
                session_id=self.session_id, role=Roles.publisher
            )
            print(
                f"Generated token: {self.token[:32]}..."
            )  # don’t print full token in logs

        except Exception as e:
            raise SystemExit(f"Error: {e}")

    def start_audio_connector(self):
        try:
            # Build websocket options
            ws_opts = {
                "uri": self.args.ws_uri,
                "audioRate": self.args.audio_rate,
                "bidirectional": bool(self.args.bidirectional),
            }

            print("Connecting audio to WebSocket with options:")
            print(json.dumps(ws_opts, indent=2))

            # Call the Audio Connector (this is equivalent to POST /v2/project/{apiKey}/connect)
            self.ot.connect_audio_to_websocket(self.session_id, self.token, ws_opts)
        except Exception as e:
            raise SystemExit(f"Error: {e}")
