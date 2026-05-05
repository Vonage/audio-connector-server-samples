#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Vonage Voice API server for the appointment booking agent.

Handles incoming phone calls via /answer and /events webhooks,
and streams audio to/from the Pipecat bot over WebSocket.

Run:
  uv run server.py

Env required:
  WS_URI                       (public wss://.../ws for websocket)
  VONAGE_VOICE_FROM_NUMBER     (linked Vonage number, e.g. 190458XXXXX)
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
    """Pre-import bot and load models at startup so first call connects without delay."""
    from bot import warmup

    warmup()
    logger.info("Appointment bot warmup complete.")
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


@app.api_route("/answer", methods=["GET", "POST"])
async def answer(request: Request):
    """
    Vonage Voice API calls this webhook to get the NCCO.
    Returns NCCO that connects the call to our WebSocket for the appointment bot.
    """
    logger.debug("Incoming /answer request")
    try:
        body = await request.body()
        if body:
            logger.info("ANSWER body: {}", body.decode("utf-8", errors="replace"))
    except Exception:
        pass

    logger.info("ANSWER query: {}", dict(request.query_params))

    ws_uri = os.getenv("WS_URI")
    from_number = os.getenv("VONAGE_VOICE_FROM_NUMBER")
    if not ws_uri:
        raise HTTPException(status_code=500, detail="Missing env var: WS_URI")
    if not from_number:
        raise HTTPException(
            status_code=500,
            detail="Missing env var: VONAGE_VOICE_FROM_NUMBER (linked Vonage number, e.g. 190458XXXXX)",
        )

    ncco = [
        {
            "action": "talk",
            "text": "Please wait while we connect you to the appointment booking agent.",
        },
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
    """
    Vonage Voice API status events webhooks land here.
    Accepts GET or POST. We log and return 200 OK.
    """
    if request.method == "GET":
        event_data = dict(request.query_params)
        logger.info("EVENTS (GET) query: {}", json.dumps(event_data, indent=2))
    else:
        raw = await request.body()
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
            logger.info("EVENTS (POST) json: {}", json.dumps(parsed, indent=2))
        except Exception:
            logger.info("EVENTS (POST) raw: {}", text)

    return PlainTextResponse("ok")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Vonage Voice API connects here and streams audio to/from the appointment bot.
    """
    await websocket.accept()
    logger.info("Vonage WebSocket connected to /ws")

    try:
        from bot import bot
        from pipecat.runner.types import WebSocketRunnerArguments

        runner_args = WebSocketRunnerArguments(websocket=websocket, body={})
        await bot(runner_args)

    except Exception as e:
        logger.exception("Error while running appointment bot on Vonage websocket: {}", e)
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
