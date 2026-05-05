# Vonage Audio Connector — JSON Audio Transport

This example demonstrates the **audio transport** feature for Vonage Audio Connector. Instead of sending raw binary PCM frames over WebSocket, the Audio Connector sends and receives audio as **base64-encoded JSON messages**.

## What is Audio Transport?

By default, Audio Connector streams audio as raw binary PCM frames over the WebSocket. With the `audioTransport` option, you can switch to **JSON transport**:

```
Default (binary):   WebSocket frame = raw PCM bytes
JSON transport:     WebSocket frame = {"audio": "<base64-encoded PCM>"}
```

This enables sending structured metadata alongside audio data in every frame using `static_fields`, custom field naming with `audio_field`, and separate inbound/outbound field names with `receive_audio_field`.

## How It Works

1. **POST `/connect`** triggers the Audio Connector API with `audioTransport` set to `json` + `base64`
2. Vonage opens a WebSocket to `/ws` and sends JSON messages instead of binary frames
3. Each inbound message contains base64-encoded PCM audio in the `audio` field
4. The bot decodes the audio, runs it through OpenAI Realtime (speech → speech), and sends responses back as base64 JSON
5. Vonage decodes the response audio and plays it into the session

### Audio Transport Config

When connecting via the **Vonage SDK** (`vonage-video`):

```python
from vonage_video import AudioConnectorWebSocket, AudioTransportConfig, AudioTransport

websocket = AudioConnectorWebSocket(
    uri=ws_uri,
    audio_rate=16000,
    bidirectional=True,
    audio_transport=AudioTransportConfig(
        transport=AudioTransport.JSON,
        encoding="base64",
    ),
)
```

When connecting via the **OpenTok SDK** (`opentok`):

```python
ws_opts = {
    "uri": ws_uri,
    "audioRate": 16000,
    "bidirectional": True,
    "audio_transport": {
        "transport": "json",
        "encoding": "base64",
    },
}
ot.connect_audio_to_websocket(session_id, token, ws_opts)
```

## Architecture

```
Vonage Video Session
        │
        │  Audio Connector (JSON transport)
        ▼
POST /connect  ──────────▶ Vonage Cloud
                              │
                              │  wss://<server>/ws
                              ▼
                    FastAPI WebSocket (/ws)
                              │
                    {"audio": "base64..."} ◄──► {"audio": "base64..."}
                              │
                              ▼
                        Pipecat Pipeline
                  OpenAI Realtime (speech ↔ speech)
```

## API Endpoints

| Endpoint   | Method    | Description |
|------------|-----------|-------------|
| `/health`  | GET       | Health check |
| `/connect` | POST      | Triggers Audio Connector with JSON audio transport |
| `/ws`      | WebSocket | Receives/sends base64-encoded JSON audio frames |

## Prerequisites

- Python 3.10+
- OpenAI API key
- Vonage Video API credentials (Application ID + Private Key **or** OpenTok API Key + Secret)
- A Vonage Video session (routed)
- A public WebSocket URL (e.g., via ngrok)

> **Note:** This example requires the `audio_transport` feature available in the feature branches of `vonage-python-sdk`, `Opentok-Python-SDK`, and `audio-connector-server-sdk` (VIDMR-1483).

## Setup

1. **Clone and install:**

   ```bash
   cd vonage-ac-audio-transport
   pip install -e .
   ```

2. **Configure environment:**

   ```bash
   cp env.example .env
   # Edit .env with your credentials
   ```

3. **Expose your server** (for local development):

   ```bash
   ngrok http 8005
   ```

   Update `WS_URI` in `.env` with your ngrok URL: `wss://<subdomain>.ngrok.io/ws`

4. **Create a Vonage Video session** using the [Vonage Video Playground](https://tools.vonage.com/video/playground) or [OpenTok Playground](https://tokbox.com/developer/tools/playground/). Set `VONAGE_SESSION_ID` in `.env`.

5. **Start the server:**

   ```bash
   uvicorn server:app --host 0.0.0.0 --port 8005
   ```

6. **Trigger the connection:**

   ```bash
   curl -X POST http://localhost:8005/connect
   ```

   The response will include `"audio_transport": "json/base64"` confirming JSON transport is active.

## Key Differences from Other Examples

| Feature | Other examples | This example |
|---------|---------------|--------------|
| WebSocket frame format | Raw binary PCM | JSON with base64 audio |
| Serializer | `VonageFrameSerializer` | `VonageJsonTransportSerializer` (custom) |
| Audio Connector option | `bidirectional: true` | `bidirectional: true` + `audioTransport: json/base64` |
| Metadata support | Not possible | Can add `static_fields` to every frame |
