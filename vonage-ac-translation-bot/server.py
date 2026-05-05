import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from opentok import Client as OpenTokClient
from opentok import MediaModes, Roles
from vonage import Auth, HttpClientOptions, Vonage
from vonage_video import AudioConnectorOptions, TokenOptions

load_dotenv(override=True)

ACTIVE_SESSION_ID: str | None = None


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(status_code=500, detail=f"Missing env var: {name}")
    return value


def _get_active_session_id() -> str:
    global ACTIVE_SESSION_ID
    if ACTIVE_SESSION_ID:
        return ACTIVE_SESSION_ID
    return _require_env("VONAGE_SESSION_ID")


def _resolve_api_url(default: str = "https://api.opentok.com") -> str:
    return os.getenv("API_URL", default)


def _api_host_from_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    if parsed.hostname:
        return parsed.hostname
    return api_url.replace("https://", "").replace("http://", "").strip("/")


def _require_opentok_credentials() -> tuple[str, str]:
    api_key = os.getenv("OPENTOK_API_KEY") or os.getenv("VONAGE_API_KEY")
    api_secret = os.getenv("OPENTOK_API_SECRET") or os.getenv("VONAGE_API_SECRET")
    if not api_key or not api_secret:
        raise HTTPException(
            status_code=500,
            detail=(
                "Missing OpenTok credentials for web client token generation "
                "(OPENTOK_API_KEY/OPENTOK_API_SECRET or VONAGE_API_KEY/VONAGE_API_SECRET)"
            ),
        )
    if api_key.startswith("YOUR_") or api_secret.startswith("YOUR_"):
        raise HTTPException(status_code=500, detail="OpenTok credentials are placeholders in .env")
    return api_key, api_secret


def _create_client_token(role: str, compat_publisher: bool = False) -> dict[str, str]:
    session_id = _get_active_session_id()
    if session_id.startswith("YOUR_"):
        raise HTTPException(status_code=500, detail="VONAGE_SESSION_ID is a placeholder in .env")

    api_key, api_secret = _require_opentok_credentials()
    api_url = _resolve_api_url()

    try:
        opentok_client = OpenTokClient(api_key, api_secret, api_url=api_url)
    except TypeError:
        opentok_client = OpenTokClient(api_key, api_secret)

    try:
        opentok_client.list_streams(session_id)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "Session validation failed for configured credentials/API_URL. "
                "Ensure VONAGE_SESSION_ID, API key/secret, and API_URL belong to the same environment "
                f"(api_url={api_url}, api_key={api_key}). Underlying error: {error}"
            ),
        )

    token_role_label = "default"
    try:
        if role == "listener" and not compat_publisher:
            token = opentok_client.generate_token(session_id)
        else:
            token_role = Roles.publisher
            token = opentok_client.generate_token(session_id, role=token_role, data=f"role={role}")
            token_role_label = str(token_role)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to generate client token: {error}")

    logger.info(
        f"Generated client token: role={role}, token_role={token_role_label}, api_url={api_url}, api_key={api_key}, session_id={session_id}"
    )

    return {
        "apiKey": api_key,
        "sessionId": session_id,
        "token": token,
        "role": role,
        "tokenRole": token_role_label,
        "apiUrl": api_url,
        "secretExposed": False,
    }


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
        f"Calling Vonage Audio Connector connect: session_id={session_id}, ws_uri={ws_uri}, audioRate={audio_rate}, api_base={api_base}"
    )

    def _call_opentok_connect() -> Any:
        try:
            opentok_client = OpenTokClient(api_key, api_secret, api_url=api_base)
        except TypeError:
            opentok_client = OpenTokClient(api_key, api_secret)

        token = opentok_client.generate_token(
            session_id,
            role=Roles.publisher,
            data="role=translator-bot",
        )
        ws_options = {
            "uri": ws_uri,
            "audioRate": audio_rate,
            "bidirectional": True,
        }
        return opentok_client.connect_audio_to_websocket(session_id, token, ws_options)

    def _call_vonage_connect() -> Any:
        auth = Auth(
            application_id=application_id,
            private_key=private_key,
        )

        options = HttpClientOptions(video_host="video." + _api_host_from_url(api_base), timeout=30)
        vonage_client = Vonage(auth=auth, http_client_options=options)

        token_options = TokenOptions(session_id=session_id, role="publisher")
        client_token = vonage_client.video.generate_client_token(token_options)

        ws_options = {
            "uri": ws_uri,
            "audioRate": audio_rate,
            "bidirectional": True,
        }

        audio_connector_options = AudioConnectorOptions(
            session_id=session_id,
            token=client_token,
            websocket=ws_options,
        )
        return vonage_client.video.start_audio_connector(audio_connector_options)

    loop = asyncio.get_running_loop()
    if use_application_auth:
        return await loop.run_in_executor(None, _call_vonage_connect)
    return await loop.run_in_executor(None, _call_opentok_connect)


@asynccontextmanager
async def lifespan(_app: FastAPI):
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


@app.get("/config-summary")
async def config_summary() -> JSONResponse:
    api_key = os.getenv("OPENTOK_API_KEY") or os.getenv("VONAGE_API_KEY") or ""
    active_session_id = ACTIVE_SESSION_ID or os.getenv("VONAGE_SESSION_ID", "")
    return JSONResponse(
        {
            "translation_mode": "one_way",
            "source_language": os.getenv("SOURCE_LANGUAGE", ""),
            "target_language": os.getenv("TARGET_LANGUAGE", ""),
            "session_id": active_session_id,
            "session_source": "runtime" if ACTIVE_SESSION_ID else "env",
            "api_key": api_key,
            "audio_rate": os.getenv("VONAGE_AUDIO_RATE", "16000"),
            "api_url": _resolve_api_url(),
            "ws_uri": os.getenv("WS_URI", ""),
            "secret_exposed": False,
        }
    )


@app.get("/client-config/{role}")
async def client_config(role: str, compatPublisher: bool = False) -> JSONResponse:
    if role not in {"speaker", "listener"}:
        raise HTTPException(status_code=400, detail="Role must be one of: speaker, listener")
    return JSONResponse(_create_client_token(role, compat_publisher=compatPublisher))


@app.get("/session")
async def session_config() -> JSONResponse:
    session_id = _get_active_session_id()
    api_key, api_secret = _require_opentok_credentials()
    api_url = _resolve_api_url()

    try:
        opentok_client = OpenTokClient(api_key, api_secret, api_url=api_url)
    except TypeError:
        opentok_client = OpenTokClient(api_key, api_secret)

    try:
        token = opentok_client.generate_token(session_id)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to generate session token: {error}")

    logger.info(
        f"Generated /session token: api_url={api_url}, api_key={api_key}, session_id={session_id}"
    )

    return JSONResponse(
        {
            "apiKey": api_key,
            "sessionId": session_id,
            "token": token,
            "apiUrl": api_url,
        }
    )


@app.post("/session/new")
async def create_new_session() -> JSONResponse:
    global ACTIVE_SESSION_ID

    api_key, api_secret = _require_opentok_credentials()
    api_url = _resolve_api_url()

    try:
        opentok_client = OpenTokClient(api_key, api_secret, api_url=api_url)
    except TypeError:
        opentok_client = OpenTokClient(api_key, api_secret)

    try:
        created_session = opentok_client.create_session(media_mode=MediaModes.routed)
        session_id = created_session.session_id
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to create session: {error}")

    ACTIVE_SESSION_ID = session_id
    logger.info(
        f"Created new runtime session: api_url={api_url}, api_key={api_key}, session_id={session_id}"
    )

    return JSONResponse(
        {
            "sessionId": session_id,
            "sessionSource": "runtime",
            "apiUrl": api_url,
            "apiKey": api_key,
            "mediaMode": "routed",
            "message": "New session created and set as active for this server process",
        }
    )


@app.post("/session/use-env")
async def use_env_session() -> JSONResponse:
    global ACTIVE_SESSION_ID

    env_session_id = _require_env("VONAGE_SESSION_ID")
    ACTIVE_SESSION_ID = None

    return JSONResponse(
        {
            "sessionId": env_session_id,
            "sessionSource": "env",
            "message": "Runtime session cleared; app now uses VONAGE_SESSION_ID from .env",
        }
    )


@app.get("/ui", response_class=HTMLResponse)
async def ui_index() -> HTMLResponse:
    return HTMLResponse(
        """
<!doctype html>
<html>
    <head>
        <meta charset=\"utf-8\" />
            <title>Vonage One-Way Translation UI</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 720px; margin: 2rem auto; }
            a { display: block; margin: 0.75rem 0; font-size: 1.1rem; }
            code { background: #f5f5f5; padding: 0.15rem 0.35rem; }
            #health { margin-top: 0.5rem; margin-bottom: 0.5rem; font-weight: bold; }
            #config { white-space: pre-wrap; background: #f7f7f7; padding: 0.75rem; min-height: 120px; }
        </style>
    </head>
    <body>
        <h2>Vonage One-Way Translation Bot - Simple UI</h2>
        <div id="health">Health: checking...</div>
        <h4>Active Config</h4>
        <div id="config">Loading...</div>
        <a href=\"/ui/speaker\" target=\"_blank\">Open Speaker Page</a>
        <a href=\"/ui/listener\" target=\"_blank\">Open Listener Page</a>
        <p>After listener joins, trigger Audio Connector from terminal:</p>
        <p><code>curl -X POST http://localhost:8005/connect</code></p>

        <script>
            const healthEl = document.getElementById('health');
            const configEl = document.getElementById('config');

            async function refreshHealth() {
                try {
                    const response = await fetch('/health');
                    if (!response.ok) {
                        healthEl.textContent = 'Health: DOWN';
                        return;
                    }
                    const body = await response.json();
                    healthEl.textContent = body.ok ? 'Health: OK' : 'Health: DOWN';
                } catch (_) {
                    healthEl.textContent = 'Health: DOWN';
                }
            }

            async function refreshConfig() {
                try {
                    const response = await fetch('/config-summary');
                    const cfg = await response.json();
                    const lines = [
                        `translation_mode: ${cfg.translation_mode}`,
                        `source_language: ${cfg.source_language || '(not set)'}`,
                        `target_language: ${cfg.target_language || '(not set)'}`,
                        `session_id: ${cfg.session_id || '(not set)'}`,
                        `api_key: ${cfg.api_key || '(not set)'}`,
                        `audio_rate: ${cfg.audio_rate}`,
                        `api_url: ${cfg.api_url}`,
                        `ws_uri: ${cfg.ws_uri || '(not set)'}`,
                    ];
                    configEl.textContent = lines.join('\n');
                } catch (error) {
                    configEl.textContent = `Failed to load config: ${error.message}`;
                }
            }

            refreshHealth();
            refreshConfig();
            setInterval(refreshHealth, 5000);
            setInterval(refreshConfig, 5000);
        </script>
    </body>
</html>
"""
    )


@app.get("/ui/speaker", response_class=HTMLResponse)
async def ui_speaker() -> HTMLResponse:
    return HTMLResponse(
        """
<!doctype html>
<html>
    <head>
        <meta charset=\"utf-8\" />
            <title>Speaker - One-Way</title>
        <script src=\"https://static.opentok.com/v2/js/opentok.min.js\"></script>
        <style>
            body { font-family: Arial, sans-serif; max-width: 720px; margin: 2rem auto; }
            button { margin-right: 0.5rem; }
            #log { white-space: pre-wrap; background: #f7f7f7; padding: 0.75rem; min-height: 120px; }
        </style>
    </head>
    <body>
            <h3>Speaker (publishes source audio only)</h3>
        <button id=\"join\">Join as Speaker</button>
        <button id=\"leave\" disabled>Leave</button>
        <div id=\"pub\"></div>
            <p>Steps: join here, open listener page in another browser/device, then connect Audio Connector from <code>/ui</code>.</p>
        <div id=\"log\"></div>

        <script>
            let session = null;
            let publisher = null;

            function log(msg) {
                const el = document.getElementById('log');
                el.textContent += `${new Date().toLocaleTimeString()}  ${msg}\n`;
            }

            async function join() {
                try {
                    const response = await fetch('/client-config/speaker');
                    const raw = await response.text();
                    let cfg = null;
                    try {
                        cfg = JSON.parse(raw);
                    } catch (_) {
                        throw new Error(`client-config returned non-JSON: ${raw}`);
                    }
                    if (!response.ok) {
                        throw new Error(cfg.detail || raw);
                    }

                    log(`using api_key=${cfg.apiKey}, session_id=${cfg.sessionId}`);

                    session = OT.initSession(cfg.apiKey, cfg.sessionId);
                    session.on('sessionConnected', () => log('connected'));
                    session.on('streamCreated', () => log('remote stream detected (not subscribed on speaker page)'));

                    session.connect(cfg.token, (error) => {
                        if (error) return log(`connect error: ${error.message}`);

                        publisher = OT.initPublisher('pub', {
                            insertMode: 'append',
                            width: '100%',
                            height: '80px',
                            publishVideo: false,
                        }, (pubErr) => pubErr && log(`publisher init error: ${pubErr.message}`));

                        session.publish(publisher, (pubError) => {
                            if (pubError) return log(`publish error: ${pubError.message}`);
                            log('microphone publishing started');
                            document.getElementById('join').disabled = true;
                            document.getElementById('leave').disabled = false;
                        });
                    });
                } catch (e) {
                    log(`join failed: ${e.message}`);
                }
            }

            function leave() {
                if (session) {
                    session.disconnect();
                }
                session = null;
                publisher = null;
                document.getElementById('join').disabled = false;
                document.getElementById('leave').disabled = true;
                log('left session');
            }

            document.getElementById('join').onclick = join;
            document.getElementById('leave').onclick = leave;
        </script>
    </body>
</html>
"""
    )


@app.get("/ui/listener", response_class=HTMLResponse)
async def ui_listener() -> HTMLResponse:
    return HTMLResponse(
        """
<!doctype html>
<html>
    <head>
        <meta charset=\"utf-8\" />
            <title>Listener - One-Way</title>
        <script src=\"https://static.opentok.com/v2/js/opentok.min.js\"></script>
        <style>
            body { font-family: Arial, sans-serif; max-width: 720px; margin: 2rem auto; }
            button { margin-right: 0.5rem; }
            #audio { margin-top: 1rem; }
            #log { white-space: pre-wrap; background: #f7f7f7; padding: 0.75rem; min-height: 120px; }
        </style>
    </head>
    <body>
            <h3>Listener (hears translated audio only)</h3>
        <button id=\"join\">Join as Listener</button>
        <button id=\"leave\" disabled>Leave</button>
        <div id=\"audio\"></div>
            <p>Filter rule: subscribe only streams tagged with <code>role=translator-bot</code>.</p>
        <label><input type="checkbox" id="useSessionEndpoint" /> Use sample-style /session token endpoint</label><br/>
        <label><input type="checkbox" id="compatPublisher" /> Use compatibility token role (publisher)</label><br/>
            <label><input type="checkbox" id="allowAll" /> Allow all remote audio (debug)</label>
        <div id=\"log\"></div>

        <script>
            let session = null;
            let activeSubscriber = null;

            function log(msg) {
                const el = document.getElementById('log');
                el.textContent += `${new Date().toLocaleTimeString()}  ${msg}\n`;
            }

            async function unblockAudio() {
                if (!window.OT || typeof OT.unblockAudio !== 'function') {
                    return;
                }
                try {
                    const result = OT.unblockAudio();
                    if (result && typeof result.then === 'function') {
                        await result;
                    }
                    log('audio output unblocked');
                } catch (error) {
                    log(`audio unblock warning: ${error.message || error}`);
                }
            }

            function shouldSubscribe(stream) {
                if (document.getElementById('allowAll').checked) {
                    return true;
                }
                const data = (stream.connection && stream.connection.data) || '';
                return data.includes('role=translator-bot');
            }

            async function join() {
                try {
                    await unblockAudio();

                    const compatPublisher = document.getElementById('compatPublisher').checked;
                    const useSessionEndpoint = document.getElementById('useSessionEndpoint').checked;
                    const endpoint = useSessionEndpoint
                        ? '/session'
                        : `/client-config/listener?compatPublisher=${compatPublisher}`;
                    const response = await fetch(endpoint);
                    const raw = await response.text();
                    let cfg = null;
                    try {
                        cfg = JSON.parse(raw);
                    } catch (_) {
                        throw new Error(`client-config returned non-JSON: ${raw}`);
                    }
                    if (!response.ok) {
                        throw new Error(cfg.detail || raw);
                    }

                    log(`using api_url=${cfg.apiUrl}, api_key=${cfg.apiKey}, session_id=${cfg.sessionId}, token_role=${cfg.tokenRole}`);
                    log('token generated by backend using configured API_URL; secret is never exposed in UI logs');
                    if (useSessionEndpoint) {
                        log('sample mode enabled: token is generated via /session (default SDK behavior)');
                    }
                    if (compatPublisher) {
                        log('compat mode enabled: listener token role forced to publisher');
                    }

                    session = OT.initSession(cfg.apiKey, cfg.sessionId);
                    session.on('sessionConnected', () => log('connected'));

                    session.on('streamCreated', (event) => {
                        if (!shouldSubscribe(event.stream)) {
                            log('ignored non-bot stream');
                            return;
                        }

                        log(`bot stream detected: hasAudio=${event.stream.hasAudio}`);

                        activeSubscriber = session.subscribe(event.stream, 'audio', {
                            insertMode: 'append',
                            subscribeToVideo: false,
                            subscribeToAudio: true,
                            audioVolume: 100,
                        }, (error) => {
                            if (error) log(`subscribe error: ${error.message}`);
                            else {
                                try {
                                    activeSubscriber.subscribeToAudio(true);
                                } catch (_) {}
                                log('subscribed to translated stream');
                            }
                        });
                    });

                    session.connect(cfg.token, (error) => {
                        if (error) return log(`connect error: ${error.message}`);
                        document.getElementById('join').disabled = true;
                        document.getElementById('leave').disabled = false;
                    });
                } catch (e) {
                    log(`join failed: ${e.message}`);
                }
            }

            function leave() {
                if (session) {
                    session.disconnect();
                }
                session = null;
                activeSubscriber = null;
                document.getElementById('join').disabled = false;
                document.getElementById('leave').disabled = true;
                log('left session');
            }

            document.getElementById('join').onclick = join;
            document.getElementById('leave').onclick = leave;
        </script>
    </body>
</html>
"""
    )


@app.post("/connect")
async def connect(_request: Request) -> JSONResponse:
    application_id = os.getenv("VONAGE_APPLICATION_ID")
    private_key = os.getenv("VONAGE_PRIVATE_KEY")

    api_key = os.getenv("OPENTOK_API_KEY") or os.getenv("VONAGE_API_KEY")
    api_secret = os.getenv("OPENTOK_API_SECRET") or os.getenv("VONAGE_API_SECRET")

    if application_id and private_key and not application_id.startswith("YOUR_"):
        use_application_auth = True
        api_base = _resolve_api_url("api.vonage.com")
    elif api_key and api_secret:
        use_application_auth = False
        api_base = _resolve_api_url("https://api.opentok.com")
    else:
        raise HTTPException(
            status_code=500,
            detail=(
                "Missing Vonage auth env vars: either VONAGE_APPLICATION_ID and VONAGE_PRIVATE_KEY, "
                "or OPENTOK_API_KEY/OPENTOK_API_SECRET (or VONAGE_API_KEY/VONAGE_API_SECRET)"
            ),
        )

    session_id = _get_active_session_id()
    audio_rate = int(os.getenv("VONAGE_AUDIO_RATE", "16000"))
    ws_uri = _require_env("WS_URI")

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
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to connect Audio Connector: {error}")

    return JSONResponse(
        {
            "status": "connect_triggered",
            "session_id": session_id,
            "ws_uri": ws_uri,
            "audio_rate": audio_rate,
            "api_base": api_base,
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
    except Exception as error:
        logger.exception(f"Error while running bot on Vonage websocket: {error}")
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
