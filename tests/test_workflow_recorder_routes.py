from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import main
import routes.workflow_recorder as workflows_route
from database import get_session
from security import require_client_token


TEST_ORG_ID = "11111111-1111-1111-1111-111111111111"
TEST_AGENT_ID = "22222222-2222-2222-2222-222222222222"


class DummySession:
    async def execute(self, _stmt: Any) -> Any:
        return None

    def add(self, _obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
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


def test_workflows_start_recording_404_when_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    main.app.dependency_overrides[require_client_token] = lambda: TEST_ORG_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("ENABLE_WORKFLOW_RECORDER", "false")

    response = client.post(
        "/workflows/recordings",
        json={"agent_id": TEST_AGENT_ID, "title": "rec", "notes": "x", "capture_policy": {}},
    )
    assert response.status_code == 404


def test_workflows_start_recording_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Rec:
        def __init__(self) -> None:
            now = datetime.now(timezone.utc)
            self.id = uuid4()
            self.organization_id = UUID(TEST_ORG_ID)
            self.agent_id = UUID(TEST_AGENT_ID)
            self.title = "rec"
            self.status = "recording"
            self.created_by_user_id = None
            self.started_at = now
            self.stopped_at = None
            self.recording_metadata: Dict[str, Any] = {}
            self.n8n_workflow_id = None
            self.n8n_workflow_url = None
            self.created_at = now
            self.updated_at = now

    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    async def fake_start_recording(*_args: Any, **_kwargs: Any) -> Any:
        return _Rec()

    async def fake_audit(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setenv("ENABLE_WORKFLOW_RECORDER", "true")
    monkeypatch.setattr(workflows_route, "start_recording", fake_start_recording)
    monkeypatch.setattr(workflows_route, "write_audit_log", fake_audit)
    main.app.dependency_overrides[require_client_token] = lambda: TEST_ORG_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/workflows/recordings",
        json={"agent_id": TEST_AGENT_ID, "title": "rec", "notes": "x", "capture_policy": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recording"
    assert body["agent_id"] == TEST_AGENT_ID


def test_workflows_start_recording_human_mode_sends_recording_control(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Rec:
        def __init__(self) -> None:
            now = datetime.now(timezone.utc)
            self.id = uuid4()
            self.organization_id = UUID(TEST_ORG_ID)
            self.agent_id = UUID(TEST_AGENT_ID)
            self.title = "rec"
            self.status = "recording"
            self.created_by_user_id = None
            self.started_at = now
            self.stopped_at = None
            self.recording_metadata: Dict[str, Any] = {}
            self.n8n_workflow_id = None
            self.n8n_workflow_url = None
            self.created_at = now
            self.updated_at = now

    async def override_session() -> AsyncGenerator[DummySession, None]:
        yield DummySession()

    async def fake_start_recording(*_args: Any, **_kwargs: Any) -> Any:
        return _Rec()

    async def fake_audit(*_args: Any, **_kwargs: Any) -> Any:
        return None

    sent: List[Dict[str, Any]] = []

    async def fake_send_json(_agent_id: Any, payload: Dict[str, Any]) -> bool:
        sent.append(payload)
        return True

    monkeypatch.setenv("ENABLE_WORKFLOW_RECORDER", "true")
    monkeypatch.setenv("ENABLE_HUMAN_RECORDER", "true")
    monkeypatch.setattr(workflows_route, "start_recording", fake_start_recording)
    monkeypatch.setattr(workflows_route, "write_audit_log", fake_audit)
    monkeypatch.setattr(workflows_route._ws_manager, "send_json", fake_send_json)
    main.app.dependency_overrides[require_client_token] = lambda: TEST_ORG_ID
    main.app.dependency_overrides[get_session] = override_session

    response = client.post(
        "/workflows/recordings",
        json={
            "agent_id": TEST_AGENT_ID,
            "title": "rec",
            "notes": "x",
            "capture_policy": {"mode": "human"},
        },
    )
    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0]["type"] == "recording_control"
    assert sent[0]["status"] == "start"
