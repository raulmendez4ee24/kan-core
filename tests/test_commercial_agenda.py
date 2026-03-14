from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid
from typing import Any, Dict, List

import pytest

import brain.core as core_module
from brain.commercial_agenda import (
    build_tomorrow_agenda_snapshot,
    create_meeting_request,
    get_recent_tomorrow_agenda_snapshot,
    _tomorrow_local_date,
)
from brain.core import CognitiveEngine
from brain.crm_engine import dispatch_due_followups
from models import FollowUpJob


class _Scalars:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def all(self) -> List[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _ExecuteResult:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class FakeSession:
    def __init__(self, execute_rows: List[List[Any]], client: Any = None) -> None:
        self._execute_rows = list(execute_rows)
        self._client = client
        self.added: List[Any] = []
        self.added_many: List[Any] = []
        self.commits = 0
        self.refreshed: List[Any] = []

    async def get(self, _model: Any, _id: Any) -> Any:
        return self._client

    async def execute(self, _stmt: Any) -> _ExecuteResult:
        if not self._execute_rows:
            return _ExecuteResult([])
        return _ExecuteResult(self._execute_rows.pop(0))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def add_all(self, objs: List[Any]) -> None:
        self.added_many.extend(objs)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)


class MemoryOk:
    def __init__(self) -> None:
        self.store_calls: List[Dict[str, Any]] = []

    async def search_context(self, *args: Any, **kwargs: Any) -> List[str]:
        return []

    async def store_interaction(self, **kwargs: Any) -> None:
        self.store_calls.append(kwargs)


@pytest.fixture
def engine() -> CognitiveEngine:
    eng = object.__new__(CognitiveEngine)
    eng._api_key = "k"
    eng._provider = "gemini"
    eng._base_url = "https://example.test"
    eng._timeout = 10.0
    eng._retries = 1
    eng._backoff_base = 0.1
    eng._backoff_max = 1.0
    eng._max_output_tokens = None
    eng._max_context_chars = 200
    eng._enable_bias_guard = True
    eng._memory = MemoryOk()
    return eng


def test_create_meeting_request_creates_internal_job_and_first_person_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead = SimpleNamespace(id=uuid.uuid4(), email=None)

    async def fake_upsert_lead(*_args: Any, **_kwargs: Any) -> Any:
        return lead

    monkeypatch.setattr("brain.crm_engine.upsert_lead", fake_upsert_lead)
    session = FakeSession(execute_rows=[[], []])

    result = asyncio.run(
        create_meeting_request(
            session,
            organization_id=uuid.uuid4(),
            session_id="whatsapp:+5215512345678",
            message="Quiero implementar un chatbot y hablar con alguien",
        )
    )

    assert result["job"].status == "awaiting_availability"
    assert "Yo ya registré tu solicitud" in result["reply"]
    assert "compárteme 1 o 2 ventanas" in result["reply"]
    assert any(
        getattr(obj, "payload", {}).get("kind") == "meeting_request" for obj in session.added
    )


def test_build_tomorrow_agenda_snapshot_falls_back_without_calendar() -> None:
    org_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    meeting_job = SimpleNamespace(
        id=uuid.uuid4(),
        status="requested",
        scheduled_for=tomorrow,
        payload={
            "kind": "meeting_request",
            "products_of_interest": ["chatbots"],
            "preferred_time_windows": ["mañana por la tarde"],
            "business_need": "implementar chatbot",
        },
        lead_id=lead_id,
    )
    generic_followup = SimpleNamespace(
        id=uuid.uuid4(),
        status="scheduled",
        scheduled_for=tomorrow,
        payload={"kind": "followup", "message": "confirmar propuesta"},
        lead_id=lead_id,
    )
    hot_lead = SimpleNamespace(
        id=lead_id,
        name="Lead caliente",
        email="lead@example.com",
        phone=None,
        source="whatsapp",
        score=0.9,
        conversion_probability=0.8,
    )
    session = FakeSession(execute_rows=[[meeting_job], [meeting_job, generic_followup], [hot_lead]])

    snapshot = asyncio.run(
        build_tomorrow_agenda_snapshot(
            session,
            organization_id=org_id,
        )
    )

    assert snapshot["source_status"] == "calendar_unavailable"
    assert "No tuve acceso al calendario externo" in snapshot["summary_text"]
    assert snapshot["meetings"][0]["products_of_interest"] == ["chatbots"]
    assert snapshot["critical_items"][0]["name"] == "Lead caliente"


def test_get_recent_tomorrow_agenda_snapshot_reuses_recent_snapshot() -> None:
    tomorrow = _tomorrow_local_date().isoformat()
    event = SimpleNamespace(
        payload={
            "target_date": tomorrow,
            "source_status": "calendar_unavailable",
            "meetings": [],
            "internal_followups": [],
            "critical_items": [],
            "summary_text": "Yo ya preparé mi agenda.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        created_at=datetime.now(timezone.utc),
    )
    session = FakeSession(execute_rows=[[event], [], []])

    snapshot = asyncio.run(
        get_recent_tomorrow_agenda_snapshot(
            session,
            organization_id=uuid.uuid4(),
        )
    )

    assert snapshot is not None
    assert snapshot["summary_text"] == "Yo ya preparé mi agenda."


def test_generate_with_session_short_circuits_to_commercial_agenda(
    monkeypatch: pytest.MonkeyPatch,
    engine: CognitiveEngine,
) -> None:
    client = SimpleNamespace(
        system_prompt=None,
        model_name="gemini-1.5-flash",
        temperature=0.4,
    )
    session = FakeSession(execute_rows=[], client=client)

    async def fake_special(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        return {
            "type": "message",
            "content": "Yo ya registré tu solicitud para una reunión comercial.",
        }

    async def should_not_call_model(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise AssertionError("Model call should not happen for meeting intent")

    monkeypatch.setattr(core_module, "maybe_handle_special_request", fake_special)
    engine._call_model = should_not_call_model  # type: ignore[method-assign]

    result = asyncio.run(
        engine._generate_with_session(
            session,
            client_id=str(uuid.uuid4()),
            session_id="whatsapp:+5215512345678",
            message="Quiero una demo",
        )
    )

    assert result["content"].startswith("Yo ya registré")
    assert session.commits == 1
    assert len(engine._memory.store_calls) == 2


def test_dispatch_due_followups_handles_tomorrow_snapshot_job_without_n8n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    due_job = FollowUpJob(
        organization_id=org_id,
        lead_id=None,
        status="scheduled",
        scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=5),
        decision_reason="nightly snapshot",
        payload={"kind": "tomorrow_agenda_snapshot"},
        result={},
    )
    session = FakeSession(execute_rows=[[due_job]])

    async def fake_snapshot(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        return {
            "target_date": "2026-03-11",
            "source_status": "calendar_unavailable",
            "meetings": [],
            "internal_followups": [],
            "critical_items": [],
            "summary_text": "Yo ya preparé mi agenda.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def fake_ensure(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fail_n8n(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise AssertionError("n8n should not be called for tomorrow agenda snapshot")

    monkeypatch.setattr("brain.commercial_agenda.build_tomorrow_agenda_snapshot", fake_snapshot)
    monkeypatch.setattr("brain.commercial_agenda.ensure_tomorrow_agenda_snapshot_job", fake_ensure)
    monkeypatch.setattr("brain.crm_engine.send_to_n8n_with_response", fail_n8n)

    result = asyncio.run(
        dispatch_due_followups(
            session,
            organization_id=org_id,
        )
    )

    assert result["dispatched"] == 1
    assert due_job.status == "completed"
    assert due_job.result["snapshot_generated"] is True
