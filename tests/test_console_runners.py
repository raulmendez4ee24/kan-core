from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

import main
from database import get_session
from security import require_client_token


class _DummyExecResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _DummySession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _DummyExecResult(self._rows)


def test_console_runners_returns_agent_status(monkeypatch) -> None:
    agent_id = uuid4()
    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(
            id=agent_id,
            agent_name="local-runner",
            environment="production",
            status="online",
            last_heartbeat_at=now,
            connected_at=now,
            disconnected_at=None,
        )
    ]
    session = _DummySession(rows)

    async def override_session():
        yield session

    main.app.dependency_overrides[require_client_token] = lambda: "11111111-1111-1111-1111-111111111111"
    main.app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(main.app)
        response = client.get("/console/runners")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["agent_name"] == "local-runner"
    finally:
        main.app.dependency_overrides.clear()

