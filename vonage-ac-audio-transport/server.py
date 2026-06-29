"""
server.py

Vonage Audio Connector example using the **audio transport** feature.

Instead of raw binary PCM frames, the Audio Connector sends and receives
audio as base64-encoded JSON messages over WebSocket. This enables sending
metadata alongside audio in every frame.

Run:
  uvicorn server:app --host 0.0.0.0 --port 8005

Env required (Video API /connect):
  VONAGE_SESSION_ID
  WS_URI                       (public wss://.../ws)

  Choose ONE auth method:
    Option A — Vonage Application:
      VONAGE_APPLICATION_ID
      VONAGE_PRIVATE_KEY
    Option B — OpenTok project:
      OPENTOK_API_KEY
      OPENTOK_API_SECRET

Env required (bot):
  OPENAI_API_KEY

Optional:
  API_URL                      (default api.vonage.com / https://api.opentok.com)
  VONAGE_AUDIO_RATE            (default 16000)
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from opentok import Client as OpenTokClient
from vonage import Auth, HttpClientOptions, Vonage

load_dotenv(override=True)


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise HTTPException(status_code=500, detail=f"Missing env var: {name}")
    return val


async def connect_audio_connector(
    *,
    api_key: str,
    api_secret: str,
    session_id: str,
    ws_uri: str,
    audio_rate: int,
    api_base: str,
    use_application_auth: bool,
    application_id: str,
    private_key: str,
) -> Any:
    """
    Calls the Audio Connector connect API with audioTransport set to JSON/base64.
    Both the OpenTok and Vonage SDK paths include the audioTransport header.
    """
    logger.info(
        f"Calling Audio Connector connect (JSON transport): "
        f"session_id={session_id}, ws_uri={ws_uri}, audioRate={audio_rate}"
    )

    def _call_opentok_connect() -> Any:
        try:
            ot = OpenTokClient(api_key, api_secret, api_url=api_base)
        except TypeError:
            ot = OpenTokClient(api_key, api_secret)

        token = ot.generate_token(session_id)
        ws_opts = {
            "uri": ws_uri,
            "audioRate": audio_rate,
            "bidirectional": True,
            "audioTransport": {
                "transport": "json",
                "encoding": "base64",
            },
        }
        return ot.connect_audio_to_websocket(session_id, token, ws_opts)

    def _call_vonage_connect() -> Any:
        from vonage_video import (
            AudioConnectorOptions,
            AudioConnectorWebSocket,
            AudioTransport,
            AudioTransportConfig,
            TokenOptions,
        )

        auth = Auth(
            application_id=application_id,
            private_key=private_key,
        )
        options = HttpClientOptions(video_host="video." + api_base, timeout=30)
        vng = Vonage(auth=auth, http_client_options=options)

        token_options = TokenOptions(session_id=session_id, role="publisher")
        client_token = vng.video.generate_client_token(token_options)

        websocket = AudioConnectorWebSocket(
            uri=ws_uri,
            audio_rate=audio_rate,
            bidirectional=True,
            audio_transport=AudioTransportConfig(
                transport=AudioTransport.JSON,
                encoding="base64",
            ),
        )

        audio_connector_options = AudioConnectorOptions(
            session_id=session_id,
            token=client_token,
            websocket=websocket,
        )
        return vng.video.start_audio_connector(audio_connector_options)

    loop = asyncio.get_running_loop()
    if use_application_auth:
        return await loop.run_in_executor(None, _call_vonage_connect)
    else:
        return await loop.run_in_executor(None, _call_opentok_connect)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/connect")
async def connect(request: Request) -> JSONResponse:
    """
    Trigger Vonage Audio Connector to connect to our WebSocket
    with JSON audio transport enabled.
    """
    application_id = os.getenv("VONAGE_APPLICATION_ID")
    private_key = os.getenv("VONAGE_PRIVATE_KEY")

    api_key = os.getenv("OPENTOK_API_KEY")
    api_secret = os.getenv("OPENTOK_API_SECRET")

    if application_id and private_key and not application_id.startswith("YOUR_"):
        api_base = os.getenv("API_URL", "api.vonage.com")
        use_application_auth = True
    elif api_key and api_secret:
        api_base = os.getenv("API_URL", "https://api.opentok.com")
        use_application_auth = False
    else:
        raise HTTPException(
            status_code=500,
            detail=(
                "Missing auth env vars: either VONAGE_APPLICATION_ID + "
                "VONAGE_PRIVATE_KEY, or OPENTOK_API_KEY + OPENTOK_API_SECRET"
            ),
        )

    session_id = _require_env("VONAGE_SESSION_ID")
    audio_rate = int(os.getenv("VONAGE_AUDIO_RATE", "16000"))
    ws_uri = os.getenv("WS_URI")

    try:
        resp = await connect_audio_connector(
            api_key=api_key,
            api_secret=api_secret,
            session_id=session_id,
            ws_uri=ws_uri,
            audio_rate=audio_rate,
            api_base=api_base,
            use_application_auth=use_application_auth,
            application_id=application_id,
            private_key=private_key,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to connect Audio Connector: {e}"
        )

    return JSONResponse(
        {
            "status": "connect_triggered",
            "audio_transport": "json/base64",
            "session_id": session_id,
            "ws_uri": ws_uri,
            "audio_rate": audio_rate,
            "api_url": api_base,
            "response_repr": repr(resp),
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Vonage Audio Connector connects here and sends JSON-wrapped base64 audio.
    """
    await websocket.accept()
    logger.info("Vonage WebSocket connected to /ws (JSON audio transport)")

    try:
        from bot import bot
        from pipecat.runner.types import WebSocketRunnerArguments

        runner_args = WebSocketRunnerArguments(websocket=websocket, body={})
        await bot(runner_args)

    except Exception as e:
        logger.exception(f"Error running bot: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
