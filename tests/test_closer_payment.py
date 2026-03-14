"""Tests for PaymentConnector + Google Calendar wiring in CloserOperator."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from brain.revenue_operators.closer import (
    CloserOperator,
    CloserStrategy,
    ObjectionHandler,
    PipelineLead,
    StrategySelector,
    _build_demo_message,
    _build_payment_link_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeScorer:
    """Always returns a fixed score so strategy resolution is deterministic."""

    def __init__(self, score: int = 90) -> None:
        self._score = score

    async def get_score(self, lead_id: str) -> int:  # noqa: ARG002
        return self._score


def _operator(
    *,
    scorer_score: int = 90,
    payment_connector: Any = None,
    send_whatsapp=None,
    schedule_demo=None,
    objection_handler=None,
) -> CloserOperator:
    return CloserOperator(
        strategy_selector=StrategySelector(scorer=_FakeScorer(scorer_score)),
        objection_handler=objection_handler or ObjectionHandler(),
        payment_connector=payment_connector,
        send_whatsapp=send_whatsapp,
        schedule_demo=schedule_demo,
    )


def _high_score_lead(
    *,
    product_price: float | None = 5_000.0,
    phone: str | None = "+521234567890",
    product_name: str = "CRM Pack",
) -> PipelineLead:
    """Lead that has engagement/urgency scores high enough to trigger the close branch."""
    return PipelineLead(
        lead_id="lead_close_001",
        name="Carlos",
        engagement_score=85.0,
        urgency_score=85.0,
        budget_score=85.0,
        last_contact_at=datetime.now(timezone.utc),
        product_price=product_price,
        product_name=product_name,
        phone=phone,
    )


def _low_score_lead(*, product_price: float | None = 5_000.0) -> PipelineLead:
    return PipelineLead(
        lead_id="lead_low_001",
        engagement_score=10.0,
        last_contact_at=datetime.now(timezone.utc),
        product_price=product_price,
    )


# ---------------------------------------------------------------------------
# _build_payment_link_message
# ---------------------------------------------------------------------------


def test_payment_link_message_contains_link() -> None:
    lead = _high_score_lead()
    msg = _build_payment_link_message(lead, "https://buy.stripe.com/test")
    assert "https://buy.stripe.com/test" in msg


def test_payment_link_message_contains_lead_name() -> None:
    lead = _high_score_lead()
    msg = _build_payment_link_message(lead, "https://buy.stripe.com/test")
    assert "Carlos" in msg


def test_payment_link_message_contains_product() -> None:
    lead = _high_score_lead(product_name="Pro Plan")
    msg = _build_payment_link_message(lead, "https://buy.stripe.com/test")
    assert "Pro Plan" in msg


def test_payment_link_message_contains_price() -> None:
    lead = _high_score_lead(product_price=9_999.0)
    msg = _build_payment_link_message(lead, "https://buy.stripe.com/test")
    assert "9,999" in msg or "9999" in msg


def test_payment_link_message_no_link_graceful() -> None:
    lead = _high_score_lead()
    msg = _build_payment_link_message(lead, None)
    assert "Carlos" in msg
    assert "http" not in msg


# ---------------------------------------------------------------------------
# _build_demo_message
# ---------------------------------------------------------------------------


def test_demo_message_contains_lead_name() -> None:
    lead = _high_score_lead(product_price=20_000.0)
    msg = _build_demo_message(lead, "2026-03-20T10:00:00")
    assert "Carlos" in msg


def test_demo_message_contains_scheduled_time() -> None:
    lead = _high_score_lead(product_price=20_000.0)
    msg = _build_demo_message(lead, "2026-03-20T10:00:00")
    assert "2026-03-20" in msg


def test_demo_message_no_time_graceful() -> None:
    lead = _high_score_lead(product_price=20_000.0)
    msg = _build_demo_message(lead, None)
    assert "Carlos" in msg


# ---------------------------------------------------------------------------
# build_followup_action — payment link branch (score > 80, price < 15k)
# ---------------------------------------------------------------------------


def test_high_score_low_price_returns_send_payment_link() -> None:
    link_calls: list[dict] = []

    async def _fake_create_link(lead_id, product, amount_mxn, description) -> str:
        link_calls.append({"lead_id": lead_id, "amount": amount_mxn})
        return "https://buy.stripe.com/abc"

    class _FakeConnector:
        async def create_payment_link(self, lead_id, product, amount_mxn, description):
            return await _fake_create_link(lead_id, product, amount_mxn, description)

    async def _run() -> None:
        op = _operator(
            payment_connector=_FakeConnector(),
            send_whatsapp=AsyncMock(return_value=True),
        )
        lead = _high_score_lead(product_price=4_999.0)
        action = await op.build_followup_action(lead)
        assert action.action_type == "send_payment_link"
        assert action.payment_link == "https://buy.stripe.com/abc"
        assert len(link_calls) == 1
        assert link_calls[0]["amount"] == 4_999.0

    asyncio.get_event_loop().run_until_complete(_run())


def test_payment_link_action_has_correct_fields() -> None:
    async def _fake_create_link(*a, **kw):
        return "https://buy.stripe.com/xyz"

    class _FakeConnector:
        async def create_payment_link(self, *a, **kw):
            return await _fake_create_link()

    async def _run() -> None:
        op = _operator(
            payment_connector=_FakeConnector(),
            send_whatsapp=AsyncMock(return_value=True),
        )
        lead = _high_score_lead(product_price=10_000.0, product_name="Growth Pack")
        action = await op.build_followup_action(lead)
        assert action.action_type == "send_payment_link"
        assert action.channel == "whatsapp"
        assert action.priority >= 95
        assert action.confidence >= 0.85
        assert action.calendar_event_id is None
        assert action.demo_scheduled_at is None

    asyncio.get_event_loop().run_until_complete(_run())


def test_payment_link_sends_whatsapp_when_phone_present() -> None:
    wa_calls: list[dict] = []

    async def _fake_wa(to: str, message: str) -> bool:
        wa_calls.append({"to": to, "message": message})
        return True

    class _FakeConnector:
        async def create_payment_link(self, *a, **kw):
            return "https://buy.stripe.com/abc"

    async def _run() -> None:
        op = _operator(
            payment_connector=_FakeConnector(),
            send_whatsapp=_fake_wa,
        )
        lead = _high_score_lead(product_price=5_000.0, phone="+521234567890")
        await op.build_followup_action(lead)
        assert len(wa_calls) == 1
        assert wa_calls[0]["to"] == "+521234567890"
        assert "buy.stripe.com" in wa_calls[0]["message"]

    asyncio.get_event_loop().run_until_complete(_run())


def test_payment_link_skips_whatsapp_when_no_phone() -> None:
    wa_calls: list[dict] = []

    async def _fake_wa(to: str, message: str) -> bool:
        wa_calls.append({"to": to})
        return True

    class _FakeConnector:
        async def create_payment_link(self, *a, **kw):
            return "https://buy.stripe.com/abc"

    async def _run() -> None:
        op = _operator(
            payment_connector=_FakeConnector(),
            send_whatsapp=_fake_wa,
        )
        lead = _high_score_lead(product_price=5_000.0, phone=None)
        action = await op.build_followup_action(lead)
        assert action.action_type == "send_payment_link"
        assert len(wa_calls) == 0  # no phone → no WhatsApp

    asyncio.get_event_loop().run_until_complete(_run())


def test_payment_link_action_survives_connector_error() -> None:
    """If create_payment_link raises, action is still returned with payment_link=None."""

    class _BrokenConnector:
        async def create_payment_link(self, *a, **kw):
            raise RuntimeError("Stripe API down")

    async def _run() -> None:
        op = _operator(
            payment_connector=_BrokenConnector(),
            send_whatsapp=AsyncMock(return_value=True),
        )
        lead = _high_score_lead(product_price=3_000.0)
        action = await op.build_followup_action(lead)
        assert action.action_type == "send_payment_link"
        assert action.payment_link is None

    asyncio.get_event_loop().run_until_complete(_run())


def test_payment_link_action_survives_whatsapp_error() -> None:
    """If WhatsApp send raises, action is still returned with the link."""

    class _FakeConnector:
        async def create_payment_link(self, *a, **kw):
            return "https://buy.stripe.com/abc"

    async def _fail_wa(to: str, message: str) -> bool:
        raise RuntimeError("YCloud down")

    async def _run() -> None:
        op = _operator(
            payment_connector=_FakeConnector(),
            send_whatsapp=_fail_wa,
        )
        lead = _high_score_lead(product_price=5_000.0)
        action = await op.build_followup_action(lead)
        assert action.action_type == "send_payment_link"
        assert action.payment_link == "https://buy.stripe.com/abc"

    asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# build_followup_action — demo branch (score > 80, price >= 15k)
# ---------------------------------------------------------------------------


def test_high_score_high_price_returns_schedule_demo() -> None:
    demo_calls: list[dict] = []

    async def _fake_schedule(lead) -> tuple[str | None, str | None]:
        demo_calls.append({"lead_id": lead.lead_id})
        return "evt_abc123", "2026-03-21T10:00:00+00:00"

    async def _run() -> None:
        op = _operator(schedule_demo=_fake_schedule)
        lead = _high_score_lead(product_price=20_000.0)
        action = await op.build_followup_action(lead)
        assert action.action_type == "schedule_demo"
        assert action.calendar_event_id == "evt_abc123"
        assert action.demo_scheduled_at == "2026-03-21T10:00:00+00:00"
        assert len(demo_calls) == 1

    asyncio.get_event_loop().run_until_complete(_run())


def test_schedule_demo_action_has_correct_fields() -> None:
    async def _fake_schedule(lead) -> tuple[str | None, str | None]:
        return "evt_xyz", "2026-03-22T09:00:00+00:00"

    async def _run() -> None:
        op = _operator(schedule_demo=_fake_schedule)
        lead = _high_score_lead(product_price=15_000.0)
        action = await op.build_followup_action(lead)
        assert action.action_type == "schedule_demo"
        assert action.channel == "whatsapp"
        assert action.priority >= 95
        assert action.confidence >= 0.85
        assert action.payment_link is None

    asyncio.get_event_loop().run_until_complete(_run())


def test_schedule_demo_action_survives_scheduler_error() -> None:
    async def _fail_schedule(lead) -> tuple[str | None, str | None]:
        raise RuntimeError("Calendar API down")

    async def _run() -> None:
        op = _operator(schedule_demo=_fail_schedule)
        lead = _high_score_lead(product_price=50_000.0)
        action = await op.build_followup_action(lead)
        assert action.action_type == "schedule_demo"
        assert action.calendar_event_id is None
        assert action.demo_scheduled_at is None

    asyncio.get_event_loop().run_until_complete(_run())


def test_exactly_at_threshold_goes_to_demo() -> None:
    """Price == 15,000 MXN must route to schedule_demo, not payment link."""
    demo_calls: list[str] = []

    async def _fake_schedule(lead) -> tuple[str | None, str | None]:
        demo_calls.append(lead.lead_id)
        return "evt_threshold", "2026-03-25T10:00:00"

    async def _run() -> None:
        op = _operator(schedule_demo=_fake_schedule)
        lead = _high_score_lead(product_price=15_000.0)
        action = await op.build_followup_action(lead)
        assert action.action_type == "schedule_demo"
        assert len(demo_calls) == 1

    asyncio.get_event_loop().run_until_complete(_run())


def test_just_below_threshold_goes_to_payment_link() -> None:
    """Price == 14,999.99 MXN must route to payment link."""
    link_calls: list[str] = []

    class _FakeConnector:
        async def create_payment_link(self, *a, **kw):
            link_calls.append("called")
            return "https://buy.stripe.com/below_threshold"

    async def _run() -> None:
        op = _operator(
            payment_connector=_FakeConnector(),
            send_whatsapp=AsyncMock(return_value=True),
        )
        lead = _high_score_lead(product_price=14_999.99)
        action = await op.build_followup_action(lead)
        assert action.action_type == "send_payment_link"
        assert len(link_calls) == 1

    asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# build_followup_action — score <= 80 falls through to existing branches
# ---------------------------------------------------------------------------


def test_score_below_threshold_no_product_price_does_not_trigger_close() -> None:
    async def _run() -> None:
        # scorer_score=90 but product_price=None → should skip close branch
        op = _operator(scorer_score=90)
        lead = PipelineLead(
            lead_id="lead_no_price",
            engagement_score=85.0,
            urgency_score=85.0,
            last_contact_at=datetime.now(timezone.utc),
            product_price=None,  # no price → no close action
        )
        action = await op.build_followup_action(lead)
        assert action.action_type == "push_booking"  # falls through to existing

    asyncio.get_event_loop().run_until_complete(_run())


def test_low_score_lead_not_routed_to_close_even_with_price() -> None:
    async def _run() -> None:
        op = _operator(scorer_score=30)  # low scorer → _lead_score won't exceed 80
        lead = _low_score_lead(product_price=5_000.0)
        action = await op.build_followup_action(lead)
        # low score + no recent contact → reactivate_cold_lead
        # (last_contact_at is None → defaults to 30d ago, age_days >= 7)
        assert action.action_type != "send_payment_link"
        assert action.action_type != "schedule_demo"

    asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# PipelineLead new fields
# ---------------------------------------------------------------------------


def test_pipeline_lead_defaults() -> None:
    lead = PipelineLead(lead_id="x")
    assert lead.product_price is None
    assert lead.product_name == "nuestro servicio"
    assert lead.phone is None


def test_pipeline_lead_accepts_new_fields() -> None:
    lead = PipelineLead(
        lead_id="y",
        product_price=12_000.0,
        product_name="Enterprise Pack",
        phone="+521234567890",
    )
    assert lead.product_price == 12_000.0
    assert lead.product_name == "Enterprise Pack"
    assert lead.phone == "+521234567890"


# ---------------------------------------------------------------------------
# CloserAction new fields
# ---------------------------------------------------------------------------


def test_closer_action_defaults_for_new_fields() -> None:
    from brain.revenue_operators.closer import CloserAction

    action = CloserAction(
        lead_id="l1",
        action_type="advance_followup",
        priority=70,
        confidence=0.7,
        reason="test",
        message="msg",
    )
    assert action.payment_link is None
    assert action.calendar_event_id is None
    assert action.demo_scheduled_at is None
