# vonage-shoe-store-audio-bot

Pipecat voice bot for shoe store workflow automation using Vonage Audio Connector (Video API).

## What this runs

1. Uses the same Vonage connector pattern as the triage nurse example.
2. Connects a Vonage Video session to `/ws` by calling `/connect`.
3. Runs a voice agent that automates shoe-store workflow steps:
   - capture customer details
   - check inventory
   - create order
   - reserve pickup window
   - send order confirmation SMS via webhook automation
4. Uses n8n webhooks as required workflow backends for inventory, order, pickup, transcript, and SMS.

## Endpoints

- `GET /health` health check
- `POST /connect` trigger Vonage Audio Connector to websocket bridge
- `WS /ws` bidirectional audio stream for Pipecat

## Prerequisites

1. Python 3.10+
2. `uv`
3. ngrok (for local development)
4. OpenAI API key
5. Vonage Video Application credentials OR OpenTok project credentials
6. Pre-created routed Vonage session ID

## Setup

1. Copy env file:

```bash
cp env.example .env
```

2. Configure required values in `.env`:

   **Core API credentials:**
   - `OPENAI_API_KEY` — your OpenAI API key
   - `VONAGE_SESSION_ID` — pre-created OpenTok/Vonage session ID
   - One auth option:
     - `VONAGE_APPLICATION_ID` + `VONAGE_PRIVATE_KEY`, or
     - `OPENTOK_API_KEY` + `OPENTOK_API_SECRET`

   **Public WebSocket URL (WS_URI):**
   - If running locally, you need to expose your server via ngrok:
     ```bash
     ngrok http 8005
     ```
   - Copy the public HTTPS URL ngrok provides (e.g., `https://abc123.ngrok.io`)
   - Set `WS_URI` in `.env` to: `wss://abc123.ngrok.io/ws`
   - **Note:** Keep ngrok running while you test the demo

   Note: the `N8N_SHOE_*_WEBHOOK` values are already prefilled with localhost defaults for this demo and usually do not need editing.

3. Start n8n first (required backend for this demo):

```bash
cd n8n
cp env.example .env
```

4. Configure SMS credentials in `n8n/.env` (IMPORTANT for SMS to work):

   - Edit `n8n/.env` and replace:
     ```
     SMS_API_KEY=YOUR_SMS_API_KEY
     SMS_API_SECRET=YOUR_SMS_API_SECRET
     ```
   - Save the file and keep note of these values

5. Start n8n:

```bash
docker compose up -d
```

Wait for n8n to start (usually 30-60 seconds). You can check status with:

```bash
docker compose logs -f
```

6. **Access n8n UI and import workflow:**

   - Open your browser and go to: **http://localhost:5678**
  - If your browser shows a basic-auth popup first, use username `admin` and password `admin123` (from `n8n/.env`)
  - If n8n then shows its own login or signup screen asking for an email address, create the owner account with any email and password on first launch
  - After that, use that email/password for future n8n logins
   - Once logged in, click the **Workflows** menu on the left
   - Click **New** or **+** button to create a new workflow
   - Click **File** → **Import from file** (or drag & drop)
   - Select `n8n/workflows/vonage-shoe-store-audio-bot.workflow.json` from the repo
   - Click **Publish** (blue button at top right) to activate the workflow

7. Return to Python app directory and continue setup:

```bash
cd ..
```

8. Install dependencies:

```bash
uv sync
```

9. Run the server:

```bash
uv run server.py
```

10. Trigger connector (make sure ngrok is still running in another terminal):

```bash
curl -X POST http://localhost:8005/connect \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## Required workflow automation webhooks

These are required for runtime, but they are already pre-populated in `env.example` for local n8n:

- `N8N_SHOE_TRANSCRIPT_WEBHOOK`
- `N8N_SHOE_INVENTORY_WEBHOOK`
- `N8N_SHOE_ORDER_WEBHOOK`
- `N8N_SHOE_PICKUP_WEBHOOK`
- `N8N_SHOE_SMS_WEBHOOK`

You only need to change them if n8n is running on a different host/port.

## Bot workflow summary

The assistant collects spoken order context in this order:

1. customer name and phone
2. brand/style + size + color preference
3. checks inventory
4. confirms SKU and quantity
5. creates order
6. books pickup window
7. sends SMS summary and ends the call

## Notes

- Transcript entries are appended to `TRANSCRIPT_DIR` (default `transcripts/`).
- Keep responses spoken-friendly and short for telephony quality.
- SMS delivery depends on Vonage account having active SMS service and sufficient credits.
