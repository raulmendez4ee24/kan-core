from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from brain.revenue_operators.closer import CloserAction, CloserStrategy, PipelineLead
from brain.voice_engine import (
    CallOutcome,
    build_confirmation_script,
    build_followup_script,
    build_outreach_script,
    confirmation_call,
    followup_call,
    outreach_call,
    schedule_confirmation_calls,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeOpportunity:
    def __init__(self, score=90.0, **kw):
        self.business_name = kw.get("business_name", "Clínica Dental Sur")
        self.vertical = kw.get("vertical", "dentists")
        self.city = kw.get("city", "Guadalajara")
        self.score = score
        self.reasons = kw.get("reasons", ["no tiene bot de citas"])


class _FakeOffer:
    def __init__(self):
        self.vertical = "dentists"
        self.package_name = "Dental Pro"
        self.summary = "bot de citas + recordatorios automáticos"
        self.monthly_price_mxn = 3_500


class _FakeLead:
    lead_id = "lead_voice_fw"
    name = "Dr. García"
    vertical = "dental"
    source = "meta_ads"
    objection = None
    ready_for_booking = False
    last_contact_at = datetime.now(timezone.utc) - timedelta(days=5)
    metadata: dict = {}

    def model_copy(self, *, update=None):
        import copy
        obj = copy.copy(self)
        for k, v in (update or {}).items():
            setattr(obj, k, v)
        return obj


class _FakeBooking:
    lead_id = "lead_bk_001"
    scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1, minutes=30)).isoformat()
    metadata = {"name": "Juan López"}


class _FakeBookingFarFuture:
    lead_id = "lead_bk_future"
    scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    metadata = {}


class _FakeOperator:
    def __init__(self, action_type="advance_followup"):
        self._action_type = action_type

    async def build_followup_action(self, lead) -> CloserAction:
        return CloserAction(
            lead_id=getattr(lead, "lead_id", "x"),
            action_type=self._action_type,
            priority=70,
            confidence=0.72,
            reason="test",
            message="Siguiente paso.",
            strategy=CloserStrategy.NURTURE_ONLY,
        )


def _booking_operator():
    return _FakeOperator(action_type="push_booking")


async def _fake_track(**kwargs: Any) -> Any:
    class _R:
        id = "bk_test"
    return _R()


# ---------------------------------------------------------------------------
# build_outreach_script
# ---------------------------------------------------------------------------


def test_build_outreach_script_contains_business_name():
    opp = _FakeOpportunity()
    script = build_outreach_script(opp)
    assert "Clínica Dental Sur" in script


def test_build_outreach_script_contains_vertical():
    opp = _FakeOpportunity()
    script = build_outreach_script(opp)
    assert "dentists" in script.lower() or "dental" in script.lower()


def test_build_outreach_script_contains_city():
    opp = _FakeOpportunity()
    script = build_outreach_script(opp)
    assert "Guadalajara" in script


def test_build_outreach_script_includes_offer_when_provided():
    opp = _FakeOpportunity()
    offer = _FakeOffer()
    script = build_outreach_script(opp, offer=offer)
    assert "Dental Pro" in script
    assert "3,500" in script or "3500" in script


def test_build_outreach_script_works_without_offer():
    opp = _FakeOpportunity()
    script = build_outreach_script(opp)
    assert len(script) > 20


def test_build_outreach_script_includes_reason_hint():
    opp = _FakeOpportunity(reasons=["no tiene sistema de citas"])
    script = build_outreach_script(opp)
    assert "citas" in script.lower()


# ---------------------------------------------------------------------------
# build_followup_script
# ---------------------------------------------------------------------------


def test_build_followup_script_contains_source():
    lead = _FakeLead()
    script = build_followup_script(lead)
    assert "meta_ads" in script.lower() or "seguimiento" in script.lower()


def test_build_followup_script_contains_vertical():
    lead = _FakeLead()
    script = build_followup_script(lead)
    assert "dental" in script.lower()


def test_build_followup_script_contains_name():
    lead = _FakeLead()
    script = build_followup_script(lead)
    assert "Dr. García" in script or "seguimiento" in script.lower()


# ---------------------------------------------------------------------------
# build_confirmation_script
# ---------------------------------------------------------------------------


def test_build_confirmation_script_contains_name():
    booking = _FakeBooking()
    script = build_confirmation_script(booking)
    assert "Juan López" in script


def test_build_confirmation_script_contains_cita():
    script = build_confirmation_script(_FakeBooking())
    assert "cita" in script.lower()


def test_build_confirmation_script_contains_time():
    booking = _FakeBooking()
    script = build_confirmation_script(booking)
    # Should contain a time string like "HH:MM"
    import re
    assert re.search(r"\d{1,2}:\d{2}", script) is not None


# ---------------------------------------------------------------------------
# outreach_call
# ---------------------------------------------------------------------------


def test_outreach_call_booking_confirmed_on_push_booking() -> None:
    transcripts = ["Sí, me interesa, adelante."]

    async def _listen() -> str:
        return transcripts.pop(0) if transcripts else ""

    async def _run() -> CallOutcome:
        return await outreach_call(
            _FakeOpportunity(),
            call_sid="CA_out_01",
            operator=_booking_operator(),
            say=AsyncMock(),
            listen=_listen,
            track_booking=_fake_track,
        )

    outcome = asyncio.run(_run())
    assert outcome.status == "booking_confirmed"
    assert "clínica_dental_sur" in outcome.lead_id or "dental" in outcome.lead_id.lower()


def test_outreach_call_rejected_on_rejection_phrase() -> None:
    transcripts = ["No me interesa, gracias."]

    async def _listen() -> str:
        return transcripts.pop(0) if transcripts else ""

    async def _run() -> CallOutcome:
        return await outreach_call(
            _FakeOpportunity(),
            call_sid="CA_out_02",
            operator=_FakeOperator(),
            say=AsyncMock(),
            listen=_listen,
        )

    outcome = asyncio.run(_run())
    assert outcome.status == "rejected"


def test_outreach_call_uses_outreach_script_as_intro() -> None:
    said: list[str] = []

    async def _say(text: str) -> None:
        said.append(text)

    async def _listen() -> str:
        return "No gracias."

    asyncio.run(
        outreach_call(
            _FakeOpportunity(),
            call_sid="CA_out_03",
            operator=_FakeOperator(),
            say=_say,
            listen=_listen,
        )
    )
    # First said text must be the outreach intro script
    assert len(said) >= 1
    assert "Clínica Dental Sur" in said[0]


def test_outreach_call_passes_offer_to_script() -> None:
    said: list[str] = []

    async def _say(text: str) -> None:
        said.append(text)

    async def _listen() -> str:
        return "No gracias."

    asyncio.run(
        outreach_call(
            _FakeOpportunity(),
            call_sid="CA_out_04",
            operator=_FakeOperator(),
            say=_say,
            listen=_listen,
            offer=_FakeOffer(),
        )
    )
    assert "Dental Pro" in said[0]


# ---------------------------------------------------------------------------
# followup_call
# ---------------------------------------------------------------------------


def test_followup_call_booking_confirmed() -> None:
    transcripts = ["Sí, hablemos."]

    async def _listen() -> str:
        return transcripts.pop(0) if transcripts else ""

    async def _run() -> CallOutcome:
        return await followup_call(
            _FakeLead(),
            call_sid="CA_fw_01",
            operator=_booking_operator(),
            say=AsyncMock(),
            listen=_listen,
            track_booking=_fake_track,
        )

    outcome = asyncio.run(_run())
    assert outcome.status == "booking_confirmed"


def test_followup_call_uses_followup_script_as_intro() -> None:
    said: list[str] = []

    async def _say(text: str) -> None:
        said.append(text)

    async def _listen() -> str:
        return "No gracias."

    asyncio.run(
        followup_call(
            _FakeLead(),
            call_sid="CA_fw_02",
            operator=_FakeOperator(),
            say=_say,
            listen=_listen,
        )
    )
    assert len(said) >= 1
    assert "seguimiento" in said[0].lower() or "Dr. García" in said[0]


def test_followup_call_rejection_returns_rejected_status() -> None:
    async def _listen() -> str:
        return "No quiero, gracias adiós."

    outcome = asyncio.run(
        followup_call(
            _FakeLead(),
            call_sid="CA_fw_03",
            operator=_FakeOperator(),
            say=AsyncMock(),
            listen=_listen,
        )
    )
    assert outcome.status == "rejected"


# ---------------------------------------------------------------------------
# confirmation_call
# ---------------------------------------------------------------------------


def test_confirmation_call_returns_true_on_success() -> None:
    said: list[str] = []

    async def _say(text: str) -> None:
        said.append(text)

    result = asyncio.run(confirmation_call(_FakeBooking(), say=_say))
    assert result is True
    assert len(said) == 1
    assert "Juan López" in said[0]


def test_confirmation_call_returns_false_when_say_raises() -> None:
    async def _failing_say(text: str) -> None:
        raise RuntimeError("TTS down")

    result = asyncio.run(confirmation_call(_FakeBooking(), say=_failing_say))
    assert result is False


def test_confirmation_call_script_in_message() -> None:
    said: list[str] = []

    async def _say(text: str) -> None:
        said.append(text)

    asyncio.run(confirmation_call(_FakeBooking(), say=_say))
    assert "cita" in said[0].lower()


# ---------------------------------------------------------------------------
# schedule_confirmation_calls
# ---------------------------------------------------------------------------


def test_schedule_confirmation_calls_fires_for_booking_within_window() -> None:
    called: list[str] = []

    async def _say(text: str) -> None:
        called.append(text)

    # Booking 1h30m from now → within 2h window
    bookings = [_FakeBooking()]
    result = asyncio.run(schedule_confirmation_calls(bookings, say=_say))
    assert "lead_bk_001" in result
    assert len(called) == 1


def test_schedule_confirmation_calls_skips_booking_outside_window() -> None:
    called: list[str] = []

    async def _say(text: str) -> None:
        called.append(text)

    bookings = [_FakeBookingFarFuture()]
    result = asyncio.run(schedule_confirmation_calls(bookings, say=_say))
    assert result == []
    assert called == []


def test_schedule_confirmation_calls_mixed_bookings() -> None:
    called_lead_ids: list[str] = []

    async def _say(text: str) -> None:
        pass  # not tracking text here

    class _BookingInWindow:
        lead_id = "in_window"
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        metadata = {"name": "Test"}

    class _BookingOutWindow:
        lead_id = "out_window"
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
        metadata = {}

    result = asyncio.run(
        schedule_confirmation_calls([_BookingInWindow(), _BookingOutWindow()], say=_say)
    )
    assert "in_window" in result
    assert "out_window" not in result


def test_schedule_confirmation_calls_respects_custom_window() -> None:
    called: list[str] = []

    async def _say(text: str) -> None:
        called.append(text)

    # Booking 3h from now — outside default 2h window, inside 4h window
    class _Booking3h:
        lead_id = "bk_3h"
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        metadata = {}

    result = asyncio.run(
        schedule_confirmation_calls([_Booking3h()], say=_say, window_hours=4.0)
    )
    assert "bk_3h" in result


def test_schedule_confirmation_calls_skips_invalid_date() -> None:
    async def _say(text: str) -> None:
        pass

    class _BadBooking:
        lead_id = "bad"
        scheduled_at = "not-a-date"
        metadata = {}

    result = asyncio.run(schedule_confirmation_calls([_BadBooking()], say=_say))
    assert result == []


# ---------------------------------------------------------------------------
# RevenueBrain wiring
# ---------------------------------------------------------------------------


def test_revenue_brain_trigger_outreach_calls_returns_names_above_85() -> None:
    from brain.revenue_brain import RevenueBrain

    said: list[str] = []
    listened: list[str] = []

    async def _say(text: str) -> None:
        said.append(text)

    async def _listen() -> str:
        return ""

    brain = RevenueBrain(voice_say=_say, voice_listen=_listen)

    opps = [
        _FakeOpportunity(score=90, business_name="Alta Score"),
        _FakeOpportunity(score=70, business_name="Baja Score"),
        _FakeOpportunity(score=86, business_name="Just Above"),
    ]

    async def _run() -> list[str]:
        return await brain.trigger_outreach_calls(opps, call_sid_map={
            "Alta Score": "CA_alta",
            "Just Above": "CA_just",
        })

    result = asyncio.run(_run())
    assert "Alta Score" in result
    assert "Just Above" in result
    assert "Baja Score" not in result


def test_revenue_brain_trigger_outreach_calls_skips_when_no_voice() -> None:
    from brain.revenue_brain import RevenueBrain

    brain = RevenueBrain()  # no voice_say / voice_listen
    opps = [_FakeOpportunity(score=95)]

    result = asyncio.run(brain.trigger_outreach_calls(opps))
    assert result == []
