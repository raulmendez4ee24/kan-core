from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict

import pytest
from fastapi.testclient import TestClient

import main
import routes.ai_coo as ai_coo_route
from database import get_session
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


def test_ai_coo_returns_404_when_disabled(client: TestClient) -> None:
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    response = client.post("/optimizer/ai-coo/run", json={"window_hours": 24})
    assert response.status_code == 404


def test_ai_coo_route_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    async def fake_cycle(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        return {
            "organization_id": TEST_CLIENT_ID,
            "computed_at": datetime.now(timezone.utc),
            "execution_mode": "safe",
            "circuit_breaker_open": False,
            "signals": {"metrics_snapshot": {"a": 1}},
            "insights": [{"key": "sales_drop"}],
            "tool_snapshot": {"selected_tool": "api"},
            "action_plan": [
                {
                    "action_type": "predictive_followups",
                    "tool": "api",
                    "risk": "medium",
                    "requires_approval": False,
                    "reason": "x",
                    "payload": {"max_jobs": 5},
                }
            ],
            "executed": False,
            "results": [],
        }

    monkeypatch.setenv("ENABLE_AI_COO", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(ai_coo_route, "run_ai_coo_cycle", fake_cycle)

    response = client.post("/optimizer/ai-coo/run", json={"window_hours": 24, "dry_run": True})
    assert response.status_code == 200
    body = response.json()
    assert body["tool_snapshot"]["selected_tool"] == "api"
    assert body["action_plan"][0]["action_type"] == "predictive_followups"

