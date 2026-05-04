#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Vonage Voice API server for the MCP-only agent.

- /answer returns NCCO connecting Vonage to /ws
- /events logs call events
- /ws runs Pipecat bot
"""

import json
import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger

load_dotenv(override=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from bot import warmup

    warmup()
    logger.info("Agent warmup complete.")
    yield


app = FastAPI(lifespan=lifespan)
# CORS configuration: wildcard origins must not be combined with credentials in production.
# If you need credentialed requests, replace ["*"] with a list of trusted origins and
# then set allow_credentials=True.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.api_route("/answer", methods=["GET", "POST"])
async def answer(request: Request):
    logger.debug("Incoming /answer request")
    try:
        body = await request.body()
        if body:
            logger.debug("ANSWER body: {}", body.decode("utf-8", errors="replace"))
    except Exception:
        pass

    logger.debug("ANSWER query: {}", dict(request.query_params))

    ws_uri = os.getenv("WS_URI")
    from_number = os.getenv("VONAGE_VOICE_FROM_NUMBER")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not ws_uri:
        raise HTTPException(status_code=500, detail="Missing env var: WS_URI")
    if not from_number:
        raise HTTPException(
            status_code=500,
            detail="Missing env var: VONAGE_VOICE_FROM_NUMBER (linked Vonage number, e.g. 190458XXXXX)",
        )
    if not openai_api_key:
        raise HTTPException(status_code=500, detail="Missing env var: OPENAI_API_KEY")

    ncco = [
        {"action": "talk", "text": "Please wait while we connect you to the assistant."},
        {
            "action": "connect",
            "from": from_number,
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": ws_uri,
                    "content-type": "audio/l16;rate=16000",
                }
            ],
        },
    ]
    response = JSONResponse(content=ncco)
    logger.debug("Sending NCCO: {}", response.body)
    return response


@app.api_route("/events", methods=["GET", "POST"])
async def events(request: Request):
    if request.method == "GET":
        event_data = dict(request.query_params)
        # Log only high-level, non-PII details at INFO level.
        event_type = event_data.get("status") or event_data.get("type")
        uuid = event_data.get("uuid") or event_data.get("conversation_uuid")
        logger.info("EVENTS (GET): type={} uuid={}", event_type, uuid)
        # Full query parameters are logged at DEBUG to reduce PII exposure.
        logger.debug("EVENTS (GET) full query: {}", json.dumps(event_data, indent=2))
    else:
        raw = await request.body()
        text = raw.decode("utf-8", errors="replace")
        if not text:
            logger.info("EVENTS (POST): empty body")
        else:
            try:
                parsed = json.loads(text)
                # Log only high-level, non-PII details at INFO level.
                event_type = parsed.get("status") or parsed.get("type")
                uuid = parsed.get("uuid") or parsed.get("conversation_uuid")
                logger.info("EVENTS (POST): type={} uuid={}", event_type, uuid)
                # Full payload is logged at DEBUG to reduce PII exposure.
                logger.debug("EVENTS (POST) full payload: {}", json.dumps(parsed, indent=2))
            except Exception:
                # Avoid logging potentially sensitive raw body at INFO.
                logger.info("EVENTS (POST): non-JSON body received (length={} bytes)", len(text))
                logger.debug("EVENTS (POST) raw body: {}", text)

    return PlainTextResponse("ok")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Vonage WebSocket connected to /ws")

    if not os.getenv("OPENAI_API_KEY"):
        logger.error("Closing websocket: OPENAI_API_KEY is not configured")
        await websocket.close(code=1011, reason="Server misconfiguration: missing OPENAI_API_KEY")
        return

    try:
        from bot import bot
        from pipecat.runner.types import WebSocketRunnerArguments

        runner_args = WebSocketRunnerArguments(websocket=websocket, body={})
        await bot(runner_args)

    except Exception as e:
        logger.exception("Error while running bot on Vonage websocket: {}", e)
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
