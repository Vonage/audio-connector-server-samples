import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from opentok import Client as OpenTokClient
from vonage import Auth, HttpClientOptions, Vonage
from vonage_video import AudioConnectorOptions, TokenOptions

load_dotenv(override=True)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(status_code=500, detail=f"Missing env var: {name}")
    return value


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
    logger.info(
        f"Calling Vonage Audio Connector connect: session_id={session_id}, ws_uri={ws_uri}, audioRate={audio_rate}"
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
        }
        return ot.connect_audio_to_websocket(session_id, token, ws_opts)

    def _call_vonage_connect() -> Any:
        auth = Auth(
            application_id=application_id,
            private_key=private_key,
        )
        options = HttpClientOptions(video_host="video." + api_base, timeout=30)
        client = Vonage(auth=auth, http_client_options=options)

        token_options = TokenOptions(session_id=session_id, role="publisher")
        client_token = client.video.generate_client_token(token_options)
        ws_opts = {
            "uri": ws_uri,
            "audioRate": audio_rate,
            "bidirectional": True,
        }

        audio_connector_options = AudioConnectorOptions(
            session_id=session_id,
            token=client_token,
            websocket=ws_opts,
        )
        return client.video.start_audio_connector(audio_connector_options)

    loop = asyncio.get_running_loop()
    if use_application_auth:
        return await loop.run_in_executor(None, _call_vonage_connect)
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
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/connect")
async def connect() -> JSONResponse:
    application_id = os.getenv("VONAGE_APPLICATION_ID")
    private_key = os.getenv("VONAGE_PRIVATE_KEY")

    api_key = os.getenv("OPENTOK_API_KEY")
    api_secret = os.getenv("OPENTOK_API_SECRET")

    if application_id and private_key and not application_id.startswith("YOUR_"):
        # For Vonage Application auth, api_base must be a bare host (no scheme/path)
        # because it is used as "video." + api_base to build the video endpoint.
        raw = os.getenv("API_URL", "api.vonage.com")
        api_base = raw.removeprefix("https://").removeprefix("http://").rstrip("/")
        use_application_auth = True
    elif api_key and api_secret:
        api_base = os.getenv("API_URL", "https://api.opentok.com")
        use_application_auth = False
    else:
        raise HTTPException(
            status_code=500,
            detail=(
                "Missing auth env vars: set VONAGE_APPLICATION_ID and VONAGE_PRIVATE_KEY, "
                "or OPENTOK_API_KEY and OPENTOK_API_SECRET"
            ),
        )

    session_id = _require_env("VONAGE_SESSION_ID")
    ws_uri = _require_env("WS_URI")
    audio_rate = int(os.getenv("VONAGE_AUDIO_RATE", "16000"))

    try:
        response = await connect_audio_connector(
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to connect audio connector: {exc}")

    return JSONResponse(
        {
            "status": "connect_triggered",
            "session_id": session_id,
            "ws_uri": ws_uri,
            "audio_rate": audio_rate,
            "response_repr": repr(response),
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Vonage WebSocket connected to /ws")

    try:
        from bot import bot
        from pipecat.runner.types import WebSocketRunnerArguments

        runner_args = WebSocketRunnerArguments(websocket=websocket, body={})
        await bot(runner_args)

    except Exception as exc:
        logger.exception(f"Error while running Pipecat bot on Vonage websocket: {exc}")
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8005")))
