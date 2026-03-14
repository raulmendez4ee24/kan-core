#!/usr/bin/env python3
"""End-to-end voice call lifecycle simulation — no real Twilio/ElevenLabs calls.

Lifecycle:
  1. Hunter finds opportunity with score > 85
  2. outreach_call triggered
  3. VoiceCall loop runs 3 turns:
       turn 1 — interest ("Sí, cuéntame más")
       turn 2 — price objection ("Está muy caro")
       turn 3 — booking confirmed ("Perfecto, adelante")
  4. attribution_engine.track_booking() called
  5. Fake booking scheduled 1h30m from now → confirmation_call fires
"""
from __future__ import annotations

import asyncio
import textwrap
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------
_G = "\033[92m"   # green
_Y = "\033[93m"   # yellow
_B = "\033[94m"   # blue
_M = "\033[95m"   # magenta
_C = "\033[96m"   # cyan
_R = "\033[0m"    # reset
_BOLD = "\033[1m"

def _header(title: str) -> None:
    width = 60
    print(f"\n{_BOLD}{_B}{'━' * width}{_R}")
    print(f"{_BOLD}{_B}  {title}{_R}")
    print(f"{_BOLD}{_B}{'━' * width}{_R}")

def _step(n: int, text: str) -> None:
    print(f"\n{_BOLD}{_C}[Step {n}]{_R} {text}")

def _ok(text: str) -> None:
    print(f"  {_G}✓{_R}  {text}")

def _info(text: str) -> None:
    print(f"  {_Y}→{_R}  {text}")

def _said(text: str) -> None:
    wrapped = textwrap.fill(text, width=72, subsequent_indent="         ")
    print(f"  {_M}🔊 SAY:{_R}  {wrapped}")

def _heard(text: str) -> None:
    print(f"  {_C}🎙 HEARD:{_R} {text}")

def _tracked(**kwargs: Any) -> None:
    for k, v in kwargs.items():
        print(f"  {_G}   {k}:{_R} {v}")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Opportunity:
    score = 92.0
    business_name = "Clínica Dental Sur"
    vertical = "dentists"
    city = "Guadalajara"
    reasons = ["no tiene bot de citas", "alta rotación de pacientes"]


class _FakeBooking:
    lead_id = "opp_clínica_dental_sur"
    scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1, minutes=30)).isoformat()
    metadata = {"name": "Clínica Dental Sur"}


# ---------------------------------------------------------------------------
# Operator that follows the scripted 3-turn narrative
# ---------------------------------------------------------------------------

class _ScriptedOperator:
    """Returns a scripted action sequence to drive the 3-turn demo."""

    def __init__(self) -> None:
        self._turn = 0

    async def build_followup_action(self, lead: Any):
        from brain.revenue_operators.closer import CloserAction, CloserStrategy

        self._turn += 1
        lead_id = str(getattr(lead, "lead_id", "x"))

        if self._turn == 1:
            # Turn 1 — customer is interested, keep nurturing
            _info(f"Operator (turn {self._turn}): customer interested → advance_followup")
            return CloserAction(
                lead_id=lead_id,
                action_type="advance_followup",
                priority=72,
                confidence=0.78,
                reason="customer expressed interest",
                message="Excelente. Nuestro sistema automatiza recordatorios y citas para clínicas dentales. "
                        "¿Le gustaría escuchar una demo rápida?",
                strategy=CloserStrategy.NURTURE_ONLY,
            )

        if self._turn == 2:
            # Turn 2 — price objection detected upstream, handle it
            objection = getattr(lead, "objection", None)
            _info(
                f"Operator (turn {self._turn}): objection='{objection}' → handle + push booking"
            )
            return CloserAction(
                lead_id=lead_id,
                action_type="advance_followup",
                priority=80,
                confidence=0.82,
                reason="price objection handled",
                message="Entiendo la preocupación. El plan base comienza en $1,200 MXN/mes y se paga solo "
                        "con un paciente adicional al mes. ¿Agendamos 15 minutos para mostrárselo en vivo?",
                strategy=CloserStrategy.NURTURE_ONLY,
            )

        # Turn 3 — push booking
        _info(f"Operator (turn {self._turn}): customer confirmed → push_booking")
        return CloserAction(
            lead_id=lead_id,
            action_type="push_booking",
            priority=95,
            confidence=0.93,
            reason="customer agreed to demo",
            message="Perfecto, le agenda una sesión de demostración para mañana a las 10 AM. "
                    "Le enviamos confirmación por WhatsApp. ¡Hasta mañana!",
            strategy=CloserStrategy.DIRECT_CLOSE,
        )


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------

async def run_e2e() -> None:
    from brain.voice_engine import (
        outreach_call,
        schedule_confirmation_calls,
    )

    _header("VOICE CALL E2E SIMULATION")

    # -----------------------------------------------------------------------
    # Step 1: Hunter finds opportunity
    # -----------------------------------------------------------------------
    _step(1, "Hunter scans market and finds high-score opportunity")
    opp = _Opportunity()
    _ok(f"Opportunity: {opp.business_name!r} | score={opp.score} | vertical={opp.vertical} | city={opp.city}")
    _ok(f"Signals: {', '.join(opp.reasons)}")
    assert opp.score > 85, "Score must be > 85 to trigger auto-call"
    _ok("Score > 85 → outreach_call will be triggered ✓")

    # -----------------------------------------------------------------------
    # Step 2: Wire mocked I/O
    # -----------------------------------------------------------------------
    _step(2, "Wiring mocked say / listen / track_booking")

    said: list[str] = []
    booking_kwargs: dict[str, Any] = {}

    transcripts = [
        "Sí, cuéntame más, me parece interesante.",     # turn 1 — interest
        "Está muy caro para nosotros ahora mismo.",       # turn 2 — price objection
        "Perfecto, adelante con la demostración.",        # turn 3 — confirm booking
    ]
    turn_idx = 0

    async def _say(text: str) -> None:
        said.append(text)
        _said(text)

    async def _listen() -> str:
        nonlocal turn_idx
        if turn_idx < len(transcripts):
            txt = transcripts[turn_idx]
            turn_idx += 1
            _heard(txt)
            return txt
        return ""

    async def _track_booking(**kwargs: Any) -> Any:
        booking_kwargs.update(kwargs)
        _ok("attribution_engine.track_booking() called with:")
        _tracked(**kwargs)

        class _FakeBookingResult:
            id = "bk_e2e_001"
        return _FakeBookingResult()

    _ok("Mocked I/O wired")

    # -----------------------------------------------------------------------
    # Step 3: outreach_call triggers VoiceCall loop
    # -----------------------------------------------------------------------
    _step(3, "Triggering outreach_call (VoiceCall loop — 3 turns)")

    outcome = await outreach_call(
        opp,
        call_sid="CA_e2e_test_001",
        operator=_ScriptedOperator(),
        say=_say,
        listen=_listen,
        track_booking=_track_booking,
    )

    print()
    _ok(f"VoiceCall finished → status={outcome.status!r}")
    _ok(f"lead_id={outcome.lead_id!r} | call_sid={outcome.call_sid!r}")
    _ok(f"turns={outcome.turns} | duration={outcome.duration_seconds:.2f}s")
    if outcome.booking_id:
        _ok(f"booking_id={outcome.booking_id!r}")

    # -----------------------------------------------------------------------
    # Step 4: Verify track_booking was called
    # -----------------------------------------------------------------------
    _step(4, "Verifying attribution_engine.track_booking() was called")
    assert outcome.status == "booking_confirmed", (
        f"Expected booking_confirmed, got {outcome.status!r}"
    )
    assert booking_kwargs.get("lead_id"), "track_booking must receive lead_id"
    assert booking_kwargs.get("scheduled_at"), "track_booking must receive scheduled_at"
    _ok(f"lead_id={booking_kwargs['lead_id']!r}")
    _ok(f"scheduled_at={booking_kwargs['scheduled_at']!r}")
    _ok(f"source_campaign={booking_kwargs.get('source_campaign')!r}")
    _ok("All assertions passed ✓")

    # -----------------------------------------------------------------------
    # Step 5: Turn log
    # -----------------------------------------------------------------------
    _step(5, "Turn log (VoiceCall internal state)")
    for entry in outcome.turn_log:
        t = entry.get("transcript", "")
        action = entry.get("action_type", "—")
        strategy = entry.get("strategy", "")
        summary = f"{t[:55]!r:58s}"
        print(f"  turn {entry.get('turn', '?')}: {summary}  → {action}  [{strategy}]")

    # -----------------------------------------------------------------------
    # Step 6: confirmation_call scheduled 2h before
    # -----------------------------------------------------------------------
    _step(6, "Scheduling confirmation_call — booking is 1h30m from now (within 2h window)")

    confirmed_say: list[str] = []

    async def _confirm_say(text: str) -> None:
        confirmed_say.append(text)
        _said(text)

    booking = _FakeBooking()
    _info(f"Booking: lead_id={booking.lead_id!r} | scheduled_at={booking.scheduled_at!r}")

    called = await schedule_confirmation_calls([booking], say=_confirm_say, window_hours=2.0)

    assert called == [booking.lead_id], f"Expected [{booking.lead_id!r}], got {called!r}"
    _ok(f"Confirmation call fired for lead_id={called[0]!r} ✓")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    _header("SIMULATION COMPLETE")
    _ok(f"Turns completed    : {outcome.turns}")
    _ok(f"Final status       : {outcome.status}")
    _ok(f"Booking ID         : {outcome.booking_id}")
    _ok(f"Confirmation calls : {len(called)} fired")
    _ok(f"Total say() calls  : {len(said) + len(confirmed_say)}")
    print()


if __name__ == "__main__":
    asyncio.run(run_e2e())
