from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import main
import routes.chat as chat_route
from security import require_client_token

TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


class FakeEngine:
    def __init__(self, result: Dict[str, Any]) -> None:
        self._result = result
        self.calls: List[Dict[str, str]] = []

    async def generate_response(self, client_id: str, session_id: str, message: str) -> Dict[str, Any]:
        self.calls.append(
            {"client_id": client_id, "session_id": session_id, "message": message}
        )
        return self._result


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_chat_message_response(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_engine = FakeEngine({"type": "message", "content": "hola"})

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setattr(chat_route.CognitiveEngine, "get_instance", lambda: fake_engine)

    response = client.post(
        "/chat/message",
        json={"session_id": "s-1", "message": "hello"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "message",
        "content": "hola",
        "action": None,
        "action_dispatched": False,
        "action_id": None,
        "approval_required": False,
        "approval_status": "not_required",
    }
    assert fake_engine.calls == [
        {"client_id": TEST_CLIENT_ID, "session_id": "s-1", "message": "hello"}
    ]


def test_chat_action_requires_approval_when_sensitive(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_engine = FakeEngine(
        {
            "type": "action",
            "content": "ACTION: ...",
            "payload": {
                "contract_version": "1.0",
                "action": "create_order",
                "integration": "shopify",
                "arguments": {"sku": "A1"},
                "rationale": "User asked to create an order",
            },
        }
    )
    sent_payloads: List[Dict[str, Any]] = []

    async def fake_send_to_n8n(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent_payloads.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL_FOR_SENSITIVE_ACTIONS", "true")
    monkeypatch.setattr(chat_route.CognitiveEngine, "get_instance", lambda: fake_engine)
    monkeypatch.setattr(chat_route, "send_to_n8n_with_response", fake_send_to_n8n)

    response = client.post(
        "/chat/message",
        json={
            "session_id": "s-2",
            "message": "crea pedido",
            "channel": "web",
            "metadata": {"tenant": "acme"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "action"
    assert body["action_dispatched"] is False
    assert body["approval_required"] is True
    assert body["approval_status"] == "pending"
    assert body["action"]["integration"] == "shopify"
    assert body["action_id"]
    assert sent_payloads and sent_payloads[0]["source"] == "chat_action_approval_required"


def test_chat_action_dispatches_when_approved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_engine = FakeEngine(
        {
            "type": "action",
            "content": "ACTION: ...",
            "payload": {
                "contract_version": "1.0",
                "action": "create_order",
                "integration": "shopify",
                "arguments": {"sku": "A1"},
                "rationale": "User asked to create an order",
            },
        }
    )
    sent_payloads: List[Dict[str, Any]] = []

    async def fake_send_to_n8n(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent_payloads.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL_FOR_SENSITIVE_ACTIONS", "true")
    monkeypatch.setattr(chat_route.CognitiveEngine, "get_instance", lambda: fake_engine)
    monkeypatch.setattr(chat_route, "send_to_n8n_with_response", fake_send_to_n8n)

    response = client.post(
        "/chat/message",
        json={
            "session_id": "s-2",
            "message": "crea pedido",
            "human_approval_granted": True,
            "approver_id": "agent-7",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "action"
    assert body["action_dispatched"] is True
    assert body["approval_required"] is True
    assert body["approval_status"] == "approved"
    assert sent_payloads and sent_payloads[0]["source"] == "chat_action"
    assert sent_payloads[0]["approval"]["approver_id"] == "agent-7"


def test_chat_action_non_sensitive_dispatches_without_approval(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_engine = FakeEngine(
        {
            "type": "action",
            "content": "ACTION: ...",
            "payload": {
                "contract_version": "1.0",
                "action": "lookup_catalog",
                "integration": "shopify",
                "arguments": {"sku": "A1"},
                "rationale": "Need product details",
            },
        }
    )
    sent_payloads: List[Dict[str, Any]] = []

    async def fake_send_to_n8n(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent_payloads.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL_FOR_SENSITIVE_ACTIONS", "true")
    monkeypatch.setattr(chat_route.CognitiveEngine, "get_instance", lambda: fake_engine)
    monkeypatch.setattr(chat_route, "send_to_n8n_with_response", fake_send_to_n8n)

    response = client.post("/chat/message", json={"session_id": "s-3", "message": "busca"})

    assert response.status_code == 200
    body = response.json()
    assert body["action_dispatched"] is True
    assert body["approval_required"] is False
    assert body["approval_status"] == "not_required"
    assert sent_payloads and sent_payloads[0]["source"] == "chat_action"


def test_chat_desktop_action_dispatches_to_desktop_executor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_engine = FakeEngine(
        {
            "type": "action",
            "content": "ACTION: desktop",
            "payload": {
                "contract_version": "1.0",
                "action_type": "desktop",
                "action": "open_program",
                "integration": "desktop",
                "arguments": {"name": "notepad"},
                "rationale": "Abrir aplicacion solicitada",
            },
        }
    )
    desktop_calls: List[Dict[str, Any]] = []

    async def fake_send_to_desktop_agent(**kwargs: Any) -> Dict[str, Any]:
        desktop_calls.append(kwargs)
        return {"accepted": True, "command_id": "cmd-123", "status": "pending_approval"}

    async def fake_send_to_n8n(_payload: Dict[str, Any]) -> Dict[str, Any]:
        raise AssertionError("n8n should not be called for desktop actions")

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setattr(chat_route.CognitiveEngine, "get_instance", lambda: fake_engine)
    monkeypatch.setattr(chat_route, "send_to_desktop_agent", fake_send_to_desktop_agent)
    monkeypatch.setattr(chat_route, "send_to_n8n_with_response", fake_send_to_n8n)

    response = client.post(
        "/chat/message",
        json={
            "session_id": "s-desktop",
            "message": "abre notepad",
            "user_id": "agent-1",
            "metadata": {"foo": "bar"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "action"
    assert body["action_dispatched"] is True
    assert body["action_id"] == "cmd-123"
    assert desktop_calls
    assert desktop_calls[0]["action"] == "open_program"
    assert desktop_calls[0]["parameters"] == {"name": "notepad"}


def test_chat_browser_integration_routes_to_desktop_executor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_engine = FakeEngine(
        {
            "type": "action",
            "content": "ACTION: browser",
            "payload": {
                "contract_version": "1.0",
                "action_type": "integration",
                "action": "browser_goto",
                "integration": "browser",
                "arguments": {"url": "https://app.example.com"},
                "rationale": "Navegar a CRM",
            },
        }
    )

    desktop_calls: List[Dict[str, Any]] = []

    async def fake_send_to_desktop_agent(**kwargs: Any) -> Dict[str, Any]:
        desktop_calls.append(kwargs)
        return {"accepted": True, "command_id": "cmd-browser-1", "status": "pending_approval"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setattr(chat_route.CognitiveEngine, "get_instance", lambda: fake_engine)
    monkeypatch.setattr(chat_route, "send_to_desktop_agent", fake_send_to_desktop_agent)

    response = client.post("/chat/message", json={"session_id": "s-browser", "message": "abre crm"})
    assert response.status_code == 200
    body = response.json()
    assert body["action_dispatched"] is True
    assert body["action_id"] == "cmd-browser-1"
    assert desktop_calls and desktop_calls[0]["action"] == "browser_goto"


def test_chat_returns_502_when_engine_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_engine() -> Any:
        raise RuntimeError("boom")

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    monkeypatch.setattr(chat_route.CognitiveEngine, "get_instance", _raise_engine)

    response = client.post(
        "/chat/message",
        json={"session_id": "s-1", "message": "hello"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "AI engine failed"
