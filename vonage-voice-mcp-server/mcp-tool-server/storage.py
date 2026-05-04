import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class CalendarStore:
    def __init__(self):
        # booking_id -> booking dict
        self.bookings: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def booked_slots(self, date_str: str) -> List[str]:
        with self._lock:
            out = []
            for b in self.bookings.values():
                if b.get("date") == date_str:
                    out.append(b.get("time_slot", ""))
            return [s for s in out if s]

    def is_slot_taken(self, date_str: str, time_slot: str) -> bool:
        with self._lock:
            for b in self.bookings.values():
                if b.get("date") == date_str and b.get("time_slot") == time_slot:
                    return True
            return False

    def create_booking(
        self, name: str, date: str, time_slot: str, reason: str, phone: str, timezone: str
    ) -> str:
        with self._lock:
            booking_id = "BKG-" + uuid.uuid4().hex[:10].upper()
            self.bookings[booking_id] = {
                "booking_id": booking_id,
                "name": name,
                "date": date,
                "time_slot": time_slot,
                "reason": reason,
                "phone": phone,
                "timezone": timezone,
            }
            return booking_id

    def try_create_booking(
        self, name: str, date: str, time_slot: str, reason: str, phone: str, timezone: str
    ) -> Optional[str]:
        """Atomically create a booking only if the slot is currently free."""
        with self._lock:
            for b in self.bookings.values():
                if b.get("date") == date and b.get("time_slot") == time_slot:
                    return None

            booking_id = "BKG-" + uuid.uuid4().hex[:10].upper()
            self.bookings[booking_id] = {
                "booking_id": booking_id,
                "name": name,
                "date": date,
                "time_slot": time_slot,
                "reason": reason,
                "phone": phone,
                "timezone": timezone,
            }
            return booking_id

    def cancel_by_id(self, booking_id: str) -> bool:
        with self._lock:
            if booking_id in self.bookings:
                del self.bookings[booking_id]
                return True
            return False

    def find_booking_id(self, name: str, date: str) -> str:
        with self._lock:
            for booking_id, b in self.bookings.items():
                if b.get("name", "").lower() == name.lower() and b.get("date") == date:
                    return booking_id
            return ""


class TicketStore:
    def __init__(self):
        self.tickets: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(
        self, caller_name: str, phone: str, summary: str, transcript: str, priority: str
    ) -> str:
        with self._lock:
            ticket_id = "TCK-" + uuid.uuid4().hex[:10].upper()
            self.tickets[ticket_id] = {
                "ticket_id": ticket_id,
                "caller_name": caller_name,
                "phone": phone,
                "summary": summary,
                "transcript": transcript,
                "priority": priority,
            }
            return ticket_id


class KnowledgeStore:
    def __init__(self, kb_dir: str):
        self.kb_dir = Path(kb_dir)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 1000, overlap: int = 120) -> List[str]:
        text = (text or "").strip()
        if not text:
            return []

        chunks: List[str] = []
        i = 0
        while i < len(text):
            j = min(len(text), i + max_chars)
            chunk = text[i:j].strip()
            if chunk:
                chunks.append(chunk)
            if j == len(text):
                break
            i = max(i + 1, j - overlap)
        return chunks

    def search(self, query: str, k: int = 4) -> List[Dict[str, Any]]:
        if k <= 0:
            return []

        q_tokens = self._tokenize(query)
        if not q_tokens or not self.kb_dir.exists():
            return []

        scored: List[Dict[str, Any]] = []
        for f in sorted(self.kb_dir.glob("*.md")):
            content = f.read_text(encoding="utf-8", errors="replace")
            for chunk in self._chunk_text(content):
                c_tokens = self._tokenize(chunk)
                if not c_tokens:
                    continue
                overlap = q_tokens.intersection(c_tokens)
                if not overlap:
                    continue
                score = len(overlap) / max(1, len(q_tokens))
                scored.append(
                    {
                        "text": chunk,
                        "source": f.name,
                        "score": round(score, 4),
                    }
                )

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]
