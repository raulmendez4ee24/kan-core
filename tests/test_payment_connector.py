"""Tests for brain/payment_connector.py — mocked Stripe API."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from brain.payment_connector import (
    PaymentConnector,
    WebhookVerificationError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WH_SECRET = "whsec_test_secret"
_SK = "sk_test_key"


def _make_sig(secret: str, body: bytes, t: int | None = None) -> str:
    """Build a valid Stripe-Signature header value."""
    if t is None:
        t = int(time.time())
    signed = f"{t}.".encode() + body
    v1 = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


def _connector(
    *,
    http_post=None,
    track_close=None,
    update_crm=None,
    trigger_onboarding=None,
) -> PaymentConnector:
    return PaymentConnector(
        stripe_secret_key=_SK,
        stripe_webhook_secret=_WH_SECRET,
        http_post=http_post,
        track_close=track_close,
        update_crm=update_crm,
        trigger_onboarding=trigger_onboarding,
    )


def _checkout_event(
    lead_id: str = "lead_abc",
    product: str = "CRM Pack",
    amount_mxn: float = 5000.0,
    session_id: str = "cs_test_123",
) -> dict[str, Any]:
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "payment_status": "paid",
                "amount_total": int(amount_mxn * 100),
                "metadata": {
                    "lead_id": lead_id,
                    "product": product,
                    "amount_mxn": str(amount_mxn),
                },
            }
        },
    }


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------


def test_verify_signature_valid() -> None:
    body = b'{"type":"test"}'
    sig = _make_sig(_WH_SECRET, body)
    conn = _connector()
    conn.verify_signature(body, sig)  # must not raise


def test_verify_signature_valid_str_body() -> None:
    body = '{"type":"test"}'
    sig = _make_sig(_WH_SECRET, body.encode())
    conn = _connector()
    conn.verify_signature(body, sig)


def test_verify_signature_wrong_secret() -> None:
    body = b'{"type":"test"}'
    sig = _make_sig("wrong_secret", body)
    conn = _connector()
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        conn.verify_signature(body, sig)


def test_verify_signature_tampered_body() -> None:
    original = b'{"type":"checkout.session.completed"}'
    sig = _make_sig(_WH_SECRET, original)
    tampered = b'{"type":"payment_intent.succeeded"}'
    conn = _connector()
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        conn.verify_signature(tampered, sig)


def test_verify_signature_expired_timestamp() -> None:
    body = b'{"type":"test"}'
    old_t = int(time.time()) - 400  # beyond 300s tolerance
    sig = _make_sig(_WH_SECRET, body, t=old_t)
    conn = _connector()
    with pytest.raises(WebhookVerificationError, match="tolerance"):
        conn.verify_signature(body, sig)


def test_verify_signature_malformed_header() -> None:
    body = b'{"type":"test"}'
    conn = _connector()
    with pytest.raises(WebhookVerificationError, match="Malformed"):
        conn.verify_signature(body, "not-a-valid-header")


def test_verify_signature_no_secret_raises() -> None:
    conn = PaymentConnector(stripe_secret_key=_SK, stripe_webhook_secret="")
    body = b"{}"
    with pytest.raises(WebhookVerificationError, match="not configured"):
        conn.verify_signature(body, "t=1,v1=abc")


# ---------------------------------------------------------------------------
# create_payment_link
# ---------------------------------------------------------------------------


def test_create_payment_link_returns_url() -> None:
    captured: list[dict] = []

    async def _fake_post(url: str, *, data: dict, headers: dict) -> dict:
        captured.append({"url": url, "data": data, "headers": headers})
        return {"url": "https://buy.stripe.com/test_link_abc"}

    async def _run() -> None:
        conn = _connector(http_post=_fake_post)
        link = await conn.create_payment_link("lead_1", "CRM Pack", 5000.0, "Paquete CRM básico")
        assert link == "https://buy.stripe.com/test_link_abc"

    asyncio.get_event_loop().run_until_complete(_run())


def test_create_payment_link_sends_correct_amount() -> None:
    captured: list[dict] = []

    async def _fake_post(url: str, *, data: dict, headers: dict) -> dict:
        captured.append(data)
        return {"url": "https://buy.stripe.com/test"}

    async def _run() -> None:
        conn = _connector(http_post=_fake_post)
        await conn.create_payment_link("lead_2", "Producto X", 1234.56, "Desc")
        data = captured[0]
        # 1234.56 MXN = 123456 centavos
        assert data["line_items[0][price_data][unit_amount]"] == "123456"
        assert data["line_items[0][price_data][currency]"] == "mxn"

    asyncio.get_event_loop().run_until_complete(_run())


def test_create_payment_link_includes_lead_metadata() -> None:
    captured: list[dict] = []

    async def _fake_post(url: str, *, data: dict, headers: dict) -> dict:
        captured.append(data)
        return {"url": "https://buy.stripe.com/test"}

    async def _run() -> None:
        conn = _connector(http_post=_fake_post)
        await conn.create_payment_link("lead_xyz", "Pack Pro", 999.0, "Descripción")
        data = captured[0]
        assert data["metadata[lead_id]"] == "lead_xyz"
        assert data["metadata[product]"] == "Pack Pro"

    asyncio.get_event_loop().run_until_complete(_run())


def test_create_payment_link_uses_bearer_auth() -> None:
    captured: list[dict] = []

    async def _fake_post(url: str, *, data: dict, headers: dict) -> dict:
        captured.append(headers)
        return {"url": "https://buy.stripe.com/test"}

    async def _run() -> None:
        conn = _connector(http_post=_fake_post)
        await conn.create_payment_link("l1", "P", 100.0, "D")
        assert captured[0]["Authorization"] == f"Bearer {_SK}"

    asyncio.get_event_loop().run_until_complete(_run())


def test_create_payment_link_raises_when_no_url() -> None:
    async def _fake_post(url: str, *, data: dict, headers: dict) -> dict:
        return {"id": "pl_test"}  # no "url" key

    async def _run() -> None:
        conn = _connector(http_post=_fake_post)
        with pytest.raises(RuntimeError, match="no URL"):
            await conn.create_payment_link("l1", "P", 100.0, "D")

    asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# handle_webhook — checkout.session.completed
# ---------------------------------------------------------------------------


def test_handle_webhook_calls_track_close() -> None:
    track_calls: list[dict] = []

    async def _track(**kwargs):
        track_calls.append(kwargs)

    async def _run() -> None:
        conn = _connector(track_close=_track, update_crm=AsyncMock(), trigger_onboarding=AsyncMock())
        event = _checkout_event(lead_id="lead_1", product="CRM", amount_mxn=3000.0)
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        result = await conn.handle_webhook(event, sig, body)
        assert result["status"] == "processed"
        assert len(track_calls) == 1
        assert track_calls[0]["lead_id"] == "lead_1"
        assert track_calls[0]["revenue"] == 3000.0
        assert track_calls[0]["currency"] == "MXN"

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_calls_update_crm_with_cliente() -> None:
    crm_calls: list[dict] = []

    async def _update_crm(*, lead_id: str, status: str) -> None:
        crm_calls.append({"lead_id": lead_id, "status": status})

    async def _run() -> None:
        conn = _connector(
            track_close=AsyncMock(),
            update_crm=_update_crm,
            trigger_onboarding=AsyncMock(),
        )
        event = _checkout_event(lead_id="lead_2")
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        await conn.handle_webhook(event, sig, body)
        assert len(crm_calls) == 1
        assert crm_calls[0]["lead_id"] == "lead_2"
        assert crm_calls[0]["status"] == "cliente"

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_calls_trigger_onboarding() -> None:
    onboarding_calls: list[dict] = []

    async def _onboarding(*, lead_id: str, product: str, amount: float) -> None:
        onboarding_calls.append({"lead_id": lead_id, "product": product, "amount": amount})

    async def _run() -> None:
        conn = _connector(
            track_close=AsyncMock(),
            update_crm=AsyncMock(),
            trigger_onboarding=_onboarding,
        )
        event = _checkout_event(lead_id="lead_3", product="Pro Plan", amount_mxn=2500.0)
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        await conn.handle_webhook(event, sig, body)
        assert len(onboarding_calls) == 1
        assert onboarding_calls[0]["lead_id"] == "lead_3"
        assert onboarding_calls[0]["product"] == "Pro Plan"
        assert onboarding_calls[0]["amount"] == 2500.0

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_ignores_non_checkout_events() -> None:
    track_calls: list[dict] = []

    async def _track(**kwargs):
        track_calls.append(kwargs)

    async def _run() -> None:
        conn = _connector(track_close=_track, update_crm=AsyncMock(), trigger_onboarding=AsyncMock())
        event = {"type": "payment_intent.created", "data": {"object": {}}}
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        result = await conn.handle_webhook(event, sig, body)
        assert result["status"] == "ignored"
        assert result["event_type"] == "payment_intent.created"
        assert len(track_calls) == 0

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_returns_no_lead_id_when_metadata_missing() -> None:
    async def _run() -> None:
        conn = _connector(track_close=AsyncMock(), update_crm=AsyncMock(), trigger_onboarding=AsyncMock())
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test", "payment_status": "paid", "metadata": {}}},
        }
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        result = await conn.handle_webhook(event, sig, body)
        assert result["status"] == "no_lead_id"

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_invalid_signature_raises() -> None:
    async def _run() -> None:
        conn = _connector(track_close=AsyncMock(), update_crm=AsyncMock(), trigger_onboarding=AsyncMock())
        event = _checkout_event()
        body = json.dumps(event).encode()
        bad_sig = _make_sig("wrong_secret", body)
        with pytest.raises(WebhookVerificationError):
            await conn.handle_webhook(event, bad_sig, body)

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_track_close_error_does_not_abort() -> None:
    """track_close failure should still run update_crm and trigger_onboarding."""
    crm_calls: list[dict] = []
    onboarding_calls: list[dict] = []

    async def _fail_track(**kwargs):
        raise RuntimeError("DB down")

    async def _update_crm(*, lead_id: str, status: str) -> None:
        crm_calls.append({"lead_id": lead_id, "status": status})

    async def _onboarding(*, lead_id: str, product: str, amount: float) -> None:
        onboarding_calls.append(lead_id)

    async def _run() -> None:
        conn = _connector(
            track_close=_fail_track,
            update_crm=_update_crm,
            trigger_onboarding=_onboarding,
        )
        event = _checkout_event(lead_id="lead_5")
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        result = await conn.handle_webhook(event, sig, body)
        assert result["status"] == "processed"
        assert "track_close_error" in result
        assert len(crm_calls) == 1
        assert len(onboarding_calls) == 1

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_update_crm_error_does_not_abort() -> None:
    """update_crm failure should still run trigger_onboarding."""
    onboarding_calls: list[dict] = []

    async def _fail_crm(*, lead_id: str, status: str) -> None:
        raise RuntimeError("CRM offline")

    async def _onboarding(*, lead_id: str, product: str, amount: float) -> None:
        onboarding_calls.append(lead_id)

    async def _run() -> None:
        conn = _connector(
            track_close=AsyncMock(),
            update_crm=_fail_crm,
            trigger_onboarding=_onboarding,
        )
        event = _checkout_event(lead_id="lead_6")
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        result = await conn.handle_webhook(event, sig, body)
        assert result["status"] == "processed"
        assert "update_crm_error" in result
        assert len(onboarding_calls) == 1

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_amount_from_stripe_total_fallback() -> None:
    """When amount_mxn is absent in metadata, fall back to amount_total / 100."""
    track_calls: list[dict] = []

    async def _track(**kwargs):
        track_calls.append(kwargs)

    async def _run() -> None:
        conn = _connector(track_close=_track, update_crm=AsyncMock(), trigger_onboarding=AsyncMock())
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_fallback",
                    "payment_status": "paid",
                    "amount_total": 150000,  # 1500 MXN in centavos
                    "metadata": {"lead_id": "lead_7", "product": "Basic"},
                }
            },
        }
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        await conn.handle_webhook(event, sig, body)
        assert track_calls[0]["revenue"] == 1500.0

    asyncio.get_event_loop().run_until_complete(_run())


def test_handle_webhook_result_contains_product_and_amount() -> None:
    async def _run() -> None:
        conn = _connector(track_close=AsyncMock(), update_crm=AsyncMock(), trigger_onboarding=AsyncMock())
        event = _checkout_event(lead_id="lead_8", product="Gold Pack", amount_mxn=9999.0)
        body = json.dumps(event).encode()
        sig = _make_sig(_WH_SECRET, body)
        result = await conn.handle_webhook(event, sig, body)
        assert result["product"] == "Gold Pack"
        assert result["amount"] == 9999.0
        assert result["lead_id"] == "lead_8"

    asyncio.get_event_loop().run_until_complete(_run())
