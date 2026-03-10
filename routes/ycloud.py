from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from observability import capture_exception

logger = logging.getLogger("kan_core.webhooks.ycloud")
router = APIRouter(prefix="/webhooks/ycloud", tags=["ycloud"])
_SUPPORTED_YCLOUD_EVENTS = [
    "whatsapp.inbound_message.received",
    "whatsapp.inbound.message",
]


def _ycloud_signature_debug(raw_body: bytes, signature: Optional[str]) -> dict:
    secret = str(os.getenv("YCLOUD_WEBHOOK_SECRET") or "").strip()
    signature_text = str(signature or "").strip()
    parts = {}
    for chunk in signature_text.split(","):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parts[key.strip()] = value.strip()
    timestamp = parts.get("t")
    digest = parts.get("s") or parts.get("v1")
    expected = None
    if secret and timestamp:
        expected = hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("utf-8") + b"." + raw_body,
            hashlib.sha256,
        ).hexdigest()
    return {
        "secret_configured": bool(secret),
        "signature_present": bool(signature_text),
        "signature_header_raw": signature_text,
        "timestamp": timestamp,
        "body_length": len(raw_body),
        "expected_prefix": (expected or "")[:12],
        "received_prefix": (digest or "")[:12],
        "expected": expected,
        "received": digest,
    }


def _verify_ycloud_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    debug = _ycloud_signature_debug(raw_body, signature)
    secret = str(os.getenv("YCLOUD_WEBHOOK_SECRET") or "").strip()
    if not secret:
        return True
    if not debug["signature_present"]:
        return False
    expected = debug["expected"]
    digest = debug["received"]
    if not expected or not digest:
        return False
    return hmac.compare_digest(expected, digest)


@router.post("/whatsapp")
async def ycloud_whatsapp_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    ycloud_signature: Optional[str] = Header(None, alias="YCloud-Signature"),
):
    raw_body = await request.body()
    debug = _ycloud_signature_debug(raw_body, ycloud_signature)
    logger.warning(
        "YCloud signature debug: secret_configured=%s signature_present=%s signature_raw=%s "
        "timestamp=%s body_length=%s expected_prefix=%s received_prefix=%s",
        debug["secret_configured"],
        debug["signature_present"],
        debug["signature_header_raw"],
        debug["timestamp"],
        debug["body_length"],
        debug["expected_prefix"],
        debug["received_prefix"],
    )
    if not _verify_ycloud_signature(raw_body, ycloud_signature):
        raise HTTPException(status_code=401, detail="Invalid YCloud signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        from brain.dispatcher import handle_ycloud_event

        background_tasks.add_task(handle_ycloud_event, payload)
        return {"ok": True}
    except Exception as exc:
        logger.exception("Unhandled error processing YCloud webhook")
        capture_exception(exc, {"source": "ycloud_webhook"})
        return JSONResponse(status_code=200, content={"ok": False, "error": "Internal error — logged"})


@router.get("/setup")
async def ycloud_setup(webhook_url: Optional[str] = None):
    url = str(
        webhook_url
        or os.getenv("YCLOUD_WEBHOOK_URL")
        or "https://web-production-dbb6b.up.railway.app/webhooks/ycloud/whatsapp"
    ).strip()
    secret_configured = bool(str(os.getenv("YCLOUD_WEBHOOK_SECRET") or "").strip())
    return {
        "provider": "ycloud",
        "webhook_url": url,
        "events": _SUPPORTED_YCLOUD_EVENTS,
        "signature_header": "YCloud-Signature",
        "secret_configured": secret_configured,
    }
