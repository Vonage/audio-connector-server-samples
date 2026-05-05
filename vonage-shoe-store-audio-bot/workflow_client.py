import os
from pathlib import Path
from typing import Any

import httpx


def _load_n8n_env_file() -> dict[str, str]:
    env_path = Path("n8n/.env")
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_sms_config() -> tuple[str, str, str]:
    api_key = os.getenv("SMS_API_KEY", "").strip()
    api_secret = os.getenv("SMS_API_SECRET", "").strip()
    sms_from = os.getenv("SMS_FROM", "Vonage APIs").strip() or "Vonage APIs"

    if api_key and api_secret:
        return api_key, api_secret, sms_from

    n8n_env = _load_n8n_env_file()
    api_key = api_key or n8n_env.get("SMS_API_KEY", "")
    api_secret = api_secret or n8n_env.get("SMS_API_SECRET", "")
    sms_from = sms_from or n8n_env.get("SMS_FROM", "Vonage APIs")
    return api_key, api_secret, sms_from


async def _send_sms_direct(phone: str, message: str) -> dict[str, Any]:
    api_key, api_secret, sms_from = _resolve_sms_config()
    if not api_key or not api_secret:
        return {
            "success": False,
            "sent": False,
            "provider": "vonage-direct",
            "error": "Missing SMS_API_KEY/SMS_API_SECRET",
        }

    payload = {
        "api_key": api_key,
        "api_secret": api_secret,
        "to": phone,
        "from": sms_from,
        "text": message,
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post("https://rest.nexmo.com/sms/json", data=payload)
            response.raise_for_status()
            data = response.json() if response.text else {}
            first = (data.get("messages") or [{}])[0]
            sent = first.get("status") == "0"
            return {
                "success": sent,
                "sent": sent,
                "provider": "vonage-direct",
                "status": first.get("status", "unknown"),
                "error_text": first.get("error-text", ""),
                "message_uuid": first.get("message-id", ""),
                "to": first.get("to", phone),
                "from": sms_from,
                "raw": data,
            }
    except Exception as exc:
        return {
            "success": False,
            "sent": False,
            "provider": "vonage-direct",
            "error": str(exc),
        }


class ShoeWorkflowClient:
    def __init__(self) -> None:
        self.transcript_webhook = os.getenv("N8N_SHOE_TRANSCRIPT_WEBHOOK", "").strip()
        self.inventory_webhook = os.getenv("N8N_SHOE_INVENTORY_WEBHOOK", "").strip()
        self.order_webhook = os.getenv("N8N_SHOE_ORDER_WEBHOOK", "").strip()
        self.pickup_webhook = os.getenv("N8N_SHOE_PICKUP_WEBHOOK", "").strip()
        self.sms_webhook = os.getenv("N8N_SHOE_SMS_WEBHOOK", "").strip()

    async def _post(self, url: str, payload: dict[str, Any], workflow_name: str) -> dict[str, Any]:
        if not url:
            return {
                "success": False,
                "missing": True,
                "error": f"Missing required webhook URL for {workflow_name}",
            }

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
                "success": False,
                "unavailable": True,
                "workflow": workflow_name,
                "error": str(exc),
            }

    async def save_transcript_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.transcript_webhook,
            payload,
            "transcript",
        )

    async def check_inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.inventory_webhook,
            payload,
            "inventory",
        )

    async def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.order_webhook,
            payload,
            "order",
        )

    async def book_pickup(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            self.pickup_webhook,
            payload,
            "pickup",
        )

    async def send_sms(self, payload: dict[str, Any]) -> dict[str, Any]:
        webhook_result = await self._post(
            self.sms_webhook,
            payload,
            "sms",
        )

        if isinstance(webhook_result, dict) and webhook_result.get("sent") is True:
            return webhook_result

        phone = str(payload.get("phone", "")).strip()
        message = str(payload.get("message", "")).strip()
        if not phone or not message:
            return webhook_result

        direct_result = await _send_sms_direct(phone, message)
        if isinstance(direct_result, dict):
            direct_result["webhook_result"] = webhook_result
        return direct_result
