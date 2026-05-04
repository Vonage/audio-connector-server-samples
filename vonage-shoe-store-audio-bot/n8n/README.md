# N8N setup for vonage-shoe-store-audio-bot

N8N runs workflow automation for transcript logging, inventory response, order creation, pickup booking, and SMS confirmation.

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

### 2. Start N8N

```bash
docker compose up -d
```

Open http://localhost:5678

### 3. First-time setup (only once)

1. Create local login credentials.
2. Skip optional onboarding.

### 4. Import workflow

1. Create a new workflow in n8n.
2. Use import from file.
3. Choose `n8n/workflows/vonage-shoe-store-audio-bot.workflow.json`.
4. Publish the workflow to activate webhooks.

## Required webhook paths

1. shoe/transcript
2. shoe/inventory
3. shoe/order
4. shoe/pickup
5. shoe/sms

## Verify

1. Run app server from project root: `uv run server.py`
2. Trigger `/connect` and speak in the connected Vonage session
3. Confirm n8n executions exist for each webhook
4. Confirm `shoe/sms` response has `sent: true` when Vonage status is `0`
