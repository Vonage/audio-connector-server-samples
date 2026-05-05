# MCP Tool Server (Mock)

Mock MCP-style backend used by the Vonage voice agent sample.

This service exposes HTTP tool endpoints for:
- knowledge search (`/tools/kb/search`)
- calendar availability + booking + cancellation
- support ticket creation

It is intentionally in-memory/mock so the full demo runs quickly without external accounts.

## Endpoints

- `GET /health`
- `POST /tools/kb/search`
- `POST /tools/calendar/find_slots`
- `POST /tools/calendar/book`
- `POST /tools/calendar/cancel`
- `POST /tools/ticket/create`

## Run

```bash
uv sync
cp env.example .env
uv run server.py
```

Default base URL: `http://127.0.0.1:8010`

## Environment variables

- `PORT` (default: `8010`)
- `KB_DIR` (default: `../voice-agent/kb`)

## Data behavior

- Calendar and ticket data are stored in memory only.
- Restarting the server clears bookings/tickets.
- KB search reads markdown files from `KB_DIR` and returns top matching chunks.

## Simple examples

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
  -d '{"name":"Varun","date":"tomorrow","time_slot":"3:00 PM","reason":"Consultation"}'
```

## Production replacement guide

Keep API contracts stable and replace internals in [storage.py](storage.py):
- `KnowledgeStore` -> semantic/vector retrieval service
- `CalendarStore` -> Google/Microsoft/Cal.com adapter
- `TicketStore` -> Zendesk/Jira/ServiceNow adapter

This lets the voice agent continue working unchanged while backend integrations become production-grade.
