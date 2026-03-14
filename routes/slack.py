"""
Slack webhook routes.

POST /webhooks/slack  — Receives events from Slack Events API
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from observability import capture_exception

logger = logging.getLogger("kan_core.webhooks.slack")
router = APIRouter(prefix="/webhooks/slack", tags=["slack"])


def _verify_slack_signature(
    body: bytes,
    timestamp: Optional[str],
    signature: Optional[str],
) -> bool:
    """Validate X-Slack-Signature using HMAC-SHA256."""
    secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
    if not secret:
        return True  # No secret configured → accept all (dev mode)

    if not timestamp or not signature:
        return False

    # Reject replays older than 5 minutes.
    try:
        if abs(time.time() - float(timestamp)) > 300:
            logger.warning("Slack webhook: timestamp too old (replay protection)")
            return False
    except ValueError:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
    computed = "v0=" + hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature.strip())


@router.post("")
async def slack_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None),
):
    """Receive an event from Slack Events API and queue it for AI processing."""
    raw_body = await request.body()

    if not _verify_slack_signature(raw_body, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    try:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        # Handle Slack URL verification challenge (initial setup handshake).
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}

        # Ignore retries from Slack (X-Slack-Retry-Num header present means Slack is retrying).
        retry_num = request.headers.get("x-slack-retry-num")
        if retry_num:
            logger.debug("Slack webhook: ignoring retry #%s", retry_num)
            return {"ok": True}

        event = payload.get("event") or {}
        # Ignore bot messages to avoid loops.
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return {"ok": True}

        from brain.dispatcher import handle_slack_event
        background_tasks.add_task(handle_slack_event, payload)
        return {"ok": True}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error processing Slack event: %s", type(exc).__name__)
        capture_exception(exc, {"source": "slack_webhook"})
        return JSONResponse(status_code=200, content={"ok": False, "error": "Internal error — logged"})
