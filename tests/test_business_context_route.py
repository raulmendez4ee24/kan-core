from typing import Any, AsyncGenerator, Dict

import pytest
from fastapi.testclient import TestClient

import main
import routes.business_context as business_context_route
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


def test_business_context_run_returns_404_when_disabled(client: TestClient) -> None:
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    response = client.post("/optimizer/context/run", json={"window_hours": 24})
    assert response.status_code == 404


def test_business_context_run_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    async def fake_cycle(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        return {
            "analysis": {
                "context": {"window_hours": 24},
                "insights": [{"key": "sales_drop", "severity": "warning"}],
            },
            "created_recommendations": ["rec-1"],
            "campaign": {"executed": False, "detail": {}},
        }

    monkeypatch.setenv("ENABLE_BUSINESS_CONTEXT_REASONING", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(business_context_route, "run_business_context_cycle", fake_cycle)

    response = client.post("/optimizer/context/run", json={"window_hours": 24})
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["insights"][0]["key"] == "sales_drop"
    assert body["created_recommendations"] == ["rec-1"]
