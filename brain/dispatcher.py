import logging
import os
from typing import Any, Dict, Optional

from brain.core import CognitiveEngine
from observability import capture_exception
from tools.n8n_client import send_to_n8n
from tools.ycloud_client import send_ycloud_whatsapp_text_message

logger = logging.getLogger("kan_core.dispatcher")


async def handle_meta_event(payload: Dict[str, Any]) -> None:
    """Entry point for Meta (WhatsApp/Instagram) events."""
    # TODO: parse messages, load client/persona, route to LLM(s)
    try:
        await send_to_n8n(payload)
    except Exception as exc:
        logger.exception("Failed to handle Meta event")
        capture_exception(exc, {"source": "meta"})


def _extract_ycloud_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    return dict(payload.get("whatsappInboundMessage") or payload.get("data") or {})


def _extract_ycloud_text(message: Dict[str, Any]) -> str:
    message_type = str(message.get("type") or "").strip().lower()
    if message_type == "text":
        return str(((message.get("text") or {}).get("body") or "")).strip()
    return str(message.get("text") or "").strip()


def _resolve_ycloud_client_id(_: Dict[str, Any]) -> Optional[str]:
    token = str(os.getenv("YCLOUD_DEFAULT_CLIENT_ID") or "").strip()
    return token or None


async def handle_ycloud_event(payload: Dict[str, Any]) -> None:
    supported_event_types = {
        "whatsapp.inbound.message",
        "whatsapp.inbound_message.received",
    }
    event_type = str(payload.get("type") or payload.get("eventType") or "").strip()
    if event_type and event_type not in supported_event_types:
        return

    message = _extract_ycloud_message(payload)
    text = _extract_ycloud_text(message)
    sender = str(message.get("from") or "").strip()
    recipient = str(message.get("to") or "").strip()
    client_id = _resolve_ycloud_client_id(message)
    if not text or not sender or not recipient or not client_id:
        return

    try:
        result = await CognitiveEngine.get_instance().generate_response(
            client_id, f"whatsapp:{sender}", text
        )
        reply = str(result.get("content") or "").strip()
        if not reply and result.get("payload") is not None:
            reply = str(result.get("payload"))
        if not reply:
            return
        await send_ycloud_whatsapp_text_message(
            from_number=recipient,
            to=sender,
            text=reply,
        )
    except Exception as exc:
        logger.exception("Failed to handle YCloud event")
        capture_exception(exc, {"source": "ycloud", "sender": sender})


async def handle_client_event(client_id: str, payload: Dict[str, Any]) -> None:
    """Entry point for internal client webhooks (non-Meta)."""
    try:
        await send_to_n8n({"client_id": client_id, "data": payload})
    except Exception as exc:
        logger.exception("Failed to handle client event")
        capture_exception(exc, {"source": "client", "client_id": client_id})


async def handle_shopify_event(payload: Dict[str, Any], topic: str, shop: str) -> None:
    """Entry point for Shopify webhooks."""
    try:
        await send_to_n8n({"source": "shopify", "topic": topic, "shop": shop, "data": payload})
    except Exception as exc:
        logger.exception("Failed to handle Shopify event")
        capture_exception(exc, {"source": "shopify", "topic": topic, "shop": shop})
