from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
import routes.optimizer as optimizer_route
from brain.business_optimizer import GoalAnalysis
from database import get_session
from schemas.integration import IntegrationAutopilotResponse, IntegrationAutopilotResult
from schemas.optimizer import GoalMetricsSnapshot, GoalRecommendation
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


def _fake_analysis() -> GoalAnalysis:
    metrics = GoalMetricsSnapshot(
        window_days=30,
        total_conversations=12,
        total_messages=80,
        user_messages=50,
        assistant_messages=35,
        unanswered_messages=15,
        leads_detected=25,
        appointments_detected=4,
        appointment_rate=0.16,
        active_integrations=2,
        active_functions=["crm"],
        total_runs=10,
        successful_runs=7,
        failed_runs=3,
        error_rate=30.0,
        avg_duration_ms=140.0,
        api_cost_usd=0.35,
        auto_repair_used=2,
        fallback_used=1,
    )
    recommendations = [
        GoalRecommendation(
            id="op_unanswered_messages",
            priority="high",
            confidence=0.9,
            detected_issue="Mensajes sin respuesta.",
            proposal="Activar respuestas automaticas.",
            expected_impact="Menos leads perdidos.",
            desired_functions=["meta", "crm"],
            request_text="Automatiza respuestas de WhatsApp y CRM.",
        ),
        GoalRecommendation(
            id="op_low_appointment_rate",
            priority="high",
            confidence=0.8,
            detected_issue="Baja conversion a citas.",
            proposal="Activar agenda y seguimiento.",
            expected_impact="Mas citas.",
            desired_functions=["calendar", "crm"],
            request_text="Automatiza agendado y CRM.",
        ),
    ]
    return GoalAnalysis(
        goal="Quiero mas ventas",
        metrics=metrics,
        recommendations=recommendations,
        summary="Resumen objetivo",
        llm_summary="Recomendacion ejecutiva",
    )


def _fake_autopilot_response(function_name: str = "crm") -> IntegrationAutopilotResponse:
    return IntegrationAutopilotResponse(
        detected_functions=[function_name],
        plans=[],
        results=[
            IntegrationAutopilotResult(
                function_name=function_name,
                provider_id="hubspot",
                integration_name=function_name,
                status="integrated",
                reason="ok",
                workflowId="wf-123",
                url="https://n8n.example.com/workflow/wf-123",
            )
        ],
    )


def test_optimizer_analyze_returns_recommendations(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_analysis(*_args: Any, **_kwargs: Any) -> GoalAnalysis:
        return _fake_analysis()

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(optimizer_route, "analyze_goal_driven_plan", fake_analysis)

    response = client.post("/optimizer/analyze", json={"goal": "Quiero mas ventas"})

    assert response.status_code == 200
    body = response.json()
    assert body["goal"] == "Quiero mas ventas"
    assert body["recommendations"]
    assert body["executions"] == []


def test_optimizer_analyze_auto_execute_runs_autopilot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_analysis(*_args: Any, **_kwargs: Any) -> GoalAnalysis:
        return _fake_analysis()

    calls = {"dry_run": 0, "execute": 0}

    async def fake_autopilot(*_args: Any, **kwargs: Any) -> IntegrationAutopilotResponse:
        req = kwargs["req"]
        if req.dry_run:
            calls["dry_run"] += 1
            return IntegrationAutopilotResponse(
                detected_functions=["meta"],
                plans=[],
                results=[
                    IntegrationAutopilotResult(
                        function_name="meta",
                        provider_id="meta_graph",
                        integration_name="meta",
                        status="planned",
                        reason="ok",
                    )
                ],
            )
        calls["execute"] += 1
        return _fake_autopilot_response("meta")

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(optimizer_route, "analyze_goal_driven_plan", fake_analysis)
    monkeypatch.setattr(optimizer_route, "run_integration_autopilot", fake_autopilot)

    response = client.post(
        "/optimizer/analyze",
        json={
            "goal": "Quiero mas ventas",
            "auto_execute": True,
            "max_auto_execute": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["executions"]) == 1
    assert body["executions"][0]["status"] == "implemented"
    assert calls["dry_run"] == 1
    assert calls["execute"] == 1


def test_optimizer_implement_executes_selected_recommendation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_analysis(*_args: Any, **_kwargs: Any) -> GoalAnalysis:
        return _fake_analysis()

    async def fake_autopilot(*_args: Any, **_kwargs: Any) -> IntegrationAutopilotResponse:
        return _fake_autopilot_response("calendar")

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(optimizer_route, "analyze_goal_driven_plan", fake_analysis)
    monkeypatch.setattr(optimizer_route, "run_integration_autopilot", fake_autopilot)

    response = client.post(
        "/optimizer/implement",
        json={
            "goal": "Quiero mas ventas",
            "recommendation_ids": ["op_low_appointment_rate"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] == 1
    assert body["selected_recommendations"] == ["op_low_appointment_rate"]


def test_optimizer_analyze_auto_execute_skips_low_confidence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_analysis(*_args: Any, **_kwargs: Any) -> GoalAnalysis:
        analysis = _fake_analysis()
        analysis.recommendations[0].confidence = 0.55
        return analysis

    async def fake_autopilot(*_args: Any, **_kwargs: Any) -> IntegrationAutopilotResponse:
        raise AssertionError("Autopilot should not run for low-confidence recommendation")

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(optimizer_route, "analyze_goal_driven_plan", fake_analysis)
    monkeypatch.setattr(optimizer_route, "run_integration_autopilot", fake_autopilot)

    response = client.post(
        "/optimizer/analyze",
        json={
            "goal": "Quiero mas ventas",
            "auto_execute": True,
            "max_auto_execute": 1,
            "min_confidence_to_execute": 0.8,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["executions"][0]["status"] == "skipped"
    assert "confidence" in body["executions"][0]["reason"]


def test_optimizer_implement_blocks_when_error_rate_is_high(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def override_session() -> Any:
        yield DummySession()

    async def fake_analysis(*_args: Any, **_kwargs: Any) -> GoalAnalysis:
        analysis = _fake_analysis()
        analysis.metrics.error_rate = 75.0
        return analysis

    async def fake_autopilot(*_args: Any, **_kwargs: Any) -> IntegrationAutopilotResponse:
        raise AssertionError("Autopilot should not run when blocked by high error rate")

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(optimizer_route, "analyze_goal_driven_plan", fake_analysis)
    monkeypatch.setattr(optimizer_route, "run_integration_autopilot", fake_autopilot)

    response = client.post(
        "/optimizer/implement",
        json={
            "goal": "Quiero mas ventas",
            "recommendation_ids": ["op_unanswered_messages"],
            "block_when_error_rate_above": 40.0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] == 1
    assert body["executions"][0]["status"] == "skipped"
    assert "error_rate" in body["executions"][0]["reason"]
