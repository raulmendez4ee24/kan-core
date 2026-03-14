from __future__ import annotations

import asyncio

from brain.revenue_operators.closer import LeadScorer


def test_lead_scorer_positive_events(tmp_path, monkeypatch) -> None:
    """budget_mentioned, asked_price, replied_fast, opened_link all add positive scores."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        scorer = LeadScorer()

        assert await scorer.update_score("lead_1", "budget_mentioned") == 20
        assert await scorer.update_score("lead_1", "asked_price") == 35   # 20 + 15
        assert await scorer.update_score("lead_1", "replied_fast") == 40  # 35 + 5
        assert await scorer.update_score("lead_1", "opened_link") == 50  # 40 + 10

    asyncio.run(_run())


def test_lead_scorer_negative_event(tmp_path, monkeypatch) -> None:
    """said_thinking subtracts 10 from the score."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        scorer = LeadScorer()

        await scorer.update_score("lead_2", "budget_mentioned")  # +20 → 20
        score = await scorer.update_score("lead_2", "said_thinking")  # −10 → 10
        assert score == 10

    asyncio.run(_run())


def test_lead_scorer_get_score_zero_for_unknown_lead(tmp_path, monkeypatch) -> None:
    """get_score returns 0 when no events have been recorded for a lead."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        scorer = LeadScorer()
        assert await scorer.get_score("ghost_lead") == 0

    asyncio.run(_run())


def test_lead_scorer_get_score_reflects_updates(tmp_path, monkeypatch) -> None:
    """get_score returns the same value as the last update_score call."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        scorer = LeadScorer()

        await scorer.update_score("lead_3", "opened_link")       # +10
        await scorer.update_score("lead_3", "budget_mentioned")   # +20
        assert await scorer.get_score("lead_3") == 30

    asyncio.run(_run())


def test_lead_scorer_unknown_event_type_no_change(tmp_path, monkeypatch) -> None:
    """An unrecognised event type is ignored and leaves the score unchanged."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        scorer = LeadScorer()

        await scorer.update_score("lead_4", "replied_fast")  # +5 → 5
        score = await scorer.update_score("lead_4", "completely_unknown_event")
        assert score == 5
        assert await scorer.get_score("lead_4") == 5

    asyncio.run(_run())


def test_lead_scorer_scores_are_isolated_per_lead(tmp_path, monkeypatch) -> None:
    """Each lead_id has its own independent score."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        scorer = LeadScorer()

        await scorer.update_score("lead_A", "budget_mentioned")  # 20
        await scorer.update_score("lead_B", "asked_price")       # 15

        assert await scorer.get_score("lead_A") == 20
        assert await scorer.get_score("lead_B") == 15

    asyncio.run(_run())
