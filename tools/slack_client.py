"""
Slack API client.
Sends messages via the Web API using httpx (no slack-sdk needed).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from observability import capture_exception

logger = logging.getLogger("kan_core.slack_client")

_SLACK_API_BASE = "https://slack.com/api"
_MAX_TEXT_LENGTH = 4000


def _bot_token() -> Optional[str]:
    return (os.getenv("SLACK_BOT_TOKEN") or "").strip() or None


def _enabled() -> bool:
    """Replies are ON by default when a token is set. Set ENABLE_SLACK_REPLY=false to disable."""
    explicit = os.getenv("ENABLE_SLACK_REPLY", "").strip().lower()
    if explicit in {"0", "false", "no"}:
        return False
    return bool(_bot_token())


def _sanitize_text(text: str) -> str:
    text = text.replace("\x00", "")
    if len(text) > _MAX_TEXT_LENGTH:
        text = text[: _MAX_TEXT_LENGTH - 3] + "..."
    return text


async def send_slack_message(
    channel_id: str,
    text: str,
    thread_ts: Optional[str] = None,
) -> Optional[dict]:
    """
    Send a text message to a Slack channel via chat.postMessage.

    Env vars required:
        SLACK_BOT_TOKEN      — xoxb-... token from Slack App config
        ENABLE_SLACK_REPLY   — Must be "true" to actually send
    """
    if not _enabled():
        logger.debug("Slack replies disabled (ENABLE_SLACK_REPLY not set)")
        return None

    token = _bot_token()
    if not token:
        logger.warning("SLACK_BOT_TOKEN not configured — skipping send")
        return None

    if not channel_id or not text:
        logger.warning("send_slack_message: missing channel_id or text")
        return None

    clean_text = _sanitize_text(text.strip())
    url = f"{_SLACK_API_BASE}/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload: dict = {"channel": channel_id, "text": clean_text}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack API error: %s", data.get("error"))
            return data
    except Exception as exc:
        capture_exception(exc, {"source": "slack_client", "channel_id": channel_id})
        logger.exception("Failed to send Slack message to channel %s", channel_id)
        return None
