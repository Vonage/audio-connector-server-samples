# N8N setup for vonage-ai-triage-nurse

N8N runs orchestration: transcript, availability, scheduling, SMS, and doctor notify.

## Quick setup

### 1. Configure N8N env

```bash
cp env.example .env
```

Set in `.env`:

1. N8N_ENCRYPTION_KEY
2. SMS_API_KEY
3. SMS_API_SECRET
4. SMS_FROM

SMS values are from https://dashboard.vonage.com -> API Settings and Sender IDs.
This workflow sends real SMS through Vonage SMS API.

### 2. Start N8N

```bash
docker compose up -d
```

Open http://localhost:5678

### 3. First-time setup (only once)

On first open, N8N asks you to create a local account:

1. Enter your email and a password and click Get Started.
2. You can skip the optional onboarding steps.

### 4. Import workflow

1. On the N8N home screen, click the **+** button (top right) or go to **Workflows → Add workflow**.
2. Inside the empty workflow editor, click the **...** menu (top right) → **Import from file**.
3. Choose `n8n/workflows/vonage-ai-triage-nurse.workflow.json`.
4. The workflow nodes will appear in the canvas.
5. Click **Publish** (top right, replaces Save/Activate in newer N8N versions) to make webhooks live.

Note: the toggle next to Publish shows Active/Inactive status of the workflow.

## Required webhook paths

1. triage/transcript
2. triage/availability
3. triage/schedule
4. triage/sms
5. triage/doctor-notify

## SMS node check

In the workflow, the SMS node should be `Send SMS via Vonage` (HTTP Request node), and `Respond SMS` should return:

1. `success: true`
2. `sent: true` when Vonage status is `0`
3. `message_uuid`
4. `to`, `from`, `text`, `status`

## Verify

1. Run app server from project root: uv run server.py
2. Trigger /connect and speak in the connected Vonage session
3. Confirm N8N executions exist
4. Confirm `triage/sms` response has `sent: true`
5. Confirm `triage/doctor-notify` response has `doctor_join_url` (dummy is fine)

