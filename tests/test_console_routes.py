from datetime import datetime, timezone
from typing import Any, AsyncGenerator, List, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import main
import routes.console_auth as console_auth_route
import routes.console_policies as console_policies_route
from database import get_session


TEST_ORG_ID = "11111111-1111-1111-1111-111111111111"


class _Scalar:
    def __init__(self, value: Any) -> None:
        self._value = value

    def first(self) -> Any:
        return self._value


class _Execute:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalars(self) -> _Scalar:
        return _Scalar(self._value)


class DummySession:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def execute(self, _stmt: Any) -> _Execute:
        return _Execute(self._value)


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_console_login_returns_404_when_disabled(client: TestClient) -> None:
    response = client.post(
        "/console/auth/login",
        json={"email": "a@b.com", "password": "secret123"},
    )
    assert response.status_code == 404


def test_console_me_returns_404_when_disabled(client: TestClient) -> None:
    response = client.get("/console/auth/me")
    assert response.status_code == 404


def test_console_policies_returns_404_when_disabled(client: TestClient) -> None:
    response = client.get(f"/console/orgs/{TEST_ORG_ID}/policies")
    assert response.status_code == 404


def test_console_login_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class _User:
        def __init__(self) -> None:
            self.id = uuid4()
            self.email = "test@example.com"
            self.password_hash = "hash"
            self.is_active = True
            self.organization_id = UUID(TEST_ORG_ID)

    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession(_User())

    async def fake_resolve(*_args: Any, **_kwargs: Any) -> tuple[str, List[str]]:
        return "ORG_ADMIN", ["org.policies.read"]

    async def fake_audit(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setenv("ENABLE_ENTERPRISE_CONSOLE", "true")
    monkeypatch.setattr(console_auth_route, "verify_password", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(console_auth_route, "resolve_role_and_permissions", fake_resolve)
    monkeypatch.setattr(console_auth_route, "issue_token", lambda **_kwargs: ("token-123", int(datetime.now(timezone.utc).timestamp()) + 3600))
    monkeypatch.setattr(console_auth_route, "write_audit_log", fake_audit)

    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/console/auth/login",
        json={"email": "test@example.com", "password": "secret123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token"] == "token-123"
    assert body["token_type"] == "bearer"

