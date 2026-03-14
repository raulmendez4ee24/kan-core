import uuid
from datetime import datetime, timezone
from typing import Any, List

import pytest
from fastapi.testclient import TestClient

import main
import routes.integrations as integrations_route
import routes.shopify as shopify_route
import routes.webhooks as webhooks_route
from database import get_session
from models import ApiIntegration, IntegrationMemory, IntegrationRunLog
from schemas.integration import EndpointSpec
from security import require_client_token

TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def first(self) -> Any:
        return self._value

    def all(self) -> List[Any]:
        if self._value is None:
            return []
        if isinstance(self._value, list):
            return self._value
        return [self._value]


class _ExecuteResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._value)


class FakeAsyncSession:
    def __init__(self, execute_results: List[Any]) -> None:
        self._execute_results = list(execute_results)
        self.added: List[Any] = []
        self.commits = 0

    async def execute(self, _stmt: Any) -> _ExecuteResult:
        if not self._execute_results:
            raise AssertionError("No queued execute result for this test")
        return _ExecuteResult(self._execute_results.pop(0))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: Any) -> None:
        now = datetime.now(timezone.utc)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        obj.updated_at = now


def _make_integration(*, name: str, active: bool = True) -> ApiIntegration:
    now = datetime.now(timezone.utc)
    integ = ApiIntegration(
        id=uuid.uuid4(),
        client_id=uuid.UUID(TEST_CLIENT_ID),
        name=name,
        base_url="https://api.example.com",
        auth_type="none",
        header_name=None,
        integration_metadata={"region": "mx"},
        is_active=active,
        created_at=now,
        updated_at=now,
    )
    return integ


def _make_run_log(*, status: str = "integrated") -> IntegrationRunLog:
    now = datetime.now(timezone.utc)
    return IntegrationRunLog(
        id=uuid.uuid4(),
        client_id=uuid.UUID(TEST_CLIENT_ID),
        function_name="payments",
        provider_id="stripe",
        integration_name="payments",
        industry="general",
        status=status,
        attempt_number=1,
        auto_repair_used=False,
        fallback_used=False,
        duration_ms=120,
        cost_usd=0.01,
        error_code=None if status == "integrated" else "endpoint_changed",
        error_message=None if status == "integrated" else "failed",
        diagnostics={"k": "v"},
        created_at=now,
    )


def _make_memory() -> IntegrationMemory:
    now = datetime.now(timezone.utc)
    return IntegrationMemory(
        id=uuid.uuid4(),
        function_name="payments",
        provider_id="stripe",
        industry="general",
        success_count=10,
        failure_count=2,
        avg_duration_ms=150.0,
        avg_cost_usd=0.01,
        last_status="integrated",
        last_error_code=None,
        memory_metadata={"note": "stable"},
        last_used_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_healthz_e2e(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_e2e(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "kan-core"


def test_favicon_e2e(client: TestClient) -> None:
    response = client.get("/favicon.ico")
    assert response.status_code == 204


def test_meta_verify_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_VERIFY_TOKEN", "verify-123")

    response = client.get(
        "/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-123",
            "hub.challenge": "challenge-ok",
        },
    )

    assert response.status_code == 200
    assert response.text == "challenge-ok"


def test_meta_verify_rejects_invalid_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_VERIFY_TOKEN", "verify-123")

    response = client.get(
        "/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "challenge-ok",
        },
    )

    assert response.status_code == 403


def test_meta_receive_rejects_invalid_signature(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhooks_route, "verify_meta_signature", lambda _raw, _sig: False)

    response = client.post(
        "/webhooks/meta",
        json={"object": "page", "entry": []},
        headers={"X-Hub-Signature-256": "invalid"},
    )

    assert response.status_code == 401


def test_meta_receive_dispatches_background_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: List[Any] = []

    async def fake_handler(payload: dict) -> None:
        calls.append(payload)

    monkeypatch.setattr(webhooks_route, "verify_meta_signature", lambda _raw, _sig: True)
    monkeypatch.setattr(webhooks_route, "handle_meta_event", fake_handler)

    payload = {"object": "whatsapp_business_account", "entry": [{"id": "entry-1"}]}
    response = client.post(
        "/webhooks/meta",
        json=payload,
        headers={"X-Hub-Signature-256": "ok"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert calls and calls[0]["entry"][0]["id"] == "entry-1"


def test_client_webhook_dispatches_when_authorized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: List[Any] = []

    async def fake_handler(client_id: str, payload: dict) -> None:
        calls.append((client_id, payload))

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setattr(webhooks_route, "handle_client_event", fake_handler)

    response = client.post("/webhooks/client", json={"type": "ping"})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert calls == [(TEST_CLIENT_ID, {"type": "ping"})]


def test_shopify_webhook_rejects_invalid_hmac(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shopify_route, "verify_shopify_hmac", lambda _raw, _sig: False)

    response = client.post("/webhooks/shopify", json={"id": 123})

    assert response.status_code == 401


def test_shopify_orders_create_dispatches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: List[Any] = []

    async def fake_handler(payload: dict, topic: str, shop: str) -> None:
        calls.append((payload, topic, shop))

    monkeypatch.setattr(shopify_route, "verify_shopify_hmac", lambda _raw, _sig: True)
    monkeypatch.setattr(shopify_route, "handle_shopify_event", fake_handler)

    response = client.post(
        "/webhooks/shopify",
        json={"id": 123},
        headers={
            "X-Shopify-Hmac-Sha256": "valid",
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Shop-Domain": "acme.myshopify.com",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert calls == [({"id": 123}, "orders/create", "acme.myshopify.com")]


def test_integrations_create_e2e(client: TestClient) -> None:
    fake_session = FakeAsyncSession(execute_results=[None])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/integrations",
        json={
            "name": "crm",
            "base_url": "https://api.example.com",
            "auth_type": "none",
            "metadata": {"region": "mx"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "crm"
    assert body["metadata"] == {"region": "mx"}
    assert fake_session.commits == 1
    assert len(fake_session.added) == 1


def test_integrations_update_e2e(client: TestClient) -> None:
    existing = _make_integration(name="crm")
    fake_session = FakeAsyncSession(execute_results=[existing])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/integrations",
        json={
            "name": "crm",
            "base_url": "https://new-api.example.com",
            "auth_type": "apiKey",
            "header_name": "X-API-Key",
            "metadata": {"region": "us"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["base_url"] == "https://new-api.example.com"
    assert body["metadata"] == {"region": "us"}
    assert fake_session.commits == 1


def test_generate_workflow_e2e_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = _make_integration(name="crm")
    fake_session = FakeAsyncSession(execute_results=[existing])

    async def override_session() -> Any:
        yield fake_session

    async def fake_create_workflow(_wf: dict) -> dict:
        return {"id": "wf-123", "name": "CRM Workflow"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(integrations_route, "create_workflow", fake_create_workflow)
    monkeypatch.setenv("N8N_WEB_URL", "https://n8n.example.com")

    response = client.post(
        "/integrations/crm/generate-workflow",
        json={
            "endpoints": [
                {
                    "name": "GetLeads",
                    "method": "GET",
                    "path": "/leads",
                    "query": {"limit": 50},
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "workflowId": "wf-123",
        "url": "https://n8n.example.com/workflow/wf-123",
        "name": "CRM Workflow",
    }


def test_generate_workflow_requires_endpoints(client: TestClient) -> None:
    existing = _make_integration(name="crm")
    fake_session = FakeAsyncSession(execute_results=[existing])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post("/integrations/crm/generate-workflow", json={"endpoints": []})

    assert response.status_code == 400


def test_generate_workflow_returns_404_for_missing_integration(client: TestClient) -> None:
    fake_session = FakeAsyncSession(execute_results=[None])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/integrations/missing/generate-workflow",
        json={"endpoints": [{"name": "x", "method": "GET", "path": "/x"}]},
    )

    assert response.status_code == 404


def test_preview_workflow_e2e(client: TestClient) -> None:
    existing = _make_integration(name="crm")
    fake_session = FakeAsyncSession(execute_results=[existing])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/integrations/crm/preview-workflow",
        json={
            "endpoints": [
                {"name": "ListCustomers", "method": "GET", "path": "/customers"}
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"].startswith("crm")
    assert body["workflow"]["nodes"]


def test_generate_workflow_from_openapi_document(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = _make_integration(name="crm")
    fake_session = FakeAsyncSession(execute_results=[existing])

    async def override_session() -> Any:
        yield fake_session

    captured: dict = {}

    async def fake_create_workflow(workflow: dict) -> dict:
        captured["workflow"] = workflow
        return {"id": "wf-openapi-1", "name": "CRM OpenAPI Workflow"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(integrations_route, "create_workflow", fake_create_workflow)
    monkeypatch.setenv("N8N_WEB_URL", "https://n8n.example.com")

    response = client.post(
        "/integrations/crm/generate-workflow",
        json={
            "openapi": {
                "openapi": "3.0.0",
                "servers": [{"url": "https://api.crm.example.com"}],
                "paths": {
                    "/leads": {
                        "get": {
                            "operationId": "listLeads",
                            "parameters": [
                                {
                                    "name": "limit",
                                    "in": "query",
                                    "schema": {"type": "integer"},
                                    "default": 50,
                                }
                            ],
                        }
                    }
                },
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "workflowId": "wf-openapi-1",
        "url": "https://n8n.example.com/workflow/wf-openapi-1",
        "name": "CRM OpenAPI Workflow",
    }
    assert captured["workflow"]["nodes"][1]["name"] == "listLeads"


def test_generate_workflow_sharded_when_node_budget_exceeded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = _make_integration(name="crm")
    fake_session = FakeAsyncSession(execute_results=[existing])

    async def override_session() -> Any:
        yield fake_session

    calls: list[dict] = []

    async def fake_create_workflow(workflow: dict) -> dict:
        calls.append(workflow)
        return {"id": f"wf-shard-{len(calls)}", "name": workflow.get("name")}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(integrations_route, "create_workflow", fake_create_workflow)
    monkeypatch.setenv("N8N_WEB_URL", "https://n8n.example.com")

    endpoints = [{"name": f"E{i}", "method": "GET", "path": f"/e{i}"} for i in range(1, 21)]

    response = client.post(
        "/integrations/crm/generate-workflow",
        json={
            "endpoints": endpoints,
            "max_nodes_per_workflow": 10,
            "shard_enabled": True,
            "endpoint_selection_strategy": "as_is",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["shardCount"] == 3
    assert len(body["shards"]) == 3
    assert len(calls) == 3


def test_preview_workflow_sharded_bundle(
    client: TestClient,
) -> None:
    existing = _make_integration(name="crm")
    fake_session = FakeAsyncSession(execute_results=[existing])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    endpoints = [{"name": f"E{i}", "method": "GET", "path": f"/e{i}"} for i in range(1, 21)]

    response = client.post(
        "/integrations/crm/preview-workflow",
        json={
            "endpoints": endpoints,
            "max_nodes_per_workflow": 10,
            "shard_enabled": True,
            "endpoint_selection_strategy": "as_is",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["shardCount"] == 3
    assert len(body["workflows"]) == 3
    assert body["workflow"]["nodes"]


def test_auto_generate_workflows_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session = FakeAsyncSession(execute_results=[None])

    async def override_session() -> Any:
        yield fake_session

    async def fake_create_workflow(_workflow: dict) -> dict:
        return {"id": "wf-auto-1", "name": "Auto CRM Workflow"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(integrations_route, "create_workflow", fake_create_workflow)
    monkeypatch.setenv("N8N_WEB_URL", "https://n8n.example.com")

    response = client.post(
        "/integrations/auto-generate",
        json={
            "integrations": [
                {
                    "name": "crm",
                    "base_url": "https://api.example.com",
                    "auth_type": "none",
                    "endpoints": [{"name": "ListLeads", "method": "GET", "path": "/leads"}],
                }
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["status"] == "created"
    assert body["results"][0]["workflowId"] == "wf-auto-1"
    assert body["results"][0]["url"] == "https://n8n.example.com/workflow/wf-auto-1"


def test_integrations_autopilot_dry_run_detects_and_compares(client: TestClient) -> None:
    fake_session = FakeAsyncSession(execute_results=[[]])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/integrations/autopilot",
        json={
            "request_text": "Necesito CRM para ventas y tambien pagos con checkout.",
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "crm" in body["detected_functions"]
    assert "payments" in body["detected_functions"]
    assert all(item["status"] == "planned" for item in body["results"])
    assert body["plans"]
    assert body["plans"][0]["candidates"]


def test_integrations_autopilot_executes_full_pipeline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session = FakeAsyncSession(execute_results=[[], None, None])

    async def override_session() -> Any:
        yield fake_session

    async def fake_create_workflow(_workflow: dict) -> dict:
        return {"id": "wf-autopilot-1", "name": "Autopilot Payments"}

    async def fake_resolve_endpoints(**_kwargs: Any) -> Any:
        return (
            "https://api.stripe.com",
            [EndpointSpec(name="ListCustomers", method="GET", path="/v1/customers")],
            "openapi_url",
        )

    async def fake_endpoint_test(**_kwargs: Any) -> Any:
        return ("passed", "ok")

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(integrations_route, "create_workflow", fake_create_workflow)
    monkeypatch.setattr(integrations_route, "_resolve_endpoints_from_request", fake_resolve_endpoints)
    monkeypatch.setattr(integrations_route, "_test_provider_endpoint", fake_endpoint_test)
    monkeypatch.setenv("N8N_WEB_URL", "https://n8n.example.com")
    monkeypatch.setenv("MASTER_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

    response = client.post(
        "/integrations/autopilot",
        json={
            "request_text": "Quiero cobrar en linea",
            "desired_functions": ["payments"],
            "supplied_api_keys": {"stripe": "sk_test_123"},
            "auto_generate_workflow": True,
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["status"] == "integrated"
    assert body["results"][0]["provider_id"] == "stripe"
    assert body["results"][0]["key_status"] == "obtained"
    assert body["results"][0]["workflowId"] == "wf-autopilot-1"
    assert body["results"][0]["url"] == "https://n8n.example.com/workflow/wf-autopilot-1"
    assert body["results"][0]["saved_integration_id"]


def test_integrations_autopilot_auto_repair_and_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session = FakeAsyncSession(execute_results=[[], None, None])

    async def override_session() -> Any:
        yield fake_session

    calls = {"count": 0}

    async def fake_test_provider_endpoint(**kwargs: Any) -> Any:
        candidate = kwargs["candidate"]
        calls["count"] += 1
        if candidate.get("healthcheck_path") == "/v1/customers?limit=1":
            return ("failed", "Healthcheck fallo (404).", {"status_code": 404})
        return ("passed", "ok", {"status_code": 200, "duration_ms": 90})

    async def fake_resolve_endpoints(**_kwargs: Any) -> Any:
        return (
            "https://api.stripe.com",
            [EndpointSpec(name="ListCustomers", method="GET", path="/v1/customers")],
            "openapi_url",
        )

    async def fake_create_workflow(_workflow: dict) -> dict:
        return {"id": "wf-repair-1", "name": "Repair Flow"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(integrations_route, "_test_provider_endpoint", fake_test_provider_endpoint)
    monkeypatch.setattr(integrations_route, "_resolve_endpoints_from_request", fake_resolve_endpoints)
    monkeypatch.setattr(integrations_route, "create_workflow", fake_create_workflow)
    monkeypatch.setenv("MASTER_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
    monkeypatch.setenv("N8N_WEB_URL", "https://n8n.example.com")

    response = client.post(
        "/integrations/autopilot",
        json={
            "request_text": "Necesito pagos",
            "desired_functions": ["payments"],
            "supplied_api_keys": {"stripe": "sk_test_123"},
            "auto_repair_max_attempts": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["status"] == "integrated"
    assert body["results"][0]["auto_repair_log"]
    assert calls["count"] >= 2


def test_integrations_autopilot_provider_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session = FakeAsyncSession(execute_results=[[], None, None])

    async def override_session() -> Any:
        yield fake_session

    async def fake_test_provider_endpoint(**kwargs: Any) -> Any:
        candidate = kwargs["candidate"]
        provider = candidate.get("provider_id")
        if provider == "sendgrid":
            return ("failed", "Healthcheck fallo (500).", {"status_code": 500})
        return ("passed", "ok", {"status_code": 200, "duration_ms": 75})

    async def fake_resolve_endpoints(**_kwargs: Any) -> Any:
        return (
            "https://api.resend.com",
            [EndpointSpec(name="ListDomains", method="GET", path="/domains")],
            "manual_endpoints",
        )

    async def fake_create_workflow(_workflow: dict) -> dict:
        return {"id": "wf-fallback-1", "name": "Fallback Flow"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(integrations_route, "_test_provider_endpoint", fake_test_provider_endpoint)
    monkeypatch.setattr(integrations_route, "_resolve_endpoints_from_request", fake_resolve_endpoints)
    monkeypatch.setattr(integrations_route, "create_workflow", fake_create_workflow)
    monkeypatch.setenv("MASTER_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
    monkeypatch.setenv("N8N_WEB_URL", "https://n8n.example.com")

    response = client.post(
        "/integrations/autopilot",
        json={
            "request_text": "Necesito SMTP newsletter por email",
            "desired_functions": ["email"],
            "supplied_api_keys": {"sendgrid": "sg_x", "resend": "re_x"},
            "enable_provider_fallback": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["status"] == "integrated"
    assert body["results"][0]["provider_id"] == "resend"
    assert "sendgrid" in body["results"][0]["attempted_providers"]
    assert "resend" in body["results"][0]["attempted_providers"]


def test_rotate_secret_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _make_integration(name="payments")
    fake_session = FakeAsyncSession(execute_results=[existing])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("MASTER_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

    response = client.post(
        "/integrations/payments/rotate-secret",
        json={"secret": "new_secret_123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rotated"] is True
    assert body["backend"] == "database_encrypted"


def test_dashboard_endpoint(client: TestClient) -> None:
    existing = _make_integration(name="payments")
    run1 = _make_run_log(status="integrated")
    run2 = _make_run_log(status="failed")
    memory = _make_memory()
    fake_session = FakeAsyncSession(execute_results=[[existing], [run1, run2], [memory]])

    async def override_session() -> Any:
        yield fake_session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.get("/integrations/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["active_integrations"] == 1
    assert body["summary"]["total_runs"] == 2
    assert body["runs"]
    assert body["memory"]


def test_generate_complex_workflow_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    integ_a = _make_integration(name="crm")
    integ_b = _make_integration(name="email")
    fake_session = FakeAsyncSession(execute_results=[integ_a, integ_b])

    async def override_session() -> Any:
        yield fake_session

    async def fake_create_workflow(workflow: dict) -> dict:
        assert workflow["nodes"]
        return {"id": "wf-complex-1", "name": "Lead to Email"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(integrations_route, "create_workflow", fake_create_workflow)
    monkeypatch.setenv("N8N_WEB_URL", "https://n8n.example.com")

    response = client.post(
        "/integrations/generate-complex-workflow",
        json={
            "name": "Lead -> CRM -> Email",
            "steps": [
                {
                    "name": "CreateLead",
                    "integration_name": "crm",
                    "method": "POST",
                    "path": "/leads",
                    "body": {"email": "demo@example.com"},
                },
                {
                    "name": "SendEmail",
                    "integration_name": "email",
                    "method": "POST",
                    "path": "/messages",
                    "body": {"to": "demo@example.com"},
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workflowId"] == "wf-complex-1"
    assert body["url"] == "https://n8n.example.com/workflow/wf-complex-1"
    assert body["step_count"] == 2
