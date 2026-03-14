from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
import routes.desktop_autonomy as autonomy_route
from database import get_session
from schemas.desktop_autonomy import DesktopAutonomyRunResponse
from security import require_client_token

TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


class DummySession:
    async def execute(self, _stmt: Any) -> Any:
        return None


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_desktop_autonomy_run_feature_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    monkeypatch.setenv("ENABLE_DESKTOP_AUTONOMY_LOOP", "false")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post("/desktop-autonomy/run", json={"goal": "Abrir CRM"})
    assert response.status_code == 404


def test_desktop_autonomy_run_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_loop(*_args: Any, **_kwargs: Any) -> DesktopAutonomyRunResponse:
        now = datetime.now(timezone.utc)
        return DesktopAutonomyRunResponse(
            run_id="run-1",
            goal="Abrir CRM",
            status="blocked",
            steps=[],
            summary="queued",
            error=None,
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setenv("ENABLE_DESKTOP_AUTONOMY_LOOP", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(autonomy_route, "run_desktop_autonomy_loop", fake_loop)

    response = client.post("/desktop-autonomy/run", json={"goal": "Abrir CRM"})
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-1"
    assert body["status"] == "blocked"


def test_desktop_autonomy_get_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    fake = DesktopAutonomyRunResponse(
        run_id="run-2",
        goal="Objetivo",
        status="completed",
        steps=[],
        summary="ok",
        error=None,
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setenv("ENABLE_DESKTOP_AUTONOMY_LOOP", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setattr(autonomy_route, "get_autonomy_run", lambda _run_id: fake)

    response = client.get("/desktop-autonomy/runs/run-2")
    assert response.status_code == 200
    assert response.json()["run_id"] == "run-2"


def test_desktop_autonomy_research_run_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_research(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "status": "completed",
            "detail": "ok",
            "research": {
                "queries": ["abrir crm", "abrir crm docs", "abrir crm troubleshooting"],
                "providers_used": ["tavily"],
                "total_sources": 6,
                "unique_sources": 5,
                "verified": True,
                "notes": [],
                "top_sources": [
                    {
                        "title": "CRM docs",
                        "url": "https://example.com/docs",
                        "snippet": "snippet",
                        "provider": "tavily",
                        "query": "abrir crm",
                        "published_at": None,
                    }
                ],
            },
            "execution": {
                "run_id": "run-3",
                "goal": "Abrir CRM",
                "status": "completed",
                "steps": [],
                "summary": "done",
                "error": None,
                "created_at": now,
                "updated_at": now,
            },
        }

    monkeypatch.setenv("ENABLE_DESKTOP_AUTONOMY_LOOP", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(autonomy_route, "run_desktop_autonomy_with_research", fake_research)

    response = client.post("/desktop-autonomy/research-run", json={"goal": "Abrir CRM"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["execution"]["run_id"] == "run-3"
