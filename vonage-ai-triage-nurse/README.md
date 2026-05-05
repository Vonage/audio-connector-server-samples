# vonage-ai-triage-nurse

Pipecat voice triage nurse using Vonage Connect API and N8N automation.

## What this runs

1. Use a Vonage session (for example created in Playground).
2. Call `/connect` to bridge session audio to the Pipecat bot websocket.
3. AI triage nurse asks questions over voice.
4. N8N handles availability lookup, scheduling, SMS, and doctor notification.
5. SMS is sent through Vonage SMS API from N8N; doctor notification returns a doctor join URL payload.
6. Default patient and doctor details are loaded from `.env`.
7. Before scheduling, the bot reads back the captured phone number and requires explicit patient confirmation.
8. Appointment booking is future-only: later-today bookings are allowed if the requested time is still in the future, while past date/time requests are rejected and the bot asks again.

## Prerequisites

1. Python 3.10+
2. uv
3. Docker
4. ngrok (for local development)
5. OpenAI API key
6. Vonage Video Application **or** OpenTok project credentials
7. Vonage session ID
8. Public URL for this server (ngrok or deployed)

## Quick setup

### 1. App env

```bash
cp env.example .env
```

Get your credentials (choose one option):

**Option A — Vonage Video Application**
- Copy Vonage Application ID → `VONAGE_APPLICATION_ID`
- Download or copy Private Key → `VONAGE_PRIVATE_KEY`

**Option B — OpenTok Project**
- From your OpenTok project: API Key → `OPENTOK_API_KEY`, API Secret → `OPENTOK_API_SECRET`

**Session ID** — Create a routed session in [Vonage Playground](https://tools.vonage.com/video/playground).

Set in `.env`:

```env
OPENAI_API_KEY=YOUR_OPENAI_API_KEY

# Option A
VONAGE_APPLICATION_ID=YOUR_APPLICATION_ID
VONAGE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"

# OR Option B
OPENTOK_API_KEY=YOUR_API_KEY
OPENTOK_API_SECRET=YOUR_API_SECRET

VONAGE_SESSION_ID=YOUR_SESSION_ID
TRIAGE_DOCTOR_NAME=Dr. Demo
TRIAGE_DOCTOR_PHONE=+10000000001
TRIAGE_DOCTOR_SPECIALITY=General Medicine
DOCTOR_JOIN_URL=
WS_URI=wss://your-public-domain/ws
PORT=8012
```

Notes:
1. `VONAGE_SESSION_ID` can come from Playground.
2. `WS_URI` must be a public URL. Vonage cannot connect to localhost — use ngrok for local dev:

```bash
ngrok http 8005
```

Copy the ngrok URL (e.g. `https://abc123.ngrok.io`) and update `.env`:

```env
WS_URI=wss://abc123.ngrok.io/ws
```
3. `DOCTOR_JOIN_URL` is optional. If left blank, the doctor notification falls back to `Playground session: <session_id>`.
4. `TRIAGE_DOCTOR_*` values are used for doctor notifications.
5. Transcript entries are also saved locally as text files in `TRANSCRIPT_DIR` (default: `transcripts/triage_<session>.txt`).

### 2. N8N env

```bash
cp n8n/env.example n8n/.env
```

Set in `n8n/.env`:

```env
N8N_ENCRYPTION_KEY=any-long-random-string
SMS_API_KEY=YOUR_VONAGE_API_KEY
SMS_API_SECRET=YOUR_VONAGE_API_SECRET
SMS_FROM=Vonage APIs
```

Notes:
1. `N8N_ENCRYPTION_KEY` can be any long random string, e.g. `openssl rand -hex 32`.
2. `SMS_API_KEY` and `SMS_API_SECRET` are your Vonage dashboard API key and secret (not application credentials).
3. `SMS_FROM` is your registered sender ID or alphanumeric label. `Vonage APIs` works in most countries.

### 3. Start N8N and import workflow

```bash
docker compose -f n8n/docker-compose.yml --env-file n8n/.env up -d
```

1. Open http://localhost:5678
2. Register a local account on first open (enter email + password and click **Get started**).
3. Click **+** at the top right to create a new workflow.
4. Inside the canvas, click the **...** menu (top right) → **Import from file**.
5. Choose [n8n/workflows/vonage-ai-triage-nurse.workflow.json](n8n/workflows/vonage-ai-triage-nurse.workflow.json) from this repo.
6. Click **Publish** (top right) — this saves and activates the webhooks.

Need only N8N details? See [n8n/README.md](n8n/README.md).

### 4. Run app

```bash
uv sync
uv run server.py
```

### 5. Trigger voice bridge

```bash
curl -X POST http://localhost:8005/connect \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## Verify

1. `/connect` returns `status: connect_triggered`.
2. Speak in the connected Vonage session.
3. AI triage nurse asks questions and confirms appointment details.
4. Confirm N8N executions for transcript, availability, schedule, SMS, and doctor-notify.
5. Confirm `triage/sms` response includes `sent: true` and appointment text.
6. Confirm `triage/doctor-notify` response includes `doctor_join_url` (dummy URL is acceptable).

## Common issues

1. App fails: check OPENAI_API_KEY, OPENTOK_API_KEY, OPENTOK_API_SECRET.
2. Connect fails: WS_URI is not public or VONAGE_SESSION_ID is invalid.
3. No N8N calls: confirm workflow is active and webhook URLs in `.env` are correct.
4. No SMS webhook result: confirm N8N workflow import is up to date and active.
