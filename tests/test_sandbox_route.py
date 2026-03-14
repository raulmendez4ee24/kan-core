from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
import routes.sandbox as sandbox_route
from schemas.sandbox import SandboxResult, SandboxStep
from security import require_client_token

TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_sandbox_run_exposes_repeated_failures_stopped_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSandbox:
        def __init__(self, *, engine: Any) -> None:
            self.engine = engine

        async def run(self, **_kwargs: Any) -> SandboxResult:
            return SandboxResult(
                run_id="sandbox-run-1",
                goal="fetch orders",
                steps=[
                    SandboxStep(
                        step_index=0,
                        thought="Try orders endpoint",
                        action={
                            "contract_version": "1.0",
                            "action_type": "http",
                            "action": "GET /v1/orders",
                            "integration": "erp_api",
                            "arguments": {"url": "https://erp.example.com/v1/orders"},
                            "rationale": "fetch orders",
                        },
                        observation="[error] upstream 500",
                        observation_confidence=0.0,
                    ),
                    SandboxStep(step_index=1, thought="[bloqueo por fallos repetidos]", is_final=True),
                ],
                final_answer=(
                    "Se detecto un bloqueo: la misma accion fallo 3 veces seguidas "
                    "(http / GET /v1/orders). Detengo mas reintentos para evitar un loop."
                ),
                committed=False,
                pending_commit=False,
                iterations=3,
                total_tokens=150,
                stopped_reason="repeated_failures",
                client_id=TEST_CLIENT_ID,
                session_id="sess-1",
            )

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setattr(sandbox_route, "LLMSandbox", FakeSandbox)
    monkeypatch.setattr(
        sandbox_route.CognitiveEngine,
        "get_instance",
        classmethod(lambda cls: object()),
    )

    response = client.post(
        "/sandbox/run",
        json={
            "goal": "fetch orders",
            "session_id": "sess-1",
            "max_iterations": 5,
            "auto_commit": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["stopped_reason"] == "repeated_failures"
    assert body["iterations"] == 3
    assert body["pending_commit"] is False
    assert "fallo 3 veces" in body["final_answer"]

