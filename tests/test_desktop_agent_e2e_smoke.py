from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import main
import routes.desktop_agent as desktop_route
from database import get_session
from models import DesktopAgent, DesktopCommand, DesktopCommandResult
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


class _StatefulDummySession:
    def __init__(self) -> None:
        self.commands: Dict[UUID, DesktopCommand] = {}
        self.results: Dict[UUID, DesktopCommandResult] = {}

    async def execute(self, _stmt: Any) -> _ExecuteResult:
        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, DesktopCommand):
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()
            self.commands[obj.id] = obj
            return
        if isinstance(obj, DesktopCommandResult):
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()
            self.results[obj.command_id] = obj

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        now = datetime.now(timezone.utc)
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        obj.updated_at = now
        if isinstance(obj, DesktopCommand):
            self.commands[obj.id] = obj
        if isinstance(obj, DesktopCommandResult):
            self.results[obj.command_id] = obj


class _DummySessionCtx:
    def __init__(self, session: _StatefulDummySession) -> None:
        self._session = session

    async def __aenter__(self) -> _StatefulDummySession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
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


def _fake_agent() -> DesktopAgent:
    now = datetime.now(timezone.utc)
    return DesktopAgent(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        agent_name="smoke-agent",
        status="online",
        capabilities={"os": "windows", "transport": "websocket"},
        last_heartbeat_at=now,
        connected_at=now,
        disconnected_at=None,
        created_at=now,
        updated_at=now,
    )


def test_browser_command_e2e_create_approve_dispatch_and_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _StatefulDummySession()
    agent = _fake_agent()

    async def override_session() -> Any:
        yield session

    async def fake_resolve_auth(_websocket: Any):
        return UUID(TEST_CLIENT_ID), TEST_CLIENT_ID, agent.agent_name, "production"

    async def fake_upsert(*_args: Any, **_kwargs: Any):
        return agent

    async def fake_get_agent(*_args: Any, **_kwargs: Any):
        return agent

    async def fake_mark_hb(*_args: Any, **_kwargs: Any):
        return None

    async def fake_mark_offline(*_args: Any, **_kwargs: Any):
        return None

    async def fake_dispatch_backlog(*_args: Any, **_kwargs: Any):
        return None

    async def fake_get_command(*_args: Any, **_kwargs: Any):
        command_id = _kwargs.get("command_id")
        return session.commands.get(command_id)

    async def fake_get_result(*_args: Any, **_kwargs: Any):
        command_id = _kwargs.get("command_id")
        return session.results.get(command_id)

    async def fake_audit(*_args: Any, **_kwargs: Any):
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", '["example.com"]')
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(desktop_route, "_resolve_ws_auth", fake_resolve_auth)
    monkeypatch.setattr(desktop_route, "_upsert_agent", fake_upsert)
    monkeypatch.setattr(desktop_route, "_dispatch_approved_backlog", fake_dispatch_backlog)
    monkeypatch.setattr(desktop_route, "_get_agent", fake_get_agent)
    monkeypatch.setattr(desktop_route, "_mark_agent_heartbeat", fake_mark_hb)
    monkeypatch.setattr(desktop_route, "_mark_agent_offline", fake_mark_offline)
    monkeypatch.setattr(desktop_route, "_get_command", fake_get_command)
    monkeypatch.setattr(desktop_route, "_get_command_result", fake_get_result)
    monkeypatch.setattr(desktop_route, "write_audit_log", fake_audit)
    monkeypatch.setattr(desktop_route, "AsyncSessionLocal", lambda: _DummySessionCtx(session))

    with client.websocket_connect("/desktop-agent/ws") as websocket:
        websocket.send_json({"type": "agent_hello", "agent_id": TEST_CLIENT_ID})
        hello_ack = websocket.receive_json()
        assert hello_ack["type"] == "ack"
        assert hello_ack["status"] == "received"

        create_resp = client.post(
            "/desktop-agent/commands",
            json={
                "agent_id": str(agent.id),
                "action": "browser_goto",
                "args": {"url": "https://example.com"},
            },
        )
        assert create_resp.status_code == 200
        create_body = create_resp.json()
        command_id = create_body["id"]
        assert create_body["status"] == "pending_approval"

        approve_resp = client.post(
            f"/desktop-agent/commands/{command_id}/approve",
            json={},
        )
        assert approve_resp.status_code == 200
        approve_body = approve_resp.json()
        assert approve_body["status"] in {"approved", "sent"}

        ws_command = websocket.receive_json()
        assert ws_command["type"] == "command"
        assert ws_command["action"] == "browser_goto"
        assert ws_command["approved"] is True
        assert ws_command["command_id"] == command_id

        websocket.send_json(
            {
                "type": "result",
                "command_id": command_id,
                "status": "success",
                "duration_ms": 120,
                "error": None,
                "data": {"navigated": True},
            }
        )
        result_ack = websocket.receive_json()
        assert result_ack["type"] == "ack"
        assert result_ack["status"] == "persisted"
        assert result_ack["command_id"] == command_id

        get_resp = client.get(f"/desktop-agent/commands/{command_id}")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["status"] == "completed"
        assert body["result"]["status"] == "success"
        assert body["result"]["data"]["navigated"] is True
