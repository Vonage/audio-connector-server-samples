# Vonage SDK example for Audio Connector
import json
from vonage import Vonage, Auth, HttpClientOptions
from vonage_video import AudioConnectorOptions, TokenOptions, SessionOptions


class VonageSDK:
    """Main Server SDK class for using Vonage APIs.

    When creating an instance, it will create the authentication objects and
    an HTTP Client needed for using Vonage APIs.
    Use an instance of this class to access the Vonage APIs, e.g. to access
    methods associated with the Vonage SMS API, call `vonage.sms.method_name()`.
    """

    def __init__(self, args):
        self.client_token = None
        self.vonage = None
        self.session_id = None
        self.args = args

    def generate_tokens(self):
        try:
            # Create an Auth instance
            auth = Auth(
                application_id=self.args.application_id,
                private_key=self.args.private_key,
            )

            # Create HttpClientOptions instance
            # (not required unless you want to change options from the defaults)
            vonage_url = self.args.api_base or "api.vonage.com"

            options = HttpClientOptions(video_host="video." + vonage_url, timeout=30)

            # Create a Vonage instance
            self.vonage = Vonage(auth=auth, http_client_options=options)

            if self.args.session_id:
                self.session_id = self.args.session_id
                print(f"Using existing session: {self.session_id}")
            else:
                # Create a Video session
                session_options = SessionOptions()
                session = self.vonage.video.create_session(session_options)
                self.session_id = session.session_id
                print(f"Created new session: {self.session_id}")

            # Generate a client token for the Audio Connector to use
            token_options = TokenOptions(session_id=self.session_id, role="publisher")
            self.client_token = self.vonage.video.generate_client_token(token_options)
            print(
                f"Generated token: {self.client_token[:32]}..."
            )  # don’t print full token in logs

        except Exception as e:
            raise SystemExit(f"Error: {e}")

    def start_audio_connector(self):
        # Build websocket options
        ws_opts = {
            "uri": self.args.ws_uri,
            "audioRate": self.args.audio_rate,
            "bidirectional": bool(self.args.bidirectional),
        }
        print("Connecting audio to WebSocket with options:")
        print(json.dumps(ws_opts, indent=2))

        audio_connector_options = AudioConnectorOptions(
            session_id=self.session_id, token=self.client_token, websocket=ws_opts
        )
        return self.vonage.video.start_audio_connector(audio_connector_options)
