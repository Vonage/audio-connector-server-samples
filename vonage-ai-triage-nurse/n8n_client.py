import os
from typing import Any

import httpx


class N8NClient:
    def __init__(self) -> None:
        self.transcript_webhook = os.getenv("N8N_TRANSCRIPT_WEBHOOK", "").strip()
        self.availability_webhook = os.getenv("N8N_AVAILABILITY_WEBHOOK", "").strip()
        self.schedule_webhook = os.getenv("N8N_SCHEDULE_WEBHOOK", "").strip()
        self.sms_webhook = os.getenv("N8N_SMS_WEBHOOK", "").strip()
        self.doctor_notify_webhook = os.getenv("N8N_DOCTOR_NOTIFY_WEBHOOK", "").strip()

    async def _post(
        self, url: str, payload: dict[str, Any], fallback: dict[str, Any]
    ) -> dict[str, Any]:
        if not url:
            return {**fallback, "success": False, "fallback": True}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict):
                    return data
                return {"success": True, "raw": data}
        except Exception as exc:
            return {
                **fallback,
                "success": False,
                "fallback": True,
                "error": str(exc),
            }

    async def save_transcript_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.transcript_webhook,
            payload,
            {
                "success": True,
                "fallback": True,
                "message": "Transcript saved locally because N8N transcript webhook is not configured.",
            },
        )

    async def lookup_availability(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.availability_webhook,
            payload,
            {
                "success": True,
                "fallback": True,
                "available_slots": [
                    {
                        "date": payload.get("preferred_date") or "next-business-day",
                        "time": "10:30 AM",
                    },
                    {
                        "date": payload.get("preferred_date") or "next-business-day",
                        "time": "2:00 PM",
                    },
                ],
            },
        )

    async def schedule_appointment(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.schedule_webhook,
            payload,
            {
                "success": True,
                "fallback": True,
                "appointment_id": f"APT-{payload.get('triage_session_id', 'DEMO')[:8].upper()}",
                "date": payload.get("date", "next-business-day"),
                "time": payload.get("time", "10:30 AM"),
            },
        )

    async def send_sms(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.sms_webhook,
            payload,
            {
                "success": True,
                "fallback": True,
                "message": "SMS webhook not configured. Message generated but not sent.",
            },
        )

    async def notify_doctor(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.doctor_notify_webhook,
            payload,
            {
                "success": True,
                "fallback": True,
                "message": "Doctor notification webhook not configured. URL generated locally.",
            },
        )
