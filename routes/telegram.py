"""
Telegram webhook routes.

POST /webhooks/telegram        — Receives updates from Telegram Bot API
GET  /webhooks/telegram/setup  — Registers our webhook URL with Telegram
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from observability import capture_exception

logger = logging.getLogger("kan_core.webhooks.telegram")
router = APIRouter(prefix="/webhooks/telegram", tags=["telegram"])


def _verify_secret_token(header_value: Optional[str]) -> bool:
    """Validate X-Telegram-Bot-Api-Secret-Token if TELEGRAM_SECRET_TOKEN is configured."""
    expected = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip()
    if not expected:
        return True  # No secret configured → accept all (dev mode)
    if not header_value:
        return False
    return hmac.compare_digest(expected, header_value.strip())


@router.post("")
async def telegram_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    """Receive an update from Telegram and queue it for AI processing."""
    if not _verify_secret_token(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=401, detail="Invalid Telegram secret token")

    try:
        raw_body = await request.body()
        try:
            update = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        from brain.dispatcher import handle_telegram_event
        background_tasks.add_task(handle_telegram_event, update)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error processing Telegram update: %s", type(exc).__name__)
        capture_exception(exc, {"source": "telegram_webhook"})
        return JSONResponse(status_code=200, content={"ok": False, "error": "Internal error — logged"})


@router.get("/setup")
async def telegram_setup(webhook_url: Optional[str] = None):
    """
    Register our webhook URL with Telegram.
    Pass ?webhook_url=https://your-domain/webhooks/telegram
    or set TELEGRAM_WEBHOOK_URL env var.
    """
    from tools.telegram_client import set_webhook

    url = (webhook_url or os.getenv("TELEGRAM_WEBHOOK_URL") or "").strip()
    if not url:
        raise HTTPException(
            status_code=400,
            detail="Provide ?webhook_url=... or set TELEGRAM_WEBHOOK_URL env var",
        )

    secret = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip() or None
    result = await set_webhook(url, secret_token=secret)
    return result
