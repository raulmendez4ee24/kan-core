from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from brain.opportunity_pipeline import (
    auto_approve,
    create_opportunity,
    get_opportunity,
    get_opportunity_transitions,
    get_pipeline_by_status,
    transition_opportunity,
)


@pytest.fixture(autouse=True)
def _pipeline_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPPORTUNITY_PIPELINE_DB_PATH", str(tmp_path / "opportunity_pipeline.sqlite3"))
    monkeypatch.delenv("RAUL_WHATSAPP_TO", raising=False)
    monkeypatch.delenv("YCLOUD_WHATSAPP_FROM", raising=False)
    yield


def test_create_and_transition_pipeline() -> None:
    async def scenario() -> None:
        record = await create_opportunity(
            domain="hunt",
            business_name="Clinica Norte",
            vertical="clinics",
            source="google_maps",
            score=72.5,
            payload={"city": "Guadalajara"},
        )

        assert record.status == "DETECTED"
        assert record.business_name == "Clinica Norte"

        evaluated = await transition_opportunity(
            record.id,
            "EVALUATED",
            reason="manual_review",
            payload_patch={"reviewed": True},
        )
        assert evaluated.status == "EVALUATED"
        assert evaluated.payload["reviewed"] is True

        transitions = await get_opportunity_transitions(record.id)
        assert [transition.to_status for transition in transitions] == ["DETECTED", "EVALUATED"]

    asyncio.run(scenario())


def test_auto_approve_marks_high_score_as_validated() -> None:
    async def scenario() -> None:
        record = await create_opportunity(
            domain="hunt",
            business_name="Dental Prime",
            vertical="dentists",
            source="google_maps",
            score=91.0,
        )

        result = await auto_approve(record.id, score=91.0)

        assert result.auto_validated is True
        assert result.status == "VALIDATED"
        updated = await get_opportunity(record.id)
        assert updated is not None
        assert updated.status == "VALIDATED"
        assert updated.approved_automatically is True

    asyncio.run(scenario())


def test_auto_approve_requests_mid_score_without_breaking_when_whatsapp_not_configured() -> None:
    async def scenario() -> None:
        record = await create_opportunity(
            domain="hunt",
            business_name="Barberia Centro",
            vertical="barbershops",
            source="google_maps",
            score=78.0,
        )

        result = await auto_approve(record.id, score=78.0)

        assert result.auto_validated is False
        assert result.approval_requested is True
        assert result.whatsapp_sent is False
        updated = await get_opportunity(record.id)
        assert updated is not None
        assert updated.status == "EVALUATED"
        assert updated.approval_requested is True

    asyncio.run(scenario())


def test_pipeline_endpoint_shape_source() -> None:
    async def scenario() -> None:
        alpha = await create_opportunity(
            domain="hunt",
            business_name="Spa Aurora",
            vertical="spas",
            source="instagram",
            score=68.0,
        )
        await transition_opportunity(alpha.id, "EVALUATED", reason="scored")

        beta = await create_opportunity(
            domain="hunt",
            business_name="Restaurante Sol",
            vertical="restaurants",
            source="google_maps",
            score=89.0,
        )
        await auto_approve(beta.id, score=89.0)

        pipeline = await get_pipeline_by_status()

        assert len(pipeline["DETECTED"]) == 0
        assert [record.business_name for record in pipeline["EVALUATED"]] == ["Spa Aurora"]
        assert [record.business_name for record in pipeline["VALIDATED"]] == ["Restaurante Sol"]

    asyncio.run(scenario())
