from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from brain.revenue_operators.closer import (
    CloserOperator,
    CloserStrategy,
    ObjectionHandler,
    ObjectionResponse,
    PipelineLead,
    StrategySelector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _handler(tmp_path, monkeypatch) -> ObjectionHandler:
    monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
    return ObjectionHandler()


class _FakeScorer:
    async def get_score(self, lead_id: str) -> int:  # noqa: ARG002
        return 0


def _operator(handler: ObjectionHandler) -> CloserOperator:
    return CloserOperator(
        strategy_selector=StrategySelector(scorer=_FakeScorer()),
        objection_handler=handler,
    )


# ---------------------------------------------------------------------------
# ObjectionHandler.get_best_response — fallback behaviour
# ---------------------------------------------------------------------------


def test_get_best_response_fallback_known_objection(tmp_path, monkeypatch) -> None:
    """Known objection type with no trained data returns the matching built-in text."""

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        resp = await handler.get_best_response("price", "general")
        assert isinstance(resp, ObjectionResponse)
        assert resp.is_fallback is True
        assert resp.response_id is None
        assert "precio" in resp.response_text.lower() or "price" in resp.response_text.lower()
        assert resp.objection_type == "price"
        assert resp.vertical == "general"
        assert resp.conversion_rate == 0.0

    asyncio.run(_run())


def test_get_best_response_fallback_unknown_objection(tmp_path, monkeypatch) -> None:
    """Unknown objection type with no trained data returns the generic default."""

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        resp = await handler.get_best_response("some_random_objection", "dental")
        assert resp.is_fallback is True
        assert resp.response_id is None
        assert len(resp.response_text) > 10

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# ObjectionHandler.add_response + get_best_response
# ---------------------------------------------------------------------------


def test_add_response_returns_id(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        rid = await handler.add_response("price", "dental", "Ofrecemos financiamiento flexible.", 0.45)
        assert isinstance(rid, str) and len(rid) == 64  # SHA-256 hex

    asyncio.run(_run())


def test_get_best_response_returns_trained_over_fallback(tmp_path, monkeypatch) -> None:
    """A trained response is preferred over the generic fallback."""

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        await handler.add_response("price", "dental", "Ofrecemos financiamiento flexible.", 0.45)
        resp = await handler.get_best_response("price", "dental")
        assert resp.is_fallback is False
        assert resp.response_id is not None
        assert resp.response_text == "Ofrecemos financiamiento flexible."
        assert resp.conversion_rate == pytest.approx(0.45)

    asyncio.run(_run())


def test_get_best_response_returns_highest_conversion_rate(tmp_path, monkeypatch) -> None:
    """When multiple responses exist, the one with highest conversion_rate wins."""

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        await handler.add_response("price", "restaurant", "Respuesta A", 0.30)
        await handler.add_response("price", "restaurant", "Respuesta B", 0.75)
        await handler.add_response("price", "restaurant", "Respuesta C", 0.55)
        resp = await handler.get_best_response("price", "restaurant")
        assert resp.response_text == "Respuesta B"
        assert resp.conversion_rate == pytest.approx(0.75)

    asyncio.run(_run())


def test_get_best_response_is_scoped_by_vertical(tmp_path, monkeypatch) -> None:
    """Responses for vertical A must not bleed into vertical B."""

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        await handler.add_response("time", "dental", "Respuesta dental", 0.80)
        # No response for (time, restaurant)
        resp = await handler.get_best_response("time", "restaurant")
        assert resp.is_fallback is True

    asyncio.run(_run())


def test_get_best_response_is_scoped_by_objection_type(tmp_path, monkeypatch) -> None:
    """A response trained for 'price' must not appear when querying 'trust'."""

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        await handler.add_response("price", "general", "Respuesta precio", 0.90)
        resp = await handler.get_best_response("trust", "general")
        assert resp.is_fallback is True

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# ObjectionHandler.update_conversion_rate
# ---------------------------------------------------------------------------


def test_update_conversion_rate_changes_ranking(tmp_path, monkeypatch) -> None:
    """After update_conversion_rate, the new winner is served."""

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        rid_a = await handler.add_response("trust", "general", "Respuesta A", 0.20)
        await handler.add_response("trust", "general", "Respuesta B", 0.50)

        # Elevate A above B
        updated = await handler.update_conversion_rate(rid_a, 0.95)
        assert updated is True

        resp = await handler.get_best_response("trust", "general")
        assert resp.response_text == "Respuesta A"
        assert resp.conversion_rate == pytest.approx(0.95)

    asyncio.run(_run())


def test_update_conversion_rate_unknown_id_returns_false(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        result = await handler.update_conversion_rate("nonexistent_id_" + "x" * 48, 0.99)
        assert result is False

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Usage log written on every lookup
# ---------------------------------------------------------------------------


def test_usage_logged_for_trained_response(tmp_path, monkeypatch) -> None:
    """Each get_best_response call writes one entry to objection_usage_log."""

    import aiosqlite

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        await handler.add_response("price", "general", "Respuesta log test", 0.60)
        await handler.get_best_response("price", "general", lead_id="lead_log_1")
        await handler.get_best_response("price", "general", lead_id="lead_log_2")

        db = await aiosqlite.connect(handler._db_path())
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS cnt FROM objection_usage_log WHERE objection_type = 'price' AND vertical = 'general'"
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        assert row["cnt"] == 2

    asyncio.run(_run())


def test_usage_logged_for_fallback(tmp_path, monkeypatch) -> None:
    """Fallback lookups are also logged (is_fallback=1)."""

    import aiosqlite

    async def _run() -> None:
        handler = _handler(tmp_path, monkeypatch)
        await handler.get_best_response("competition", "general", lead_id="lead_fb")

        db = await aiosqlite.connect(handler._db_path())
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute(
                "SELECT is_fallback FROM objection_usage_log WHERE lead_id = 'lead_fb'"
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        assert row is not None
        assert row["is_fallback"] == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# CloserOperator wiring
# ---------------------------------------------------------------------------


def test_closer_operator_objection_branch_uses_handler_text(tmp_path, monkeypatch) -> None:
    """build_followup_action uses ObjectionHandler response text in the message."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        handler = ObjectionHandler()
        await handler.add_response("price", "dental", "Texto de objeción entrenado", 0.80)

        op = _operator(handler)
        # Set last_contact_at recently so age_days < 7 — objection branch must fire
        lead = PipelineLead(
            lead_id="lead_obj",
            objection="price",
            vertical="dental",
            last_contact_at=datetime.now(timezone.utc),
        )
        action = await op.build_followup_action(lead)

        assert action.action_type == "handle_objection"
        assert action.message == "Texto de objeción entrenado"
        assert action.objection_response_id is not None

    asyncio.run(_run())


def test_closer_operator_objection_branch_fallback_when_no_memory(tmp_path, monkeypatch) -> None:
    """build_followup_action falls back gracefully when no trained response exists."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        handler = ObjectionHandler()
        op = _operator(handler)
        lead = PipelineLead(
            lead_id="lead_noop",
            objection="time",
            vertical="general",
            last_contact_at=datetime.now(timezone.utc),
        )
        action = await op.build_followup_action(lead)

        assert action.action_type == "handle_objection"
        assert len(action.message) > 10
        assert action.objection_response_id is None  # fallback → no stored ID

    asyncio.run(_run())


def test_closer_operator_objection_response_id_stamped(tmp_path, monkeypatch) -> None:
    """objection_response_id on CloserAction matches what ObjectionHandler stored."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        handler = ObjectionHandler()
        rid = await handler.add_response("trust", "restaurant", "Respuesta confianza", 0.70)

        op = _operator(handler)
        lead = PipelineLead(
            lead_id="lead_rid",
            objection="trust",
            vertical="restaurant",
            last_contact_at=datetime.now(timezone.utc),
        )
        action = await op.build_followup_action(lead)

        assert action.objection_response_id == rid

    asyncio.run(_run())


def test_closer_operator_non_objection_branches_have_no_response_id(tmp_path, monkeypatch) -> None:
    """Non-objection branches leave objection_response_id as None."""

    async def _run() -> None:
        monkeypatch.setenv("LEAD_SCORER_DB_PATH", str(tmp_path / "scores.sqlite3"))
        op = CloserOperator(
            strategy_selector=StrategySelector(scorer=_FakeScorer()),
            objection_handler=ObjectionHandler(),
        )
        lead = PipelineLead(lead_id="lead_plain")  # no objection, no booking flag
        action = await op.build_followup_action(lead)
        assert action.objection_response_id is None

    asyncio.run(_run())
