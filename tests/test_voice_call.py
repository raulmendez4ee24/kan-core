from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from brain.revenue_operators.closer import (
    CloserAction,
    CloserOperator,
    CloserStrategy,
    PipelineLead,
    StrategySelector,
)
from brain.voice_engine import CallOutcome, VoiceCall, _extract_objection, _is_rejection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEAD = PipelineLead(lead_id="lead_voice_test", vertical="dental")


def _make_call(
    transcripts: list[str],
    actions: list[CloserAction] | None = None,
    *,
    track_booking: Any = None,
    max_turns: int | None = None,
    max_duration: float | None = None,
) -> tuple[VoiceCall, list[str]]:
    """Build a VoiceCall with controlled transcript sequence and recorded speech."""
    said: list[str] = []
    transcript_iter = iter(transcripts)

    async def _say(text: str) -> None:
        said.append(text)

    async def _listen() -> str:
        try:
            return next(transcript_iter)
        except StopIteration:
            return ""

    # Default operator: cycles through provided actions or always returns advance_followup
    action_iter = iter(actions or [])

    class _FakeOperator:
        async def build_followup_action(self, lead: PipelineLead) -> CloserAction:
            try:
                return next(action_iter)
            except StopIteration:
                return CloserAction(
                    lead_id=lead.lead_id,
                    action_type="advance_followup",
                    priority=70,
                    confidence=0.72,
                    reason="siguiente paso",
                    message="Cuéntame más.",
                    strategy=CloserStrategy.NURTURE_ONLY,
                )

    async def _fake_track(**kwargs: Any) -> Any:
        class _R:
            id = "booking_test_id"
        return _R()

    vc = VoiceCall(
        lead=_LEAD,
        call_sid="CA_test",
        operator=_FakeOperator(),
        say=_say,
        listen=_listen,
        track_booking=track_booking or _fake_track,
    )
    if max_turns is not None:
        vc.MAX_TURNS = max_turns
    if max_duration is not None:
        vc.MAX_DURATION_SECONDS = max_duration
    return vc, said


def _booking_action(lead_id: str = "lead_voice_test") -> CloserAction:
    return CloserAction(
        lead_id=lead_id,
        action_type="push_booking",
        priority=95,
        confidence=0.90,
        reason="booking",
        message="Perfecto, reservamos una llamada.",
        strategy=CloserStrategy.DIRECT_CLOSE,
    )


def _advance_action(lead_id: str = "lead_voice_test") -> CloserAction:
    return CloserAction(
        lead_id=lead_id,
        action_type="advance_followup",
        priority=70,
        confidence=0.72,
        reason="siguiente paso",
        message="Cuéntame más.",
        strategy=CloserStrategy.NURTURE_ONLY,
    )


# ---------------------------------------------------------------------------
# Rejection detection helpers
# ---------------------------------------------------------------------------


def test_is_rejection_detects_spanish_phrases() -> None:
    assert _is_rejection("No me interesa, gracias") is True
    assert _is_rejection("No gracias, estoy bien") is True
    assert _is_rejection("No quiero más información") is True
    assert _is_rejection("No necesito eso") is True
    assert _is_rejection("Adiós") is True


def test_is_rejection_detects_english_phrases() -> None:
    assert _is_rejection("Not interested, thanks") is True
    assert _is_rejection("No thanks") is True
    assert _is_rejection("Please don't call again") is True


def test_is_rejection_false_for_positive_response() -> None:
    assert _is_rejection("Me parece interesante, cuéntame más") is False
    assert _is_rejection("¿Cuánto cuesta?") is False
    assert _is_rejection("Sí, me interesa") is False


def test_is_rejection_case_insensitive() -> None:
    assert _is_rejection("NO ME INTERESA") is True


# ---------------------------------------------------------------------------
# Objection extraction helpers
# ---------------------------------------------------------------------------


def test_extract_objection_price() -> None:
    assert _extract_objection("Está muy caro para mí") == "price"
    assert _extract_objection("El precio es un problema") == "price"


def test_extract_objection_time() -> None:
    assert _extract_objection("Estoy muy ocupado ahora") == "time"
    assert _extract_objection("No tengo tiempo") == "time"


def test_extract_objection_trust() -> None:
    assert _extract_objection("Necesito más confianza en el servicio") == "trust"


def test_extract_objection_competition() -> None:
    assert _extract_objection("Estoy viendo opciones con otros proveedores") == "competition"


def test_extract_objection_none_for_no_signal() -> None:
    assert _extract_objection("Suena interesante") is None
    assert _extract_objection("") is None


# ---------------------------------------------------------------------------
# VoiceCall.run — normal call flow
# ---------------------------------------------------------------------------


def test_run_says_intro_first() -> None:
    """The intro script must be the first thing said."""
    vc, said = _make_call(["Me interesa.", ""], max_turns=2)

    async def _run() -> None:
        await vc.run("Hola, soy el asistente de ventas.")
        assert said[0] == "Hola, soy el asistente de ventas."

    asyncio.run(_run())


def test_run_booking_confirmed_when_operator_returns_push_booking() -> None:
    vc, said = _make_call(
        transcripts=["Sí, me interesa mucho."],
        actions=[_booking_action()],
    )

    async def _run() -> CallOutcome:
        return await vc.run("Intro")

    outcome = asyncio.run(_run())
    assert outcome.status == "booking_confirmed"
    assert outcome.lead_id == "lead_voice_test"
    assert outcome.call_sid == "CA_test"
    assert outcome.turns == 1


def test_run_booking_says_action_message_before_confirming() -> None:
    vc, said = _make_call(
        transcripts=["Sí quiero."],
        actions=[_booking_action()],
    )

    async def _run() -> None:
        await vc.run("Intro")

    asyncio.run(_run())
    assert "Perfecto, reservamos una llamada." in said


def test_run_booking_calls_track_booking() -> None:
    tracked: list[dict] = []

    async def _capture_track(**kwargs: Any) -> Any:
        tracked.append(kwargs)
        class _R:
            id = "bk_123"
        return _R()

    vc, _ = _make_call(
        transcripts=["Adelante."],
        actions=[_booking_action()],
        track_booking=_capture_track,
    )

    async def _run() -> None:
        await vc.run("Intro")

    asyncio.run(_run())
    assert len(tracked) == 1
    assert tracked[0]["lead_id"] == "lead_voice_test"
    assert "scheduled_at" in tracked[0]


def test_run_booking_outcome_contains_booking_id() -> None:
    async def _track(**kwargs: Any) -> Any:
        class _R:
            id = "bk_xyz"
        return _R()

    vc, _ = _make_call(
        transcripts=["Perfecto."],
        actions=[_booking_action()],
        track_booking=_track,
    )

    outcome = asyncio.run(vc.run("Intro"))
    assert outcome.booking_id == "bk_xyz"


# ---------------------------------------------------------------------------
# Rejection path
# ---------------------------------------------------------------------------


def test_run_rejected_on_explicit_rejection_phrase() -> None:
    vc, _ = _make_call(transcripts=["No me interesa, gracias."])

    outcome = asyncio.run(vc.run("Intro"))
    assert outcome.status == "rejected"
    assert outcome.rejection_reason == "No me interesa, gracias."


def test_run_rejected_does_not_call_track_booking() -> None:
    called: list[bool] = []

    async def _track(**kwargs: Any) -> Any:
        called.append(True)
        class _R:
            id = "x"
        return _R()

    vc, _ = _make_call(
        transcripts=["No gracias, adiós."],
        track_booking=_track,
    )

    asyncio.run(vc.run("Intro"))
    assert called == []


def test_run_rejection_turn_count_correct() -> None:
    vc, _ = _make_call(
        transcripts=["Algo normal.", "No quiero, gracias."],
        actions=[_advance_action(), _advance_action()],
    )

    outcome = asyncio.run(vc.run("Intro"))
    assert outcome.status == "rejected"
    assert outcome.turns == 2


# ---------------------------------------------------------------------------
# Timeout path
# ---------------------------------------------------------------------------


def test_run_timeout_when_max_turns_exceeded() -> None:
    vc, _ = _make_call(
        transcripts=["Interesante." for _ in range(5)],
        max_turns=3,
    )

    outcome = asyncio.run(vc.run("Intro"))
    assert outcome.status == "timeout"


def test_run_timeout_when_duration_exceeded(monkeypatch) -> None:
    """Simulate elapsed time exceeding MAX_DURATION_SECONDS."""
    import brain.voice_engine as ve

    call_count = 0
    real_monotonic = _time_monotonic = __import__("time").monotonic

    def _fake_monotonic() -> float:
        nonlocal call_count
        call_count += 1
        # first call (start): 0; subsequent calls: 400s later
        return 0.0 if call_count == 1 else 400.0

    monkeypatch.setattr("brain.voice_engine._time.monotonic", _fake_monotonic)

    vc, _ = _make_call(transcripts=["Hola."])
    vc.MAX_DURATION_SECONDS = 300.0

    outcome = asyncio.run(vc.run("Intro"))
    assert outcome.status == "timeout"


# ---------------------------------------------------------------------------
# Objection extraction and operator wiring
# ---------------------------------------------------------------------------


def test_run_passes_objection_to_operator_when_detected() -> None:
    """When a price objection is detected, the operator receives a lead with objection='price'."""
    seen_objections: list[str | None] = []

    class _TrackingOperator:
        async def build_followup_action(self, lead: PipelineLead) -> CloserAction:
            seen_objections.append(lead.objection)
            return _advance_action()

    said: list[str] = []
    transcripts = ["Está muy caro.", ""]

    async def _say(text: str) -> None:
        said.append(text)

    async def _listen() -> str:
        return transcripts.pop(0) if transcripts else ""

    vc = VoiceCall(
        lead=_LEAD,
        call_sid="CA_test",
        operator=_TrackingOperator(),
        say=_say,
        listen=_listen,
    )

    async def _run() -> None:
        await vc.run("Intro")

    asyncio.run(_run())
    assert seen_objections[0] == "price"


def test_run_resets_objection_after_handling() -> None:
    """After handling an objection turn, the next turn has no objection."""
    seen_objections: list[str | None] = []

    class _TrackingOperator:
        async def build_followup_action(self, lead: PipelineLead) -> CloserAction:
            seen_objections.append(lead.objection)
            return _advance_action()

    transcripts = ["Está muy caro.", "Sí, suena bien."]

    async def _listen() -> str:
        return transcripts.pop(0) if transcripts else ""

    vc = VoiceCall(
        lead=_LEAD,
        call_sid="CA_test",
        operator=_TrackingOperator(),
        say=AsyncMock(),
        listen=_listen,
    )
    vc.MAX_TURNS = 3

    asyncio.run(vc.run("Intro"))
    # First turn: price objection; second turn: no objection
    assert seen_objections[0] == "price"
    assert seen_objections[1] is None


# ---------------------------------------------------------------------------
# Turn log
# ---------------------------------------------------------------------------


def test_turn_log_records_all_turns() -> None:
    vc, _ = _make_call(
        transcripts=["Turno uno.", "Turno dos.", "No gracias."],
        actions=[_advance_action(), _advance_action()],
    )

    asyncio.run(vc.run("Intro"))
    log = vc.turn_log
    assert len(log) == 3
    assert log[0]["transcript"] == "Turno uno."
    assert log[1]["transcript"] == "Turno dos."
    assert log[2]["transcript"] == "No gracias."


def test_turn_log_records_action_type() -> None:
    vc, _ = _make_call(
        transcripts=["Sí, me interesa."],
        actions=[_booking_action()],
    )

    asyncio.run(vc.run("Intro"))
    assert vc.turn_log[0]["action_type"] == "push_booking"


def test_turn_log_is_read_only_copy() -> None:
    """turn_log returns a copy — mutating it doesn't affect internal state."""
    vc, _ = _make_call(transcripts=["Hola."])
    asyncio.run(vc.run("Intro"))
    log = vc.turn_log
    log.clear()
    assert len(vc.turn_log) > 0


# ---------------------------------------------------------------------------
# Silence / empty transcript
# ---------------------------------------------------------------------------


def test_run_skips_empty_transcripts_and_continues() -> None:
    """Empty transcripts (silence) should not count as turns or trigger rejection."""
    vc, _ = _make_call(
        transcripts=["", "", "Sí, me interesa."],
        actions=[_booking_action()],
        max_turns=5,
    )

    outcome = asyncio.run(vc.run("Intro"))
    assert outcome.status == "booking_confirmed"


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


def test_run_continues_when_operator_raises() -> None:
    """If operator raises on one turn, the loop continues instead of crashing."""
    call_count = 0

    class _FlakyOperator:
        async def build_followup_action(self, lead: PipelineLead) -> CloserAction:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")
            return _booking_action()

    transcripts = ["Interesante.", "Sí quiero."]

    async def _listen() -> str:
        return transcripts.pop(0) if transcripts else ""

    vc = VoiceCall(
        lead=_LEAD,
        call_sid="CA_test",
        operator=_FlakyOperator(),
        say=AsyncMock(),
        listen=_listen,
    )
    vc.MAX_TURNS = 5

    outcome = asyncio.run(vc.run("Intro"))
    assert outcome.status == "booking_confirmed"


def test_run_returns_error_when_intro_say_fails() -> None:
    """If the very first say() call fails, outcome status is 'error'."""

    async def _failing_say(text: str) -> None:
        raise RuntimeError("TTS down")

    vc = VoiceCall(
        lead=_LEAD,
        call_sid="CA_test",
        operator=object(),  # type: ignore[arg-type]  — never reached
        say=_failing_say,
        listen=AsyncMock(return_value=""),
    )

    outcome = asyncio.run(vc.run("Intro"))
    assert outcome.status == "error"
