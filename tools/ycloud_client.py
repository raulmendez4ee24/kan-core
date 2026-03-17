from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

from observability import capture_exception

logger = logging.getLogger("kan_core.ycloud")

_YCLOUD_BASE_URL = "https://api.ycloud.com/v2"
_MAX_TEXT_LENGTH = 4096
_YCLOUD_FROM_CACHE: Optional[str] = None


def _api_key() -> Optional[str]:
    return (os.getenv("YCLOUD_API_KEY") or "").strip() or None


def _enabled() -> bool:
    explicit = os.getenv("ENABLE_YCLOUD_REPLY", "").strip().lower()
    if explicit in {"0", "false", "no"}:
        return False
    return bool(_api_key())


def _sanitize_text(text: str) -> str:
    token = str(text or "").replace("\x00", "").strip()
    if not token:
        return ""
    if len(token) > _MAX_TEXT_LENGTH:
        token = token[: _MAX_TEXT_LENGTH - 3] + "..."
    return token


async def ycloud_request(
    method: str,
    path: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    api_key = _api_key()
    if not api_key:
        logger.warning("YCLOUD_API_KEY not configured — skipping request")
        return None

    base_url = (os.getenv("YCLOUD_BASE_URL") or _YCLOUD_BASE_URL).rstrip("/")
    url = f"{base_url}{path}"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    if path == "/whatsapp/messages/sendDirectly":
        logger.info(
            "YCloud outbound request: method=%s url=%s payload=%s",
            method.upper(),
            url,
            payload,
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.request(method.upper(), url, json=payload, headers=headers)
            response.raise_for_status()
            if not response.content:
                if path == "/whatsapp/messages/sendDirectly":
                    logger.info("YCloud outbound response: {}")
                return {}
            response_payload = response.json()
            if path == "/whatsapp/messages/sendDirectly":
                logger.info("YCloud outbound response: %s", response_payload)
            return response_payload
    except Exception as exc:
        capture_exception(exc, {"source": "ycloud_client", "url": url})
        logger.exception("YCloud API request failed payload=%s", payload)
        return None


async def send_ycloud_whatsapp_text_message(
    *,
    from_number: str,
    to: str,
    text: str,
) -> Optional[Dict[str, Any]]:
    if not _enabled():
        return None

    sender = str(from_number or os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
    recipient = str(to or "").strip()
    body = _sanitize_text(text)
    if not sender or not recipient or not body:
        logger.warning("send_ycloud_whatsapp_text_message: missing sender, recipient, or body")
        return None

    payload: Dict[str, Any] = {
        "from": sender,
        "to": recipient,
        "type": "text",
        "text": {"body": body},
    }
    return await ycloud_request("POST", "/whatsapp/messages/sendDirectly", payload=payload)


async def resolve_ycloud_whatsapp_from_number() -> Optional[str]:
    global _YCLOUD_FROM_CACHE
    cached = str(_YCLOUD_FROM_CACHE or os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
    if cached:
        _YCLOUD_FROM_CACHE = cached
        return cached

    response = await ycloud_request("GET", "/whatsapp/phoneNumbers")
    items = list((response or {}).get("items") or [])
    for item in items:
        status = str(item.get("status") or "").strip().upper()
        phone_number = str(item.get("phoneNumber") or item.get("displayPhoneNumber") or "").strip()
        if status == "CONNECTED" and phone_number:
            _YCLOUD_FROM_CACHE = phone_number
            return phone_number
    if items:
        phone_number = str(items[0].get("phoneNumber") or items[0].get("displayPhoneNumber") or "").strip()
        if phone_number:
            _YCLOUD_FROM_CACHE = phone_number
            return phone_number
    return None


async def send_ycloud_whatsapp_template_message(
    *,
    from_number: str,
    to: str,
    template_name: str,
    body_variables: list[str],
    language: str = "es",
) -> Optional[Dict[str, Any]]:
    if not _enabled():
        return None

    sender = str(from_number or "").strip() or await resolve_ycloud_whatsapp_from_number()
    recipient = str(to or "").strip()
    variables = [_sanitize_text(value) for value in body_variables]
    if not sender or not recipient or not template_name or not all(variables):
        logger.warning(
            "send_ycloud_whatsapp_template_message: missing sender, recipient, template, or body variables"
        )
        return None

    payload: Dict[str, Any] = {
        "from": sender,
        "to": recipient,
        "type": "template",
        "template": {
            "name": str(template_name).strip(),
            "language": {"code": str(language or 'es').strip() or "es"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": value} for value in variables],
                }
            ],
        },
    }
    return await ycloud_request("POST", "/whatsapp/messages/sendDirectly", payload=payload)
