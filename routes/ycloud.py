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


def _raw_secret() -> str:
    """Return the configured secret, stripping any erroneous 'whsec_' prefix.

    YCloud does not use Stripe-style prefixed secrets.  If the env var was
    accidentally stored as 'whsec_<hex>', we strip the prefix so the HMAC key
    matches what YCloud actually signed with.
    """
    secret = str(os.getenv("YCLOUD_WEBHOOK_SECRET") or "").strip()
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_"):]
    return secret


def _compute_hmac(secret: str, timestamp: str, raw_body: bytes) -> str:
    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    return hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()


def _parse_signature_header(signature: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse 't=<ts>,s=<digest>' → (timestamp, digest).  Returns (None, None) on failure."""
    text = str(signature or "").strip()
    parts: dict[str, str] = {}
    for chunk in text.split(","):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parts[key.strip()] = value.strip()
    return parts.get("t"), parts.get("s") or parts.get("v1")


def _verify_ycloud_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    secret = _raw_secret()
    if not secret:
        # No secret configured — allow all (safe for local dev, not for prod)
        return True

    timestamp, digest = _parse_signature_header(signature)
    if not timestamp or not digest:
        return False

    expected = _compute_hmac(secret, timestamp, raw_body)
    return hmac.compare_digest(expected, digest)


@router.post("/whatsapp")
async def ycloud_whatsapp_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    ycloud_signature: Optional[str] = Header(None, alias="YCloud-Signature"),
):
    raw_body = await request.body()

    timestamp, received_digest = _parse_signature_header(ycloud_signature)
    secret = _raw_secret()
    expected_digest = _compute_hmac(secret, timestamp or "", raw_body) if secret and timestamp else None

    match = (
        hmac.compare_digest(expected_digest, received_digest)
        if expected_digest and received_digest
        else False
    )

    if not match:
        logger.warning(
            "YCloud signature MISMATCH: secret_len=%d timestamp=%s body_len=%d "
            "expected=%s received=%s",
            len(secret),
            timestamp,
            len(raw_body),
            (expected_digest or "")[:16],
            (received_digest or "")[:16],
        )
        raise HTTPException(status_code=401, detail="Invalid YCloud signature")

    logger.debug("YCloud signature OK: timestamp=%s body_len=%d", timestamp, len(raw_body))

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
    secret_configured = bool(_raw_secret())
    return {
        "provider": "ycloud",
        "webhook_url": url,
        "events": _SUPPORTED_YCLOUD_EVENTS,
        "signature_header": "YCloud-Signature",
        "secret_configured": secret_configured,
    }
