"""Discord REST API client — send messages via Bot token."""
from __future__ import annotations

import logging
import os
import httpx

logger = logging.getLogger("kan_core.discord_client")

_DISCORD_API = "https://discord.com/api/v10"
_TIMEOUT = 10.0


async def send_discord_message(channel_id: str, content: str) -> bool:
    """Send a text message to a Discord channel.

    Returns True on success, False on any error.
    Requires DISCORD_BOT_TOKEN and ENABLE_DISCORD_REPLY=true.
    """
    if os.getenv("ENABLE_DISCORD_REPLY", "false").lower() not in {"1", "true", "yes"}:
        return False

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        logger.warning("DISCORD_BOT_TOKEN not set — skipping send")
        return False

    url = f"{_DISCORD_API}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    payload = {"content": content[:2000]}  # Discord hard limit

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code not in (200, 201):
                logger.error(
                    "Discord API error %s: %s", resp.status_code, resp.text[:200]
                )
                return False
            return True
    except Exception as exc:
        logger.exception("Failed to send Discord message: %s", exc)
        return False
