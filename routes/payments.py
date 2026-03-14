"""Payments routes — Stripe webhook receiver.

POST /payments/webhook  — Stripe sends events here; verifies signature and
                          processes checkout.session.completed events.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger("kan_core.routes.payments")

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(alias="stripe-signature", default=""),
):
    """Receive Stripe webhook events.

    Verifies the ``Stripe-Signature`` header, then processes
    ``checkout.session.completed`` to update the attribution engine,
    CRM status, and onboarding sequence.

    This endpoint must NOT require API-key auth — Stripe sends the request.
    Authentication is performed via HMAC signature verification.
    """
    raw_body: bytes = await request.body()

    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("stripe_webhook: failed to parse JSON body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    from brain.payment_connector import WebhookVerificationError, get_payment_connector

    connector = get_payment_connector()

    try:
        result = await connector.handle_webhook(payload, stripe_signature, raw_body)
    except WebhookVerificationError as exc:
        logger.warning("stripe_webhook: signature verification failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("stripe_webhook: unexpected error: %s", exc)
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

    return result
