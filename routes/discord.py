"""Discord webhook endpoint.

Receives Interactions and Message events from Discord.

Required env vars:
    DISCORD_PUBLIC_KEY      — Ed25519 public key from Discord Developer Portal
    DISCORD_BOT_TOKEN       — Bot token for sending replies
    DISCORD_CHANNEL_CLIENT_MAP_JSON — {"channel_id": "org_uuid", "*": "fallback_uuid"}
    ENABLE_DISCORD_REPLY    — "true" to enable outbound messages

Discord verifies every interaction with an Ed25519 signature.
We verify before processing (Discord will deactivate endpoints that don't verify).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response

from brain.dispatcher import handle_discord_event

logger = logging.getLogger("kan_core.routes.discord")

router = APIRouter(prefix="/webhooks/discord", tags=["discord"])


def _verify_discord_signature(
    raw_body: bytes,
    signature: str,
    timestamp: str,
) -> bool:
    """Verify Ed25519 signature from Discord."""
    public_key_hex = os.getenv("DISCORD_PUBLIC_KEY", "").strip()
    if not public_key_hex:
        # If no public key configured, skip verification (dev mode only).
        logger.warning("DISCORD_PUBLIC_KEY not set — skipping signature check")
        return True

    try:
        from nacl.signing import VerifyKey  # type: ignore

        vk = VerifyKey(bytes.fromhex(public_key_hex))
        vk.verify((timestamp + raw_body.decode()).encode(), bytes.fromhex(signature))
        return True
    except Exception:
        return False


@router.post("")
async def discord_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature_ed25519: str = Header(default=""),
    x_signature_timestamp: str = Header(default=""),
) -> Response:
    """Receive Discord interaction or event payload."""
    raw_body = await request.body()

    if not _verify_discord_signature(raw_body, x_signature_ed25519, x_signature_timestamp):
        raise HTTPException(status_code=401, detail="Invalid request signature")

    try:
        payload: Dict[str, Any] = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Discord PING — must reply immediately with PONG (type=1)
    if payload.get("type") == 1:
        return Response(content='{"type":1}', media_type="application/json")

    background_tasks.add_task(handle_discord_event, payload)
    return Response(status_code=200)
