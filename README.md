# Vonage Audio Connector Server — Sample Apps

This repository contains sample applications in separate folders. Use them to explore real-time audio streaming with the Vonage Video API (Audio Connector) and Vonage Voice API, powered by [Pipecat](https://github.com/pipecat-ai/pipecat).

## Repository structure

### Core / foundational samples

| Folder | Description |
|--------|-------------|
| [`unified_sample_app`](unified_sample_app/README.md) | Bidirectional real-time audio over WebSocket, streaming to and from Vonage (Video API + OpenTok). |
| [`echo_server_app`](echo_server_app/README.md) | Minimal WebSocket server that echoes received audio back — useful for testing the Audio Connector plumbing. |

### Pipecat bot samples

| Folder | Description |
|--------|-------------|
| [`vonage-audio-bot`](vonage-audio-bot/README.md) | Conversational STT → LLM → TTS bot supporting both **Vonage Video API** (Audio Connector) and **Vonage Voice API** (phone calls). |
| [`vonage-ac-s2s`](vonage-ac-s2s/README.md) | Speech-to-speech bot using **OpenAI Realtime** (audio-in / audio-out) connected via Vonage Audio Connector — no separate STT/TTS step. |
| [`vonage-ac-translation-bot`](vonage-ac-translation-bot/README.md) | Live conference translation: translates speech from one language to another in real time and injects the translated audio back into the same Vonage session. |
| [`vonage-voice-appointment-bot`](vonage-voice-appointment-bot/README.md) | Phone-based appointment booking agent: callers can check availability, book, and cancel appointments over a Vonage Voice API call. |
| [`vonage-shoe-store-audio-bot`](vonage-shoe-store-audio-bot/README.md) | Shoe-store voice workflow bot (Video API / Audio Connector) that captures customer details, checks inventory, creates orders, reserves a pickup window, and sends confirmation SMS via n8n webhooks. |
| [`vonage-ai-triage-nurse`](vonage-ai-triage-nurse/README.md) | AI medical triage nurse (Video API / Audio Connector) that collects patient information, checks doctor availability, schedules appointments, and sends SMS notifications — all orchestrated by n8n. |
| [`vonage-voice-mcp-server`](vonage-voice-mcp-server/voice-agent/README.md) | Voice AI assistant (Voice API) that answers KB questions and manages appointments/support tickets using MCP-style HTTP tools; ships with a companion [MCP tool server](vonage-voice-mcp-server/mcp-tool-server/README.md). |

## Common requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager (Pipecat samples)
- Vonage account credentials — Video Application (Application ID + Private Key) **or** OpenTok credentials (API key + secret), depending on the sample
- OpenAI API key (Pipecat samples)
- ngrok (or any HTTPS tunnel) for local development

## Getting started

1. Pick a sample folder from the table above and open its `README.md` for detailed setup instructions.
2. Copy `env.example` to `.env` and fill in your credentials.
3. Start the server and point Vonage (or ngrok) at it.

> **Tip:** Every Pipecat sample uses `uv` — run `uv sync` inside the sample folder to install dependencies into an isolated virtual environment.
