from typing import Any, AsyncGenerator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import main
from database import get_session
from security import require_client_token


TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


class _OrgPolicy:
    def __init__(self, organization_id: UUID) -> None:
        self.id = uuid4()
        self.organization_id = organization_id
        self.policy = {}
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        self.created_at = now
        self.updated_at = now


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
    def __init__(self) -> None:
        self._row = None

    async def execute(self, _stmt: Any) -> _Execute:
        return _Execute(self._row)

    def add(self, obj: Any) -> None:
        self._row = obj

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        from datetime import datetime, timezone

        obj.updated_at = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_policy_preset_route_returns_404_when_policies_disabled(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ENTERPRISE_POLICIES", "false")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    response = client.post("/policies/presets/openclaw-allow-all")
    assert response.status_code == 404


def test_policy_preset_route_applies_when_enabled(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ENTERPRISE_POLICIES", "true")

    async def override_session() -> AsyncGenerator[DummySession, None]:
        session = DummySession()
        # Pretend we already had a row.
        session._row = _OrgPolicy(UUID(TEST_CLIENT_ID))
        yield session

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post("/policies/presets/openclaw-allow-all")
    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == TEST_CLIENT_ID
    assert body["policy"]["autonomy"]["execution_mode"] == "full_auto"
    assert body["policy"]["guardrails"]["require_domain_allowlist_for_browser"] is False

