from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main
import routes.autonomy as autonomy_route
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


def test_create_case_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    now = datetime.now(timezone.utc)

    async def fake_create_case(*_args: Any, **_kwargs: Any) -> dict:
        return {
            "case": {
                "id": str(uuid4()),
                "organization_id": TEST_CLIENT_ID,
                "case_type": "payment_followthrough",
                "status": "completed",
                "priority_score": 0.9,
                "risk_score": 0.7,
                "current_goal": "Recuperar pago",
                "business_context": {},
                "world_state": {},
                "last_decision": {},
                "last_execution_result": {},
                "resolution_summary": {},
                "created_at": now,
                "updated_at": now,
            },
            "run": {
                "decision": {"recommended_action": "payment_followup"},
                "execution_result": {"dispatched": True},
                "outcome_evaluation": {"matches_expected": True},
                "supervision": {"action": "close_case"},
                "step": {
                    "id": str(uuid4()),
                    "case_id": str(uuid4()),
                    "step_index": 1,
                    "status": "close_case",
                    "decision_payload": {},
                    "execution_payload": {},
                    "execution_result": {},
                    "outcome_evaluation": {},
                    "created_at": now,
                },
            },
        }

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_AUTONOMY_CORE", "true")
    monkeypatch.setattr(autonomy_route, "create_autonomous_case", fake_create_case)

    response = client.post(
        "/autonomy/cases",
        json={"case_type": "payment_followthrough", "current_goal": "Recuperar pago", "run_now": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case"]["case_type"] == "payment_followthrough"
    assert body["decision"]["recommended_action"] == "payment_followup"


def test_create_case_endpoint_accepts_auto_case_type(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def override_session() -> Any:
        yield DummySession()

    now = datetime.now(timezone.utc)

    async def fake_create_case(*_args: Any, **_kwargs: Any) -> dict:
        return {
            "case": {
                "id": str(uuid4()),
                "organization_id": TEST_CLIENT_ID,
                "case_type": "workflow_recovery",
                "status": "waiting",
                "priority_score": 0.8,
                "risk_score": 0.6,
                "current_goal": "Recuperar workflow",
                "business_context": {},
                "world_state": {},
                "last_decision": {},
                "last_execution_result": {},
                "resolution_summary": {},
                "created_at": now,
                "updated_at": now,
            },
            "run": None,
        }

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_AUTONOMY_CORE", "true")
    monkeypatch.setattr(autonomy_route, "create_autonomous_case", fake_create_case)

    response = client.post(
        "/autonomy/cases",
        json={"case_type": "auto", "current_goal": "El webhook fallo y hay que recuperar el workflow", "run_now": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case"]["case_type"] == "workflow_recovery"


def test_business_state_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_business_state(*_args: Any, **_kwargs: Any) -> dict:
        return {
            "snapshot_id": str(uuid4()),
            "organization_id": TEST_CLIENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "active_objectives": 2,
            "critical_risks": ["execution_guard"],
            "cash_exposure": 1200.0,
            "customer_impact_score": 45.0,
            "ops_health_score": 0.72,
            "sla_pressure": 0.33,
            "current_bottlenecks": {"guardrail_warnings": 2},
            "recommended_focus": "payment_followthrough",
            "confidence": 0.8,
            "metrics": {"integration_error_rate": 0.1},
            "window_minutes": 60,
        }

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_AUTONOMY_CORE", "true")
    monkeypatch.setattr(autonomy_route, "get_latest_business_state", fake_business_state)

    response = client.get("/autonomy/business-state")

    assert response.status_code == 200
    body = response.json()
    assert body["recommended_focus"] == "payment_followthrough"
    assert body["critical_risks"] == ["execution_guard"]


def test_rollbackable_guardrails_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    now = datetime.now(timezone.utc)

    async def fake_rollbackables(*_args: Any, **_kwargs: Any) -> list[dict]:
        return [
            {
                "audit_log_id": str(uuid4()),
                "action": "execution_guard.preflight",
                "status": "ok",
                "created_at": now,
                "operation_type": "payment",
                "action_name": "create_payment",
                "rollback_snapshot": {"impact": {"amount_usd": 99.0}},
                "context_signature": "ctx-1",
            }
        ]

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_AUTONOMY_CORE", "true")
    monkeypatch.setattr(autonomy_route, "list_rollbackable_guardrails", fake_rollbackables)

    response = client.get("/autonomy/guardrails/rollbackable")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["operation_type"] == "payment"


def test_case_queue_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    now = datetime.now(timezone.utc)
    case_payload = {
        "id": str(uuid4()),
        "organization_id": TEST_CLIENT_ID,
        "case_type": "sales_execution",
        "status": "waiting",
        "priority_score": 0.88,
        "risk_score": 0.42,
        "current_goal": "Ejecutar seguimiento comercial",
        "business_context": {},
        "world_state": {},
        "last_decision": {},
        "last_execution_result": {},
        "resolution_summary": {},
        "created_at": now,
        "updated_at": now,
    }

    async def fake_list_queue(*_args: Any, **_kwargs: Any) -> list[dict]:
        return [case_payload]

    async def fake_run_queue(*_args: Any, **_kwargs: Any) -> dict:
        return {
            "processed": [
                {
                    "case": case_payload,
                    "decision": {"recommended_action": "advance_sales_pipeline"},
                    "execution_result": {"completed": True},
                    "supervision": {"action": "close_case"},
                }
            ],
            "processed_count": 1,
            "remaining_count": 0,
            "remaining": [],
        }

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_AUTONOMY_CORE", "true")
    monkeypatch.setattr(autonomy_route, "list_autonomous_case_queue", fake_list_queue)
    monkeypatch.setattr(autonomy_route, "run_autonomous_case_queue", fake_run_queue)

    response = client.get("/autonomy/cases/queue")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["case_type"] == "sales_execution"

    run_response = client.post("/autonomy/cases/queue/run?limit=3")
    assert run_response.status_code == 200
    run_body = run_response.json()
    assert run_body["processed_count"] == 1
    assert run_body["processed"][0]["decision"]["recommended_action"] == "advance_sales_pipeline"


def test_case_progress_and_catalog_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> Any:
        yield DummySession()

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_AUTONOMY_CORE", "true")
    monkeypatch.setattr(
        autonomy_route,
        "load_case_progress",
        lambda *_args, **_kwargs: __import__("asyncio").sleep(
            0,
            result={
                "case_id": "case-1",
                "organization_id": TEST_CLIENT_ID,
                "current_goal": "Recover payment",
                "active_domain": "payment_followthrough",
                "last_completed_step": 2,
                "next_expected_step": "close_case",
                "last_successful_tool": "internal_service",
                "last_failure": {},
                "retry_count_by_signature": {},
                "rollback_options": [],
                "open_questions": [],
                "artifacts_created": ["step-1"],
                "updated_at": "2026-03-02T00:00:00+00:00",
            },
        ),
    )
    monkeypatch.setattr(
        autonomy_route,
        "list_skills",
        lambda: [{"name": "payment_recovery", "path": "/tmp/payment_recovery/SKILL.md", "summary": "Recover payments"}],
    )
    monkeypatch.setattr(
        autonomy_route,
        "get_tool_catalog",
        lambda: [{"name": "get_case_state", "description": "Get case", "input_schema": {"type": "object"}, "reversible": True}],
    )

    progress_response = client.get("/autonomy/cases/11111111-1111-1111-1111-111111111111/progress")
    assert progress_response.status_code == 200
    assert progress_response.json()["active_domain"] == "payment_followthrough"

    skills_response = client.get("/autonomy/skills")
    assert skills_response.status_code == 200
    assert skills_response.json()[0]["name"] == "payment_recovery"

    tool_response = client.get("/autonomy/tools/catalog")
    assert tool_response.status_code == 200
    assert tool_response.json()["items"][0]["name"] == "get_case_state"
