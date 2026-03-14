"""
Telegram Cloud API client.
Sends messages via the Bot API using httpx (no extra SDK needed).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from observability import capture_exception

logger = logging.getLogger("kan_core.telegram_client")

_TELEGRAM_API_BASE = "https://api.telegram.org"
_MAX_TEXT_LENGTH = 4096


def _bot_token() -> Optional[str]:
    return (
        (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        or (os.getenv("TELEGRAM_TOKEN") or "").strip()
        or None
    )


def _enabled() -> bool:
    """Replies are ON by default when a token is set. Set ENABLE_TELEGRAM_REPLY=false to disable."""
    explicit = os.getenv("ENABLE_TELEGRAM_REPLY", "").strip().lower()
    if explicit in {"0", "false", "no"}:
        return False
    return bool(_bot_token())


def _sanitize_text(text: str) -> str:
    text = text.replace("\x00", "")
    if len(text) > _MAX_TEXT_LENGTH:
        text = text[: _MAX_TEXT_LENGTH - 3] + "..."
    return text


async def send_telegram_message(
    chat_id: str | int,
    text: str,
    parse_mode: Optional[str] = None,
) -> Optional[dict]:
    """
    Send a text message to a Telegram chat via the Bot API.

    Env vars required:
        TELEGRAM_BOT_TOKEN   — Bot token from @BotFather
        ENABLE_TELEGRAM_REPLY — Must be "true" to actually send
    """
    if not _enabled():
        logger.debug("Telegram replies disabled (ENABLE_TELEGRAM_REPLY not set)")
        return None

    token = _bot_token()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not configured — skipping send")
        return None

    if not chat_id or not text:
        logger.warning("send_telegram_message: missing chat_id or text")
        return None

    clean_text = _sanitize_text(text.strip())
    url = f"{_TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": clean_text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        capture_exception(exc, {"source": "telegram_client", "chat_id": str(chat_id)})
        logger.exception("Failed to send Telegram message to chat %s", chat_id)
        return None


async def set_webhook(webhook_url: str, secret_token: Optional[str] = None) -> dict:
    """
    Register our webhook URL with Telegram so it sends updates to us.
    Call GET /webhooks/telegram/setup to trigger this.
    """
    token = _bot_token()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

    url = f"{_TELEGRAM_API_BASE}/bot{token}/setWebhook"
    payload: dict = {"url": webhook_url}
    if secret_token:
        payload["secret_token"] = secret_token

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            return response.json()
    except Exception as exc:
        capture_exception(exc, {"source": "telegram_client.set_webhook"})
        return {"ok": False, "error": str(exc)}
