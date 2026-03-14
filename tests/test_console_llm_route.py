from types import SimpleNamespace
from typing import Any, AsyncGenerator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main
import routes.console_llm as console_llm_route
from brain.security_tokens import issue_token
from database import get_session
from models import Client


TEST_ORG_ID = "11111111-1111-1111-1111-111111111111"


class DummySession:
    def __init__(self, client_obj: Any = None) -> None:
        self.client_obj = client_obj
        self.added: list[Any] = []
        self.flushed = 0
        self.commits = 0
        self.refreshed = 0

    async def get(self, model: Any, _id: Any) -> Any:
        if model is Client:
            return self.client_obj
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _obj: Any) -> None:
        self.refreshed += 1


def _auth_headers(*, permissions: list[str]) -> dict[str, str]:
    token, _ = issue_token(
        user_id=str(uuid4()),
        organization_id=TEST_ORG_ID,
        role="ORG_ADMIN",
        permissions=permissions,
        expires_in_minutes=60,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_console_llm_status_returns_404_when_disabled(client: TestClient) -> None:
    response = client.get("/console/llm/status")
    assert response.status_code == 404


def test_console_llm_status_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_ENTERPRISE_CONSOLE", "true")
    fake_session = DummySession(
        client_obj=SimpleNamespace(model_name="gpt-5.2-chat-latest")
    )

    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield fake_session

    async def fake_find(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            name="openai",
            integration_metadata={
                "provider": "openai",
                "model_name": "gpt-5.2-chat-latest",
                "secret_backend": "database_encrypted",
            },
        )

    async def fake_load(*_args: Any, **_kwargs: Any) -> str:
        return "sk-test-key"

    monkeypatch.setattr(console_llm_route, "_find_llm_integration", fake_find)
    monkeypatch.setattr(console_llm_route, "load_integration_secret", fake_load)
    main.app.dependency_overrides[get_session] = override_session

    response = client.get(
        "/console/llm/status",
        headers=_auth_headers(permissions=["org.policies.read"]),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["configured"] is True
    assert body["integration_name"] == "openai"
    assert body["model_name"] == "gpt-5.2-chat-latest"
    assert body["secret_backend"] == "database_encrypted"


def test_console_llm_connect_success(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_ENTERPRISE_CONSOLE", "true")
    fake_session = DummySession(
        client_obj=SimpleNamespace(
            model_name="gpt-5.2-chat-latest",
            llm_preferences={},
        )
    )

    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield fake_session

    async def fake_find(*_args: Any, **_kwargs: Any) -> Any:
        return None

    async def fake_store(*_args: Any, **_kwargs: Any) -> str:
        return "database_encrypted"

    async def fake_audit(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(console_llm_route, "_find_llm_integration", fake_find)
    monkeypatch.setattr(console_llm_route, "store_integration_secret", fake_store)
    monkeypatch.setattr(console_llm_route, "write_audit_log", fake_audit)
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/console/llm/connect",
        headers=_auth_headers(permissions=["org.policies.write"]),
        json={
            "provider": "openai",
            "api_key": "sk-test-key-12345678",
            "model_name": "gpt-5.2-chat-latest",
            "metadata": {"source": "test"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["configured"] is True
    assert body["integration_name"] == "openai"
    assert body["model_name"] == "gpt-5.2-chat-latest"
    assert body["secret_backend"] == "database_encrypted"
    assert fake_session.commits == 1
    assert fake_session.flushed == 1
    assert fake_session.refreshed == 1
    assert fake_session.client_obj.llm_preferences["provider"] == "openai"
    assert fake_session.client_obj.llm_preferences["provider_integration"] == "openai"
