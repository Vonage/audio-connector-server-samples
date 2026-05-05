# Vonage Voice Appointment Booking Agent

A voice agent that helps callers **check availability**, **book appointments**, and **cancel appointments** over the phone using Pipecat and Vonage Voice API.

## Features

- **Check availability** — Callers can ask "What times are available tomorrow?"
- **Book appointments** — "I'd like to book a 10 AM appointment for John tomorrow"
- **Cancel appointments** — "Cancel my appointment for today"
- **End call** — The agent can end the call when the caller says goodbye

Available time slots: 9:00 AM, 10:00 AM, 11:00 AM, 2:00 PM, 3:00 PM, 4:00 PM.

## Architecture

```
Phone Call
        |
        |  (Voice API)
        v
GET /answer  ─────────▶ Vonage Cloud
                             |
                             |  wss://<your-server-domain>/ws
                             v
                    FastAPI WebSocket (/ws)
                             |
                             v
                        Pipecat Pipeline
                  STT → LLM (with tools) → TTS → Audio

```

The bot uses **LLM function calling** to invoke tools: `check_availability`, `book_appointment`, `cancel_appointment`, and `end_call`. Appointments are stored in memory for the demo (resets on server restart).

## Prerequisites

- Python 3.10+
- `uv` package manager
- ngrok (for local development)
- Vonage account with a Voice-capable application and linked phone number

## Setup

1. **Install dependencies**

   ```bash
   uv sync
   ```

2. **Configure environment**

   ```bash
   cp env.example .env
   # Edit .env with OPENAI_API_KEY, WS_URI, VONAGE_VOICE_FROM_NUMBER
   ```

3. **Vonage Application**

   - Create a Vonage Application with **Voice** capability
   - Purchase a number at [dashboard.vonage.com/numbers/your-numbers](https://dashboard.vonage.com/numbers/your-numbers) and link it
   - Set **Answer URL:** `https://<your-server-domain>/answer` (HTTP GET)
   - Set **Event URL:** `https://<your-server-domain>/events` (HTTP POST)
   - For local development, `<your-server-domain>` is typically your ngrok domain (e.g. `abc123.ngrok.io`).

## Running

1. **Start the server**

   ```bash
   uv run server.py
   ```

   Server runs on port **8005**.

2. **Expose with ngrok**

   ```bash
   ngrok http 8005
   ```

3. **Update `.env`**

   ```bash
   WS_URI=wss://<your-server-domain>/ws
   VONAGE_VOICE_FROM_NUMBER=190458XXXXX
   ```

   For local development, use your ngrok domain (e.g. `WS_URI=wss://abc123.ngrok.io/ws`).

4. **Call your linked number** — The appointment booking agent will answer.

## Example conversation

- **Caller:** "Hi, I'd like to book an appointment."
- **Agent:** "Hello! This is the appointment booking line. How can I help you today?"
- **Caller:** "What's available tomorrow?"
- **Agent:** "Available slots for tomorrow: 9:00 AM, 10:00 AM, 11:00 AM, 2:00 PM, 3:00 PM, 4:00 PM."
- **Caller:** "Book 10 AM for John Smith, general checkup."
- **Agent:** "Your appointment is confirmed for [date] at 10:00 AM. Reason: general checkup."

## References

- [Vonage Voice API call flow](https://developer.vonage.com/en/voice/voice-api/concepts/call-flow?source=voice)
- [Vonage WebSocket tutorial](https://developer.vonage.com/en/tutorials/connect-to-a-websocket/introduction/python)
