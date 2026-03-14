from datetime import datetime, timezone
from typing import Any, List
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import main
import routes.desktop_agent as desktop_route
from database import get_session
from models import AuditLog, DesktopAgent, DesktopCommand, DesktopCommandResult, WorkflowRecording, WorkflowRecordingStep
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


class DummySession:
    def __init__(self, execute_results: List[Any] | None = None) -> None:
        self._execute_results = list(execute_results or [])
        self.added: List[Any] = []

    async def execute(self, _stmt: Any) -> _ExecuteResult:
        if self._execute_results:
            return _ExecuteResult(self._execute_results.pop(0))
        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        now = datetime.now(timezone.utc)
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        obj.updated_at = now


class DummySessionCtx:
    def __init__(self, session: DummySession) -> None:
        self._session = session

    async def __aenter__(self) -> DummySession:
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
        agent_name="agent-1",
        status="online",
        capabilities={"os": "windows"},
        last_heartbeat_at=now,
        connected_at=now,
        disconnected_at=None,
        created_at=now,
        updated_at=now,
    )



def _fake_command(status: str = "pending_approval") -> DesktopCommand:
    now = datetime.now(timezone.utc)
    return DesktopCommand(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        agent_id=uuid4(),
        action="click",
        args={"button": "left", "clicks": 1},
        status=status,
        timeout_ms=10000,
        requested_by_user_id=None,
        approved_by_user_id=None,
        approved_at=None,
        issued_at=None,
        error=None,
        command_metadata={},
        created_at=now,
        updated_at=now,
    )



def _fake_result(command_id: UUID) -> DesktopCommandResult:
    now = datetime.now(timezone.utc)
    return DesktopCommandResult(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        agent_id=uuid4(),
        command_id=command_id,
        status="success",
        duration_ms=120,
        error=None,
        data={"ok": True},
        received_at=now,
        created_at=now,
        updated_at=now,
    )



def test_build_ws_payload_uses_execute_in_remote_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_MODE", "remote")
    command = _fake_command(status="approved")
    payload = desktop_route._build_ws_command_payload(command)
    assert payload["type"] == "execute"
    assert payload["step_id"] == str(command.id)
    assert payload["action"] == command.action


def test_create_command_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummySession()

    async def override_session() -> Any:
        yield dummy

    async def fake_get_agent(*_args: Any, **_kwargs: Any) -> DesktopAgent:
        return _fake_agent()

    async def fake_audit(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(desktop_route, "_get_agent", fake_get_agent)
    monkeypatch.setattr(desktop_route, "write_audit_log", fake_audit)

    response = client.post(
        "/desktop-agent/commands",
        json={
            "agent_id": str(uuid4()),
            "action": "click",
            "args": {"button": "left", "clicks": 1},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["action"] == "click"


def test_create_browser_command_sets_human_approval_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dummy = DummySession()

    async def override_session() -> Any:
        yield dummy

    async def fake_get_agent(*_args: Any, **_kwargs: Any) -> DesktopAgent:
        return _fake_agent()

    async def fake_audit(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", '["example.com"]')
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(desktop_route, "_get_agent", fake_get_agent)
    monkeypatch.setattr(desktop_route, "write_audit_log", fake_audit)

    response = client.post(
        "/desktop-agent/commands",
        json={
            "agent_id": str(uuid4()),
            "action": "browser_goto",
            "args": {"url": "https://app.example.com"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["requires_human_approval"] is True
    assert body["metadata"]["approval"]["required"] is True



def test_approve_command_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummySession()
    command = _fake_command(status="pending_approval")

    async def override_session() -> Any:
        yield dummy

    async def fake_get_command(*_args: Any, **_kwargs: Any) -> DesktopCommand:
        return command

    async def fake_dispatch(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def fake_get_result(*_args: Any, **_kwargs: Any) -> Any:
        return None

    async def fake_audit(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(desktop_route, "_get_command", fake_get_command)
    monkeypatch.setattr(desktop_route, "_dispatch_command_if_online", fake_dispatch)
    monkeypatch.setattr(desktop_route, "_get_command_result", fake_get_result)
    monkeypatch.setattr(desktop_route, "write_audit_log", fake_audit)

    response = client.post(
        f"/desktop-agent/commands/{command.id}/approve",
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"approved", "sent"}



def test_get_command_endpoint_returns_result(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummySession()
    command = _fake_command(status="completed")
    result = _fake_result(command.id)

    async def override_session() -> Any:
        yield dummy

    async def fake_get_command(*_args: Any, **_kwargs: Any) -> DesktopCommand:
        return command

    async def fake_get_result(*_args: Any, **_kwargs: Any) -> DesktopCommandResult:
        return result

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(desktop_route, "_get_command", fake_get_command)
    monkeypatch.setattr(desktop_route, "_get_command_result", fake_get_result)

    response = client.get(f"/desktop-agent/commands/{command.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["status"] == "success"



def test_list_agents_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    agent = DesktopAgent(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        agent_name="agent-list",
        status="offline",
        capabilities={},
        last_heartbeat_at=now,
        connected_at=None,
        disconnected_at=now,
        created_at=now,
        updated_at=now,
    )
    dummy = DummySession(execute_results=[[agent]])

    async def override_session() -> Any:
        yield dummy

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.get("/desktop-agent/agents")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["agent_name"] == "agent-list"



def test_ops_health_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummySession()

    async def override_session() -> Any:
        yield dummy

    async def fake_snapshot(*_args: Any, **_kwargs: Any) -> dict:
        return {
            "window_minutes": 60,
            "ws": {"reconnect_rate": 0.1, "total_connects": 10, "reconnect_count": 1, "connected_agents": 1},
            "command_success_rate_by_action": {"click": {"total": 10, "success": 9, "failed": 1, "success_rate": 0.9}},
            "selector_timeouts": {"total_timeout_events": 2, "by_selector": [{"selector": "#login", "timeouts": 2}]},
            "mttr_seconds": 12.0,
            "mttr_sample_count": 1,
            "sampled_results": 10,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(desktop_route, "_build_ops_health_snapshot", fake_snapshot)

    response = client.get("/desktop-agent/ops/health?window_minutes=60")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ws"]["reconnect_rate"] == 0.1
    assert payload["command_success_rate_by_action"]["click"]["success_rate"] == 0.9


def test_ops_health_history_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    log = AuditLog(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        actor_user_id=None,
        action="desktop_agent.ops.health_snapshot",
        resource="desktop_ops",
        resource_id=None,
        status="ok",
        detail=None,
        audit_metadata={"ws": {"reconnect_rate": 0.2}},
        created_at=now,
    )
    dummy = DummySession(execute_results=[[log]])

    async def override_session() -> Any:
        yield dummy

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.get("/desktop-agent/ops/health/history?limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["metrics"]["ws"]["reconnect_rate"] == 0.2


def test_ops_scorecard_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    snapshot_log = AuditLog(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        actor_user_id=None,
        action="desktop_agent.ops.health_snapshot",
        resource="desktop_ops",
        resource_id=None,
        status="ok",
        detail=None,
        audit_metadata={
            "ws": {"reconnect_rate": 0.15},
            "mttr_seconds": 20.0,
            "selector_timeouts": {"total_timeout_events": 3},
            "sampled_results": 15,
            "command_success_rate_by_action": {
                "click": {"total": 10, "success": 9},
                "browser_click": {"total": 5, "success": 4},
            },
        },
        created_at=now,
    )
    alert_log = AuditLog(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        actor_user_id=None,
        action="desktop_agent.ops.alert",
        resource="desktop_ops",
        resource_id=None,
        status="warning",
        detail="click success degraded",
        audit_metadata={"key": "action_success_rate.click"},
        created_at=now,
    )
    dummy = DummySession(execute_results=[[snapshot_log], [alert_log]])

    async def override_session() -> Any:
        yield dummy

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.get("/desktop-agent/ops/scorecard?window_minutes=60&history_limit=100")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["alerts_in_window"] == 1
    assert payload["summary"]["avg_ws_reconnect_rate"] == 0.15
    assert payload["action_success_rate_aggregate"]["click"] == 0.9


def test_websocket_heartbeat_and_result_ack(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _fake_agent()
    command = _fake_command(status="sent")
    result = _fake_result(command.id)

    async def fake_resolve_auth(_websocket: Any):
        return UUID(TEST_CLIENT_ID), TEST_CLIENT_ID, "agent-ws", "production"

    async def fake_upsert(*_args: Any, **_kwargs: Any):
        return agent

    async def fake_dispatch_backlog(*_args: Any, **_kwargs: Any):
        return None

    async def fake_get_agent(*_args: Any, **_kwargs: Any):
        return agent

    async def fake_mark_hb(*_args: Any, **_kwargs: Any):
        return None

    async def fake_mark_offline(*_args: Any, **_kwargs: Any):
        return None

    async def fake_persist(*_args: Any, **_kwargs: Any):
        return command, result

    async def fake_audit(*_args: Any, **_kwargs: Any):
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setattr(desktop_route, "_resolve_ws_auth", fake_resolve_auth)
    monkeypatch.setattr(desktop_route, "_upsert_agent", fake_upsert)
    monkeypatch.setattr(desktop_route, "_dispatch_approved_backlog", fake_dispatch_backlog)
    monkeypatch.setattr(desktop_route, "_get_agent", fake_get_agent)
    monkeypatch.setattr(desktop_route, "_mark_agent_heartbeat", fake_mark_hb)
    monkeypatch.setattr(desktop_route, "_mark_agent_offline", fake_mark_offline)
    monkeypatch.setattr(desktop_route, "_persist_result", fake_persist)
    monkeypatch.setattr(desktop_route, "write_audit_log", fake_audit)
    monkeypatch.setattr(desktop_route, "AsyncSessionLocal", lambda: DummySessionCtx(DummySession()))

    with client.websocket_connect("/desktop-agent/ws") as websocket:
        websocket.send_json({"type": "heartbeat", "agent_id": TEST_CLIENT_ID, "ts": datetime.now(timezone.utc).isoformat()})
        hb_ack = websocket.receive_json()
        assert hb_ack["type"] == "ack"
        assert hb_ack["status"] == "received"

        websocket.send_json(
            {
                "type": "result",
                "command_id": str(command.id),
                "status": "success",
                "duration_ms": 90,
                "error": None,
                "data": {"ok": True},
            }
        )
        result_ack = websocket.receive_json()
        assert result_ack["type"] == "ack"
        assert result_ack["status"] == "persisted"


def test_websocket_human_event_persisted_ack(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _fake_agent()
    now = datetime.now(timezone.utc)
    recording = WorkflowRecording(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        agent_id=agent.id,
        title="human rec",
        status="recording",
        created_by_user_id=None,
        started_at=now,
        stopped_at=None,
        recording_metadata={"capture_policy": {"mode": "human"}},
        n8n_workflow_id=None,
        n8n_workflow_url=None,
        created_at=now,
        updated_at=now,
    )

    async def fake_resolve_auth(_websocket: Any):
        return UUID(TEST_CLIENT_ID), TEST_CLIENT_ID, "agent-ws", "production"

    async def fake_upsert(*_args: Any, **_kwargs: Any):
        return agent

    async def fake_dispatch_backlog(*_args: Any, **_kwargs: Any):
        return None

    async def fake_mark_offline(*_args: Any, **_kwargs: Any):
        return None

    dummy = DummySession(execute_results=[recording, 0])

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setenv("ENABLE_WORKFLOW_RECORDER", "true")
    monkeypatch.setenv("ENABLE_HUMAN_RECORDER", "true")
    monkeypatch.setattr(desktop_route, "_resolve_ws_auth", fake_resolve_auth)
    monkeypatch.setattr(desktop_route, "_upsert_agent", fake_upsert)
    monkeypatch.setattr(desktop_route, "_dispatch_approved_backlog", fake_dispatch_backlog)
    monkeypatch.setattr(desktop_route, "_mark_agent_offline", fake_mark_offline)
    monkeypatch.setattr(desktop_route, "AsyncSessionLocal", lambda: DummySessionCtx(dummy))

    with client.websocket_connect("/desktop-agent/ws") as websocket:
        websocket.send_json(
            {
                "type": "human_event",
                "recording_id": str(recording.id),
                "ts": now.isoformat(),
                "event_type": "click",
                "payload": {"x": 10, "y": 20, "button": "left", "clicks": 1},
                "context": {"active_app": "test"},
            }
        )
        ack = websocket.receive_json()
        assert ack["type"] == "ack"
        assert ack["status"] == "persisted"

    steps = [x for x in dummy.added if isinstance(x, WorkflowRecordingStep)]
    assert len(steps) == 1
    assert steps[0].recording_id == recording.id
    assert steps[0].action == "click"


def test_websocket_result_accepts_step_id_for_runner_protocol(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _fake_agent()
    command = _fake_command(agent.id)
    result = _fake_result(command.id)

    async def fake_resolve_auth(_websocket: Any):
        return UUID(TEST_CLIENT_ID), TEST_CLIENT_ID, "runner-ws", "production"

    async def fake_upsert(*_args: Any, **_kwargs: Any):
        return agent

    async def fake_dispatch_backlog(*_args: Any, **_kwargs: Any):
        return None

    async def fake_get_agent(*_args: Any, **_kwargs: Any):
        return agent

    async def fake_mark_hb(*_args: Any, **_kwargs: Any):
        return None

    async def fake_mark_offline(*_args: Any, **_kwargs: Any):
        return None

    async def fake_persist(*_args: Any, **kwargs: Any):
        payload = kwargs.get("payload")
        assert payload is not None
        assert str(payload.command_id) == str(command.id)
        return command, result

    async def fake_audit(*_args: Any, **_kwargs: Any):
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setattr(desktop_route, "_resolve_ws_auth", fake_resolve_auth)
    monkeypatch.setattr(desktop_route, "_upsert_agent", fake_upsert)
    monkeypatch.setattr(desktop_route, "_dispatch_approved_backlog", fake_dispatch_backlog)
    monkeypatch.setattr(desktop_route, "_get_agent", fake_get_agent)
    monkeypatch.setattr(desktop_route, "_mark_agent_heartbeat", fake_mark_hb)
    monkeypatch.setattr(desktop_route, "_mark_agent_offline", fake_mark_offline)
    monkeypatch.setattr(desktop_route, "_persist_result", fake_persist)
    monkeypatch.setattr(desktop_route, "write_audit_log", fake_audit)
    monkeypatch.setattr(desktop_route, "AsyncSessionLocal", lambda: DummySessionCtx(DummySession()))

    with client.websocket_connect("/desktop-agent/ws") as websocket:
        websocket.send_json(
            {
                "type": "result",
                "step_id": str(command.id),
                "status": "completed",
                "duration_ms": 90,
                "error": None,
                "data": {"ok": True},
            }
        )
        result_ack = websocket.receive_json()
        assert result_ack["type"] == "ack"
        assert result_ack["status"] == "persisted"
