# This is the separate “MCP tool server” (easy mock). You can later swap internals with Google Calendar / M365 / Zendesk / Jira.

import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, Field
from storage import CalendarStore, KnowledgeStore, TicketStore

load_dotenv(override=True)

app = FastAPI()

calendar = CalendarStore()
tickets = TicketStore()
knowledge = KnowledgeStore(kb_dir=os.getenv("KB_DIR", "../voice-agent/kb"))

DEFAULT_SLOTS = ["9:00 AM", "10:00 AM", "11:00 AM", "2:00 PM", "3:00 PM", "4:00 PM"]


@app.get("/health")
def health():
    return {"ok": True}


class FindSlotsRequest(BaseModel):
    date: str
    timezone: str = "Asia/Kolkata"
    duration_mins: int = 30


@app.post("/tools/calendar/find_slots")
def find_slots(req: FindSlotsRequest):
    # simple demo: all slots minus booked slots for that date
    booked = set(calendar.booked_slots(req.date))
    available = [s for s in DEFAULT_SLOTS if s not in booked]
    return {
        "success": True,
        "date": req.date,
        "available_slots": available,
        "message": f"Available slots for {req.date}: {', '.join(available) if available else 'none'}",
    }


class BookRequest(BaseModel):
    name: str
    date: str
    time_slot: str
    reason: str
    phone: str = ""
    timezone: str = "Asia/Kolkata"


@app.post("/tools/calendar/book")
def book(req: BookRequest):
    if req.time_slot not in DEFAULT_SLOTS:
        return {
            "success": False,
            "message": f"Invalid time slot. Valid: {', '.join(DEFAULT_SLOTS)}",
        }

    booking_id = calendar.try_create_booking(
        name=req.name,
        date=req.date,
        time_slot=req.time_slot,
        reason=req.reason,
        phone=req.phone,
        timezone=req.timezone,
    )

    if not booking_id:
        return {
            "success": False,
            "message": f"Sorry, {req.time_slot} is no longer available. Choose another slot.",
        }

    return {
        "success": True,
        "booking_id": booking_id,
        "message": f"Booked for {req.date} at {req.time_slot}. Your booking id is {booking_id}.",
    }


class CancelRequest(BaseModel):
    booking_id: str = ""
    name: str = ""
    date: str = ""


@app.post("/tools/calendar/cancel")
def cancel(req: CancelRequest):
    if req.booking_id:
        ok = calendar.cancel_by_id(req.booking_id)
        if ok:
            return {"success": True, "message": f"Cancelled booking {req.booking_id}."}
        return {"success": False, "message": f"Booking id {req.booking_id} not found."}

    if req.name and req.date:
        booking_id = calendar.find_booking_id(req.name, req.date)
        if not booking_id:
            return {"success": False, "message": f"No booking found for {req.name} on {req.date}."}
        calendar.cancel_by_id(booking_id)
        return {"success": True, "message": f"Cancelled booking for {req.name} on {req.date}."}

    return {"success": False, "message": "Provide booking_id or (name and date) to cancel."}


class TicketRequest(BaseModel):
    caller_name: str = "Caller"
    phone: str = ""
    summary: str
    transcript: str = ""
    priority: str = "normal"


@app.post("/tools/ticket/create")
def create_ticket(req: TicketRequest):
    ticket_id = tickets.create(
        caller_name=req.caller_name,
        phone=req.phone,
        summary=req.summary,
        transcript=req.transcript,
        priority=req.priority,
    )
    return {"success": True, "ticket_id": ticket_id, "message": f"Ticket created: {ticket_id}."}


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    k: int = Field(4, ge=1)


@app.post("/tools/kb/search")
def search_kb(req: KnowledgeSearchRequest):
    results = knowledge.search(req.query, req.k)
    return {
        "success": True,
        "snippets": results,
        "message": f"Found {len(results)} relevant knowledge snippets.",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8010")))
