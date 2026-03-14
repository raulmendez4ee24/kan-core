from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main
import routes.predictions as predictions_route
import routes.predictive as predictive_route
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


def test_predictions_churn_returns_404_when_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_CRM_INTERNAL", "true")
    monkeypatch.setenv("ENABLE_PREDICTIVE_AUTOMATION", "false")

    response = client.get("/predictions/leads/churn?refresh=true")
    assert response.status_code == 404


def test_predictions_churn_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Pred:
        def __init__(self) -> None:
            now = datetime.now(timezone.utc)
            self.entity_id = str(uuid4())
            self.prediction_value = 0.7
            self.confidence = 0.8
            self.model_name = "lead-churn-heuristic-v1"
            self.features = {"x": 1}
            self.valid_until = now
            self.created_at = now

    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    async def fake_generate(*_args: Any, **_kwargs: Any) -> List[Any]:
        return [_Pred()]

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_CRM_INTERNAL", "true")
    monkeypatch.setenv("ENABLE_PREDICTIVE_AUTOMATION", "true")
    monkeypatch.setattr(predictions_route, "generate_lead_churn_predictions", fake_generate)

    response = client.get("/predictions/leads/churn?refresh=true")
    assert response.status_code == 200
    body = response.json()
    assert body["predictions"][0]["churn_risk"] == 0.7


def test_predictive_followups_route_404_when_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_CRM_INTERNAL", "true")
    monkeypatch.setenv("ENABLE_PREDICTIVE_AUTOMATION", "false")

    response = client.post("/predictive/followups/run", json={"max_jobs": 5, "dry_run": True})
    assert response.status_code == 404


def test_predictive_followups_route_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Job:
        def __init__(self) -> None:
            now = datetime.now(timezone.utc)
            self.id = uuid4()
            self.lead_id = uuid4()
            self.status = "scheduled"
            self.scheduled_for = now
            self.executed_at = None
            self.decision_reason = "x"
            self.payload: Dict[str, Any] = {}
            self.result: Dict[str, Any] = {}

    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    async def fake_run_followups(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        return {"jobs": [_Job()], "executed": 0, "skipped": 0}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_CRM_INTERNAL", "true")
    monkeypatch.setenv("ENABLE_PREDICTIVE_AUTOMATION", "true")
    monkeypatch.setattr(predictive_route, "run_followups", fake_run_followups)

    response = client.post("/predictive/followups/run", json={"max_jobs": 5, "dry_run": True})
    assert response.status_code == 200
    body = response.json()
    assert body["jobs"]

