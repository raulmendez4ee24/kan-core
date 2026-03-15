from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from brain.attribution_engine import get_latest_briefing, get_recent_briefings
from brain.revenue_brain import DailyBriefing, RevenueDiagnosis, RevenueBrain
from brain.revenue_scheduler import RevenueBrainScheduler
from main import app


class _StubRevenueBrain(RevenueBrain):
    async def daily_cycle(self, snapshot=None):  # type: ignore[override]
        return DailyBriefing(
            generated_at="2026-03-13T00:00:00+00:00",
            diagnosis=RevenueDiagnosis(
                generated_at="2026-03-13T00:00:00+00:00",
                summary="stub daily",
                opportunities=[],
                risks=[],
                metrics={},
            ),
            actions=[],
            executed=[],
            recommended=[],
        )

    async def evening_check(self, snapshot=None):  # type: ignore[override]
        return DailyBriefing(
            generated_at="2026-03-13T18:00:00+00:00",
            diagnosis=RevenueDiagnosis(
                generated_at="2026-03-13T18:00:00+00:00",
                summary="stub evening",
                opportunities=[],
                risks=[],
                metrics={},
            ),
            actions=[],
            executed=[],
            recommended=[],
        )


def test_revenue_scheduler_jobs_registered():
    scheduler = RevenueBrainScheduler(_StubRevenueBrain())
    scheduler.configure()
    jobs = {job.id for job in scheduler.scheduler.get_jobs()}
    assert "revenue-daily-cycle" in jobs
    assert "brand-daily-post" in jobs
    assert "brand-weekly-plan" in jobs
    assert "revenue-evening-check" in jobs
    assert "content-publish-hourly" in jobs
    assert "business-mentor-daily-briefing" in jobs
    assert "business-mentor-weekly-review" in jobs


def test_brain_trigger_endpoint(monkeypatch):
    monkeypatch.setenv("CLIENT_TOKENS_JSON", '{"test-client":"test-token"}')
    scheduler = RevenueBrainScheduler(_StubRevenueBrain())
    app.state.revenue_scheduler = scheduler
    with TestClient(app) as client:
        response = client.post(
            "/brain/trigger",
            headers={
                "X-Client-Id": "test-client",
                "X-Client-Token": "test-token",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["briefing"]["diagnosis"]["summary"] == "stub daily"


def test_scheduler_persists_briefings_and_briefing_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(tmp_path / "attribution.sqlite3"))
    monkeypatch.setenv("CLIENT_TOKENS_JSON", '{"test-client":"test-token"}')
    scheduler = RevenueBrainScheduler(_StubRevenueBrain())
    app.state.revenue_scheduler = scheduler

    asyncio.run(scheduler.run_daily_cycle())
    asyncio.run(scheduler.run_evening_check())

    daily = asyncio.run(get_latest_briefing("daily"))
    assert daily is not None
    assert daily.content["diagnosis"]["summary"] == "stub daily"

    recent = asyncio.run(get_recent_briefings(limit=7))
    assert len(recent) == 2

    with TestClient(app) as client:
        list_response = client.get(
            "/brain/briefings",
            headers={
                "X-Client-Id": "test-client",
                "X-Client-Token": "test-token",
            },
        )
        latest_response = client.get(
            "/brain/briefings/latest",
            headers={
                "X-Client-Id": "test-client",
                "X-Client-Token": "test-token",
            },
        )

    assert list_response.status_code == 200
    assert list_response.json()["count"] == 2
    assert latest_response.status_code == 200
    assert latest_response.json()["content"]["diagnosis"]["summary"] == "stub daily"
