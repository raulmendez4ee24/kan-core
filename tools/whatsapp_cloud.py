import os
from typing import Any, Dict, Optional

from observability import capture_exception
from tools.meta_client import meta_request


def _enabled() -> bool:
    return os.getenv("ENABLE_META_WHATSAPP_REPLY", "false").lower() in {"1", "true", "yes"}


def _access_token() -> str:
    return (os.getenv("META_WHATSAPP_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN") or "").strip()


def _sanitize_text(text: str) -> str:
    token = str(text or "").replace("\x00", "").strip()
    if not token:
        return ""
    # WhatsApp text body limit is 4096 chars.
    if len(token) > 4096:
        token = token[:4093] + "..."
    return token


async def send_whatsapp_text_message(
    *,
    phone_number_id: str,
    to: str,
    text: str,
    preview_url: bool = False,
) -> Optional[Dict[str, Any]]:
    if not _enabled():
        return None
    token = _access_token()
    if not token:
        return None

    phone_number_id = str(phone_number_id or "").strip()
    to = str(to or "").strip()
    body = _sanitize_text(text)
    if not phone_number_id or not to or not body:
        return None

    payload: Dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body, "preview_url": bool(preview_url)},
    }
    try:
        return await meta_request(
            "POST",
            f"/{phone_number_id}/messages",
            access_token=token,
            payload=payload,
        )
    except Exception as exc:
        capture_exception(
            exc,
            {
                "service": "whatsapp_cloud",
                "phone_number_id": phone_number_id,
            },
        )
        return None

