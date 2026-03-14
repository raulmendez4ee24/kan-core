from __future__ import annotations

import asyncio

from brain.revenue_operators.closer import (
    CloserOperator,
    CloserStrategy,
    LeadScorer,
    PipelineLead,
    StrategySelector,
)


class _FakeScorer:
    """Synchronous score stub — avoids SQLite in strategy unit tests."""

    def __init__(self, score: int) -> None:
        self._score = score

    async def get_score(self, lead_id: str) -> int:  # noqa: ARG002
        return self._score


def _selector(score: int) -> StrategySelector:
    return StrategySelector(scorer=_FakeScorer(score))


# ---------------------------------------------------------------------------
# StrategySelector.select_strategy thresholds
# ---------------------------------------------------------------------------

def test_strategy_direct_close_above_80() -> None:
    async def _run() -> None:
        assert await _selector(90).select_strategy("x") == CloserStrategy.DIRECT_CLOSE
        assert await _selector(81).select_strategy("x") == CloserStrategy.DIRECT_CLOSE

    asyncio.run(_run())


def test_strategy_demos_social_proof_50_to_80() -> None:
    async def _run() -> None:
        assert await _selector(80).select_strategy("x") == CloserStrategy.DEMOS_SOCIAL_PROOF
        assert await _selector(65).select_strategy("x") == CloserStrategy.DEMOS_SOCIAL_PROOF
        assert await _selector(50).select_strategy("x") == CloserStrategy.DEMOS_SOCIAL_PROOF

    asyncio.run(_run())


def test_strategy_value_no_pressure_30_to_49() -> None:
    async def _run() -> None:
        assert await _selector(49).select_strategy("x") == CloserStrategy.VALUE_NO_PRESSURE
        assert await _selector(40).select_strategy("x") == CloserStrategy.VALUE_NO_PRESSURE
        assert await _selector(30).select_strategy("x") == CloserStrategy.VALUE_NO_PRESSURE

    asyncio.run(_run())


def test_strategy_nurture_only_below_30() -> None:
    async def _run() -> None:
        assert await _selector(29).select_strategy("x") == CloserStrategy.NURTURE_ONLY
        assert await _selector(0).select_strategy("x") == CloserStrategy.NURTURE_ONLY
        assert await _selector(-5).select_strategy("x") == CloserStrategy.NURTURE_ONLY

    asyncio.run(_run())


def test_strategy_exact_boundaries() -> None:
    """Verify each cut-point lands on the correct side."""

    async def _run() -> None:
        # >80 boundary
        assert await _selector(81).select_strategy("x") == CloserStrategy.DIRECT_CLOSE
        assert await _selector(80).select_strategy("x") == CloserStrategy.DEMOS_SOCIAL_PROOF
        # >=50 boundary
        assert await _selector(50).select_strategy("x") == CloserStrategy.DEMOS_SOCIAL_PROOF
        assert await _selector(49).select_strategy("x") == CloserStrategy.VALUE_NO_PRESSURE
        # >=30 boundary
        assert await _selector(30).select_strategy("x") == CloserStrategy.VALUE_NO_PRESSURE
        assert await _selector(29).select_strategy("x") == CloserStrategy.NURTURE_ONLY

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# CloserOperator.build_followup_action wiring
# ---------------------------------------------------------------------------

def test_closer_operator_stamps_strategy_on_action(tmp_path, monkeypatch) -> None:
    """build_followup_action stamps the lead's strategy on every CloserAction it returns."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))

        # Score=90 → DIRECT_CLOSE
        op = CloserOperator(strategy_selector=_selector(90))
        lead = PipelineLead(lead_id="lead_direct", engagement_score=10.0)
        action = await op.build_followup_action(lead)
        assert action.strategy == CloserStrategy.DIRECT_CLOSE

    asyncio.run(_run())


def test_closer_operator_nurture_strategy_for_zero_score(tmp_path, monkeypatch) -> None:
    """A brand-new lead with score=0 gets NURTURE_ONLY stamped on its action."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))

        op = CloserOperator(strategy_selector=_selector(0))
        lead = PipelineLead(lead_id="lead_new", engagement_score=5.0)
        action = await op.build_followup_action(lead)
        assert action.strategy == CloserStrategy.NURTURE_ONLY

    asyncio.run(_run())


def test_closer_operator_strategy_applied_to_all_action_types(tmp_path, monkeypatch) -> None:
    """The same strategy is applied regardless of which action branch fires."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))

        op = CloserOperator(strategy_selector=_selector(60))  # DEMOS_SOCIAL_PROOF

        # push_booking branch (ready_for_booking=True)
        booking_lead = PipelineLead(lead_id="lb", ready_for_booking=True)
        assert (await op.build_followup_action(booking_lead)).strategy == CloserStrategy.DEMOS_SOCIAL_PROOF

        # handle_objection branch
        obj_lead = PipelineLead(lead_id="lo", objection="price")
        assert (await op.build_followup_action(obj_lead)).strategy == CloserStrategy.DEMOS_SOCIAL_PROOF

        # advance_followup branch (default)
        plain_lead = PipelineLead(lead_id="lp")
        assert (await op.build_followup_action(plain_lead)).strategy == CloserStrategy.DEMOS_SOCIAL_PROOF

    asyncio.run(_run())


def test_strategy_selector_uses_lead_scorer_from_db(tmp_path, monkeypatch) -> None:
    """StrategySelector reads the real LeadScorer DB when no fake scorer is injected."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        scorer = LeadScorer()
        await scorer.update_score("lead_db", "budget_mentioned")  # +20
        await scorer.update_score("lead_db", "asked_price")       # +15 → 35

        selector = StrategySelector(scorer=scorer)
        # 35 → VALUE_NO_PRESSURE (30 ≤ 35 < 50)
        assert await selector.select_strategy("lead_db") == CloserStrategy.VALUE_NO_PRESSURE

    asyncio.run(_run())
