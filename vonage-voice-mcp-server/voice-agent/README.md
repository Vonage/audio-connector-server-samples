# Vonage Voice MCP Tool Server Agent

Reference sample for building a voice AI assistant on Vonage Voice API with Pipecat and MCP-style tools.

This project demonstrates how to:
- answer KB questions via an MCP tool,
- check availability / book / cancel appointments,
- create support tickets for human follow-up.

## Why this sample uses mock tools

This repository intentionally keeps the MCP backend mock/in-memory so developers can run it in minutes without creating third-party accounts.

For production, keep the same MCP endpoints and replace backend internals with real providers (Google Calendar, Microsoft Graph, Zendesk, Jira, ServiceNow, etc.).

## Architecture

Phone Call -> Vonage Voice API -> `/answer` -> websocket `/ws` -> Pipecat pipeline  
STT -> LLM (tool-calling) -> MCP HTTP tools -> TTS

### Tool mapping

- `retrieve_kb` -> MCP `POST /tools/kb/search`
- `mcp_find_slots` -> MCP `POST /tools/calendar/find_slots`
- `mcp_book_appointment` -> MCP `POST /tools/calendar/book`
- `mcp_cancel_appointment` -> MCP `POST /tools/calendar/cancel`
- `mcp_create_ticket` -> MCP `POST /tools/ticket/create`

## End-to-end flow (simple)

1. Caller asks a question.
2. Agent calls `retrieve_kb`.
3. MCP server reads markdown docs from `KB_DIR` and returns top snippets.
4. Agent answers using returned snippets.
5. If caller asks to book/cancel, agent calls calendar endpoints.
6. If unresolved, agent creates a support ticket.

## Prerequisites

- Python 3.10+
- uv
- ngrok (for local testing)
- Vonage Voice app + linked number

Purchase a number in the Vonage Dashboard and link it to your Voice application:
- [Vonage Dashboard: Your Numbers](https://dashboard.vonage.com/numbers/your-numbers)

## Project layout

- `voice-agent/` (Vonage webhook + Pipecat voice bot)
- `mcp-tool-server/` (mock MCP tools: KB, calendar, ticket)
- `voice-agent/kb/` (knowledge documents used by MCP KB search)

## Setup

### 1) Start MCP tool server

From `mcp-tool-server/`:

```bash
uv sync
cp env.example .env
uv run server.py
```

Default MCP URL: `http://127.0.0.1:8010`

### 2) Start voice agent

From `voice-agent/`:

```bash
uv sync
cp env.example .env
# Fill OPENAI_API_KEY, WS_URI, VONAGE_VOICE_FROM_NUMBER
uv run server.py
```

### 3) Expose agent to Vonage with ngrok

```bash
ngrok http 8005
```

Set in `voice-agent/.env`:

```bash
WS_URI=wss://<your-ngrok-domain>/ws
```

### 4) Configure Vonage Voice webhooks

- Answer URL: `https://<domain>/answer` (GET)
- Event URL: `https://<domain>/events` (POST)

## Simple MCP API examples

Search KB:

```bash
curl -X POST http://127.0.0.1:8010/tools/kb/search \
  -H "content-type: application/json" \
  -d '{"query":"cancellation policy","k":3}'
```

Find slots:

```bash
curl -X POST http://127.0.0.1:8010/tools/calendar/find_slots \
  -H "content-type: application/json" \
  -d '{"date":"tomorrow","timezone":"Asia/Kolkata","duration_mins":30}'
```

Book slot:

```bash
curl -X POST http://127.0.0.1:8010/tools/calendar/book \
  -H "content-type: application/json" \
  -d '{"name":"Varun","date":"2025-03-15","time_slot":"3:00 PM","reason":"Consultation"}'
```

## Production extension points

Keep the MCP contracts stable and change only internals in `mcp-tool-server/`:

- `CalendarStore` in `storage.py` -> replace with real scheduling provider adapter
- `TicketStore` in `storage.py` -> replace with ticketing adapter
- `KnowledgeStore` in `storage.py` -> replace with semantic/vector retrieval if needed

This preserves the voice-agent behavior while upgrading backend capabilities.

## Example conversation

Caller: "What is your cancellation policy?"  
Agent: (calls `retrieve_kb`) "We allow cancellation up to 24 hours before the appointment..."

Caller: "What slots are available tomorrow?"  
Agent: (calls `mcp_find_slots`) "Available slots tomorrow are ..."

Caller: "Book 3 PM tomorrow for Varun, consultation."  
Agent: (checks availability, then books) "Confirmed. Your booking ID is ..."

Caller: "I still need help, can someone call me back?"  
Agent: (calls `mcp_create_ticket`) "I created a ticket, reference ..."

## References

- [Vonage Voice API call flow](https://developer.vonage.com/en/voice/voice-api/concepts/call-flow?source=voice)
- [Vonage WebSocket tutorial (Python)](https://developer.vonage.com/en/tutorials/connect-to-a-websocket/introduction/python)

