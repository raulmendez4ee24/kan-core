from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.dashboard import router


def test_dashboard_requires_query_token(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret123")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/dashboard")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid dashboard token"


def test_dashboard_renders_html(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret123")
    app = FastAPI()
    app.include_router(router)

    async def _fake_collect_dashboard_data(_request):
        return {
            "generated_at": "2026-03-16T10:00:00-06:00",
            "api_statuses": [
                {"name": "YCloud", "status": "up", "detail": "200 · 121ms"},
                {"name": "Stripe", "status": "down", "detail": "missing key"},
            ],
            "revenue": {
                "today": 12000.0,
                "today_goal": 3200.0,
                "month": 88000.0,
                "month_goal": 100000.0,
            },
            "crm": {
                "active_leads": 9,
                "pipeline": {"new": 3, "proposal": 2, "followup": 4},
            },
            "outbound": {
                "today_count": 4,
                "items": [
                    {
                        "business_name": "Clínica Xaviera",
                        "normalized_phone": "+523321487586",
                        "template_name": "outbound_prospecto_v1",
                        "sent_at": "2026-03-16T09:12:00-06:00",
                    }
                ],
            },
            "instagram_posts": [
                {
                    "hook": "Tu competencia ya lo automatizó.",
                    "media_id": "media_1",
                    "updated_at": "2026-03-16T09:30:00-06:00",
                }
            ],
            "scheduler_jobs": [
                {
                    "id": "revenue-daily-cycle",
                    "last_run": "2026-03-16T06:00:00-06:00",
                    "next_run": "2026-03-17T06:00:00-06:00",
                }
            ],
            "daily_briefing": {
                "created_at": "2026-03-16T06:00:00-06:00",
                "content": {
                    "diagnosis": {
                        "summary": "Yo voy a priorizar captación, cierre y retención.",
                        "opportunities": ["Hay 9 leads activos que pueden convertirse hoy."],
                        "risks": ["Ayer no se registró revenue."],
                    },
                    "recommended": [{"operator": "hunter", "action_type": "prepare_outreach"}],
                    "executed": [{"operator": "creator", "action_type": "mark_for_creative_refresh"}],
                },
            },
        }

    monkeypatch.setattr("routes.dashboard.collect_dashboard_data", _fake_collect_dashboard_data)
    client = TestClient(app)

    response = client.get("/dashboard?token=secret123")

    assert response.status_code == 200
    body = response.text
    assert "KAN LOGIC / TERMINAL" in body
    assert "API Status" in body
    assert "Clínica Xaviera" in body
    assert "Tu competencia ya lo automatizó." in body
    assert "Yo voy a priorizar captación, cierre y retención." in body
