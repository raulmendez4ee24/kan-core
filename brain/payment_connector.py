"""PaymentConnector — Stripe integration for payment links and webhook processing.

Env vars:
    STRIPE_SECRET_KEY       — Stripe secret key (sk_live_... or sk_test_...)
    STRIPE_WEBHOOK_SECRET   — Stripe webhook signing secret (whsec_...)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("kan_core.payment_connector")

_STRIPE_API = "https://api.stripe.com/v1"
_TIMESTAMP_TOLERANCE = 300  # seconds


class WebhookVerificationError(Exception):
    """Raised when the Stripe-Signature header cannot be verified."""


class PaymentConnector:
    """Handles Stripe payment links and incoming webhook events."""

    def __init__(
        self,
        *,
        stripe_secret_key: Optional[str] = None,
        stripe_webhook_secret: Optional[str] = None,
        http_post: Optional[Callable[..., Awaitable[Any]]] = None,
        track_close: Optional[Callable[..., Awaitable[Any]]] = None,
        update_crm: Optional[Callable[..., Awaitable[None]]] = None,
        trigger_onboarding: Optional[Callable[..., Awaitable[None]]] = None,
        set_converted_at: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> None:
        self._sk = (stripe_secret_key or os.getenv("STRIPE_SECRET_KEY", "")).strip()
        self._wh_secret = (stripe_webhook_secret or os.getenv("STRIPE_WEBHOOK_SECRET", "")).strip()
        self._http_post = http_post or _default_http_post
        self._track_close = track_close or _default_track_close
        self._update_crm = update_crm or _default_update_crm
        self._trigger_onboarding = trigger_onboarding or _default_trigger_onboarding
        self._set_converted_at = set_converted_at or _default_set_converted_at

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    def verify_signature(self, raw_body: bytes | str, signature_header: str) -> None:
        """Verify a Stripe-Signature header against the raw request body.

        Raises WebhookVerificationError if the signature is invalid or expired.
        """
        if not self._wh_secret:
            raise WebhookVerificationError("STRIPE_WEBHOOK_SECRET not configured")

        body_bytes: bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body

        # Parse header: "t=1234567890,v1=abc123,v0=old_sig"
        parts: dict[str, list[str]] = {}
        for segment in signature_header.split(","):
            key, _, value = segment.strip().partition("=")
            if key:
                parts.setdefault(key, []).append(value)

        timestamp_str = parts.get("t", [""])[0]
        v1_sigs = parts.get("v1", [])

        if not timestamp_str or not v1_sigs:
            raise WebhookVerificationError("Malformed Stripe-Signature header")

        try:
            ts = int(timestamp_str)
        except ValueError as exc:
            raise WebhookVerificationError("Invalid timestamp in Stripe-Signature") from exc

        if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE:
            raise WebhookVerificationError("Stripe webhook timestamp outside tolerance window")

        signed_payload = f"{timestamp_str}.".encode("utf-8") + body_bytes
        expected = hmac.new(
            self._wh_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()

        for v1 in v1_sigs:
            if hmac.compare_digest(expected, v1):
                return

        raise WebhookVerificationError("Stripe webhook signature mismatch")

    # ------------------------------------------------------------------
    # Payment link creation
    # ------------------------------------------------------------------

    async def create_payment_link(
        self,
        lead_id: str,
        product: str,
        amount_mxn: float,
        description: str,
    ) -> str:
        """Create a Stripe Payment Link and return its URL.

        Args:
            lead_id:     Internal lead identifier (stored in Stripe metadata).
            product:     Product name displayed on the checkout page.
            amount_mxn:  Price in Mexican pesos (will be converted to centavos).
            description: Short product description shown to the buyer.

        Returns:
            The Stripe-hosted Payment Link URL (https://buy.stripe.com/...).
        """
        amount_centavos = max(1, int(round(amount_mxn * 100)))

        data = {
            "line_items[0][price_data][currency]": "mxn",
            "line_items[0][price_data][unit_amount]": str(amount_centavos),
            "line_items[0][price_data][product_data][name]": product[:500],
            "line_items[0][price_data][product_data][description]": description[:500],
            "line_items[0][quantity]": "1",
            "metadata[lead_id]": lead_id,
            "metadata[product]": product[:500],
            "metadata[amount_mxn]": str(amount_mxn),
        }

        result = await self._http_post(
            f"{_STRIPE_API}/payment_links",
            data=data,
            headers={"Authorization": f"Bearer {self._sk}"},
        )

        url = str(result.get("url") or "").strip()
        if not url:
            raise RuntimeError(f"Stripe returned no URL: {result}")
        return url

    # ------------------------------------------------------------------
    # Webhook handler
    # ------------------------------------------------------------------

    async def handle_webhook(
        self,
        payload: dict[str, Any],
        signature: str,
        raw_body: bytes | str,
    ) -> dict[str, Any]:
        """Process a Stripe webhook event.

        Verifies the signature, then for ``checkout.session.completed``:
        1. Calls ``attribution_engine.track_close``
        2. Updates CRM lead status to ``'cliente'``
        3. Fires the onboarding sequence

        Returns a dict describing what was done.
        """
        self.verify_signature(raw_body, signature)

        event_type = str(payload.get("type") or "")
        if event_type != "checkout.session.completed":
            logger.debug("payment_connector: ignoring event %s", event_type)
            return {"status": "ignored", "event_type": event_type}

        session_obj: dict[str, Any] = payload.get("data", {}).get("object", {})
        metadata: dict[str, Any] = session_obj.get("metadata", {}) or {}

        lead_id = str(metadata.get("lead_id") or "").strip()
        product = str(metadata.get("product") or "").strip()

        amount_mxn_raw = str(metadata.get("amount_mxn") or "").strip()
        if amount_mxn_raw:
            try:
                amount = float(amount_mxn_raw)
            except (ValueError, TypeError):
                amount = float(session_obj.get("amount_total") or 0) / 100.0
        else:
            amount = float(session_obj.get("amount_total") or 0) / 100.0

        if not lead_id:
            logger.warning("payment_connector: checkout.session.completed with no lead_id")
            return {"status": "no_lead_id"}

        result: dict[str, Any] = {
            "status": "processed",
            "lead_id": lead_id,
            "product": product,
            "amount": amount,
        }

        # 1. Track close in attribution engine
        try:
            await self._track_close(
                lead_id=lead_id,
                revenue=amount,
                currency="MXN",
                metadata={
                    "product": product,
                    "stripe_session_id": session_obj.get("id"),
                    "payment_status": session_obj.get("payment_status"),
                },
            )
        except Exception as exc:
            logger.error("payment_connector: track_close failed: %s", exc)
            result["track_close_error"] = str(exc)

        # 2. Update CRM status → 'cliente'
        try:
            await self._update_crm(lead_id=lead_id, status="cliente")
        except Exception as exc:
            logger.error("payment_connector: update_crm failed: %s", exc)
            result["update_crm_error"] = str(exc)

        # 3. Set converted_at on CrmLead
        try:
            await self._set_converted_at(lead_id=lead_id)
        except Exception as exc:
            logger.error("payment_connector: set_converted_at failed: %s", exc)
            result["set_converted_at_error"] = str(exc)

        # 4. Trigger onboarding sequence
        try:
            await self._trigger_onboarding(lead_id=lead_id, product=product, amount=amount)
        except Exception as exc:
            logger.error("payment_connector: trigger_onboarding failed: %s", exc)
            result["onboarding_error"] = str(exc)

        return result


# ---------------------------------------------------------------------------
# Default implementations (httpx-only, no Stripe SDK)
# ---------------------------------------------------------------------------


async def _default_http_post(
    url: str,
    *,
    data: dict[str, str],
    headers: dict[str, str],
) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, data=data, headers=headers)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Stripe API {resp.status_code}: {resp.text[:400]}")
        return resp.json()


async def _default_track_close(**kwargs: Any) -> Any:
    from brain import attribution_engine

    return await attribution_engine.track_close(**kwargs)


async def _default_update_crm(*, lead_id: str, status: str) -> None:
    logger.info("payment_connector: CRM update lead=%s status=%s", lead_id, status)
    try:
        from tools.n8n_client import send_to_n8n_with_response

        await send_to_n8n_with_response(
            {
                "source": "payment_connector",
                "action": "update_lead_status",
                "lead_id": lead_id,
                "status": status,
            }
        )
    except Exception as exc:
        logger.warning("payment_connector: n8n CRM update failed: %s", exc)


async def _default_set_converted_at(*, lead_id: str) -> None:
    """Mark lead as converted in CRM by setting converted_at timestamp."""
    logger.info("payment_connector: set_converted_at lead=%s", lead_id)
    try:
        from datetime import datetime, timezone

        from sqlalchemy import or_, select

        from database import AsyncSessionLocal
        from models import CrmLead

        async with AsyncSessionLocal() as session:
            stmt = select(CrmLead).where(
                or_(
                    CrmLead.external_id == lead_id,
                    CrmLead.id == lead_id,
                )
            )
            lead = (await session.execute(stmt)).scalars().first()
            if lead and lead.converted_at is None:
                lead.converted_at = datetime.now(timezone.utc)
                await session.commit()
    except Exception as exc:
        logger.warning("payment_connector: set_converted_at DB failed: %s", exc)


async def _default_trigger_onboarding(*, lead_id: str, product: str, amount: float) -> None:
    logger.info("payment_connector: triggering onboarding lead=%s product=%s", lead_id, product)
    try:
        from tools.n8n_client import send_to_n8n_with_response

        await send_to_n8n_with_response(
            {
                "source": "payment_connector",
                "action": "trigger_onboarding",
                "lead_id": lead_id,
                "product": product,
                "amount": amount,
            }
        )
    except Exception as exc:
        logger.warning("payment_connector: n8n onboarding trigger failed: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_shared: PaymentConnector | None = None


def get_payment_connector() -> PaymentConnector:
    global _shared
    if _shared is None:
        _shared = PaymentConnector()
    return _shared
