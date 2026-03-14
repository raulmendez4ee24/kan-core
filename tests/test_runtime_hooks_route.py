from datetime import datetime, timezone
from typing import Any, List
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

import main
import routes.runtime_hooks as hooks_route
from database import get_session
from models import RuntimeHookRegistration
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

    def scalar_one_or_none(self) -> Any:
        return self._value


class DummySession:
    def __init__(self, execute_results: List[Any]) -> None:
        self._execute_results = list(execute_results)
        self.added: List[Any] = []

    async def execute(self, _stmt: Any) -> _ExecuteResult:
        if self._execute_results:
            return _ExecuteResult(self._execute_results.pop(0))
        return _ExecuteResult(None)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None


def _row(*, event: str, mode: str, version: int, metadata: dict) -> RuntimeHookRegistration:
    now = datetime.now(timezone.utc)
    return RuntimeHookRegistration(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        event=event,
        mode=mode,
        version=version,
        metadata_json=metadata,
        created_at=now,
    )


def test_runtime_hooks_versions_endpoint(monkeypatch) -> None:
    rows = [
        _row(event="before_tool", mode="annotate", version=1, metadata={"k": 1}),
        _row(event="before_tool", mode="noop", version=2, metadata={"k": 2}),
    ]
    dummy = DummySession(execute_results=[rows])

    async def override_session():
        yield dummy

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    with TestClient(main.app) as client:
        response = client.get("/runtime/hooks/before_tool/versions")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["version"] == 1
    assert body[1]["version"] == 2
    main.app.dependency_overrides = {}


def test_runtime_hooks_rollback_endpoint(monkeypatch) -> None:
    target = _row(event="before_tool", mode="annotate", version=1, metadata={"tag": "stable"})
    dummy = DummySession(execute_results=[target, 2])

    async def override_session():
        yield dummy

    async def fake_reload_org(_org: str) -> None:
        return None

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(hooks_route.runtime_hooks, "reload_org", fake_reload_org)

    with TestClient(main.app) as client:
        response = client.post("/runtime/hooks/before_tool/rollback", json={"target_version": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["rolled_back_to_version"] == 1
    assert body["new_active_version"] == 3
    assert dummy.added
    inserted = dummy.added[0]
    assert inserted.version == 3
    assert inserted.metadata_json["_rollback"]["target_version"] == 1
    main.app.dependency_overrides = {}
