"""Microsoft Teams client — send messages via Incoming Webhook or Bot Framework."""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("kan_core.teams_client")

_TIMEOUT = 10.0


async def send_teams_message(webhook_url: str, text: str) -> bool:
    """Send an Adaptive Card text message to a Teams channel via Incoming Webhook.

    ``webhook_url`` is the full webhook URL (TEAMS_WEBHOOK_URL env or per-channel).
    Returns True on success, False on any error.
    Requires ENABLE_TEAMS_REPLY=true.
    """
    if os.getenv("ENABLE_TEAMS_REPLY", "false").lower() not in {"1", "true", "yes"}:
        return False

    url = webhook_url.strip() or os.getenv("TEAMS_WEBHOOK_URL", "").strip()
    if not url:
        logger.warning("No Teams webhook URL configured — skipping send")
        return False

    # Use Adaptive Card (Teams-native, rich rendering)
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                },
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code not in (200, 201, 202):
                logger.error(
                    "Teams webhook error %s: %s", resp.status_code, resp.text[:200]
                )
                return False
            return True
    except Exception as exc:
        logger.exception("Failed to send Teams message: %s", exc)
        return False
