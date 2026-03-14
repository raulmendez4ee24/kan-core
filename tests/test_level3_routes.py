from datetime import datetime, timezone
from typing import Any, List
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main
import routes.agents as agents_route
import routes.alerts as alerts_route
import routes.auth as auth_route
import routes.autonomy as autonomy_route
import routes.predictions as predictions_route
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


def test_agents_orchestrate_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_orchestrate_agents(**_kwargs: Any) -> dict:
        return {
            "goal": "Aumentar ventas",
            "strategy": "multi-agent-coordination",
            "actions": [
                {
                    "agent": "sales",
                    "action": "create_growth_automation",
                    "rationale": "Maximizar conversion",
                    "tool": "integrations.autopilot",
                    "payload": {},
                }
            ],
            "executed": False,
            "execution_result": {"dispatched": False, "count": 1},
        }

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setenv("ENABLE_MULTI_AGENT", "true")
    monkeypatch.setattr(agents_route, "orchestrate_agents", fake_orchestrate_agents)

    response = client.post(
        "/agents/orchestrate",
        json={"goal": "Aumentar ventas", "dry_run": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["goal"] == "Aumentar ventas"
    assert body["actions"][0]["agent"] == "sales"


def test_auth_token_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_write_audit_log(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setenv("AUTH_ADMIN_KEY", "secret-admin")
    monkeypatch.setattr(auth_route, "write_audit_log", fake_write_audit_log)
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/auth/token",
        json={
            "user_id": str(uuid4()),
            "organization_id": str(uuid4()),
            "role": "SUPER_ADMIN",
            "permissions": ["admin.audit.read"],
            "expires_in_minutes": 30,
        },
        headers={"X-Admin-Key": "secret-admin"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["token_type"] == "bearer"


def test_autonomy_monitor_snapshot_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Snapshot:
        window_minutes = 5
        window_start = datetime.now(timezone.utc)
        window_end = datetime.now(timezone.utc)
        metrics = {"leads_incoming": 4, "integration_error_rate": 0.12}

    async def override_session() -> Any:
        yield DummySession()

    async def fake_snapshot(*_args: Any, **_kwargs: Any) -> Any:
        return _Snapshot()

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_AUTONOMY_CORE", "true")
    monkeypatch.setattr(autonomy_route, "build_metrics_snapshot", fake_snapshot)

    response = client.get("/autonomy/monitor/snapshot?refresh=true")

    assert response.status_code == 200
    body = response.json()
    assert body["window_minutes"] == 5
    assert body["metrics"]["leads_incoming"] == 4
    assert body["metrics"]["execution_guard"]["events"] == 0


def test_autonomy_guardrail_rollback_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_rollback(*_args: Any, **_kwargs: Any) -> dict:
        return {
            "audit_log_id": str(uuid4()),
            "source_action": "execution_guard.preflight",
            "rollback": {"domain": "payment", "status": "dispatched"},
        }

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_AUTONOMY_CORE", "true")
    monkeypatch.setattr(autonomy_route, "run_execution_guard_rollback", fake_rollback)

    response = client.post(f"/autonomy/guardrails/rollback/{uuid4()}")

    assert response.status_code == 200
    body = response.json()
    assert body["source_action"] == "execution_guard.preflight"
    assert body["rollback"]["status"] == "dispatched"


def test_alerts_incidents_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Incident:
        def __init__(self) -> None:
            now = datetime.now(timezone.utc)
            self.id = uuid4()
            self.rule_id = None
            self.incident_key = "error_rate:gte:0.3"
            self.title = "Error rate triggered"
            self.description = "Error alto"
            self.severity = "warning"
            self.status = "open"
            self.first_seen_at = now
            self.last_seen_at = now
            self.resolved_at = None
            self.incident_metadata = {"metric_key": "integration_error_rate"}
            self.created_at = now

    async def override_session() -> Any:
        yield DummySession()

    async def fake_list_incidents(*_args: Any, **_kwargs: Any) -> List[Any]:
        return [_Incident()]

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_ADVANCED_ALERTS", "true")
    monkeypatch.setattr(alerts_route, "list_incidents", fake_list_incidents)

    response = client.get("/alerts/incidents?refresh=false")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["incident_key"] == "error_rate:gte:0.3"


def test_predictions_leads_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Prediction:
        def __init__(self) -> None:
            now = datetime.now(timezone.utc)
            self.entity_id = str(uuid4())
            self.prediction_value = 0.77
            self.confidence = 0.81
            self.model_name = "lead-conversion-heuristic-v1"
            self.features = {"score": 0.6}
            self.valid_until = now
            self.created_at = now

    async def override_session() -> Any:
        yield DummySession()

    async def fake_generate_lead_predictions(*_args: Any, **_kwargs: Any) -> List[Any]:
        return [_Prediction()]

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_CRM_INTERNAL", "true")
    monkeypatch.setattr(predictions_route, "generate_lead_predictions", fake_generate_lead_predictions)

    response = client.get("/predictions/leads?refresh=true")

    assert response.status_code == 200
    body = response.json()
    assert body["predictions"]
    assert body["predictions"][0]["conversion_probability"] == 0.77
