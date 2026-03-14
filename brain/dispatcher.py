import logging
from typing import Any, Dict

from observability import capture_exception
from tools.n8n_client import send_to_n8n

logger = logging.getLogger("kan_core.dispatcher")


async def handle_meta_event(payload: Dict[str, Any]) -> None:
    """Entry point for Meta (WhatsApp/Instagram) events."""
    # TODO: parse messages, load client/persona, route to LLM(s)
    try:
        await send_to_n8n(payload)
    except Exception as exc:
        logger.exception("Failed to handle Meta event")
        capture_exception(exc, {"source": "meta"})


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
