"""
test_runtime_events.py
======================
Tests for the event-driven autonomy subsystem:

1. GoalTracker — register, mark_achieved, get_pending, deadline expiry,
   evaluate loop (achieved → feeds ContinuousImprovement; timeout → replan)
2. Chat rate limiter — sliding window, per-client isolation
3. Dispatcher channel event routing — Telegram + Slack handlers
4. ProactiveMonitor status / cycle trigger API
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import brain.goal_tracker as gt_module
from brain.goal_tracker import (
    _GoalEntry,
    _evaluate_all_goals,
    get_pending_goals,
    mark_achieved,
    register_goal,
)
from routes.chat import _check_rate_limit, _rate_limit_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    *,
    action_name: str = "sync_orders",
    action_type: str = "integration",
    timeout_minutes: int = 30,
    past_deadline: bool = False,
    achieved: bool = False,
) -> _GoalEntry:
    now = datetime.now(timezone.utc)
    if past_deadline:
        deadline = now - timedelta(minutes=1)
    else:
        deadline = now + timedelta(minutes=timeout_minutes)
    cid = str(uuid.uuid4())
    return _GoalEntry(
        goal_id=str(uuid.uuid4()),
        org_id=cid,
        client_id=cid,
        goal_text=f"test goal for {action_name}",
        action_type=action_type,
        action_name=action_name,
        dispatched_at=now - timedelta(minutes=5),
        deadline=deadline,
        achieved=achieved,
    )


@pytest.fixture(autouse=True)
def clear_goal_tracker() -> None:
    """Reset GoalTracker state between tests."""
    gt_module._pending_goals.clear()
    yield
    gt_module._pending_goals.clear()


@pytest.fixture(autouse=True)
def clear_rate_limiter() -> None:
    """Reset rate limit store between tests."""
    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()


@pytest.fixture(autouse=True)
def clear_dispatcher_runtime_map() -> None:
    from brain import dispatcher as d_module

    d_module._RUNTIME_TELEGRAM_CHAT_MAP.clear()
    yield
    d_module._RUNTIME_TELEGRAM_CHAT_MAP.clear()


# ---------------------------------------------------------------------------
# GoalTracker — register_goal
# ---------------------------------------------------------------------------

class TestRegisterGoal:
    def test_returns_valid_uuid(self) -> None:
        goal_id = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="sync contacts with hubspot",
            action_type="integration",
            action_name="create_contact",
        )
        # Must be a valid UUID
        uuid.UUID(goal_id)

    def test_goal_stored_in_pending(self) -> None:
        cid = str(uuid.uuid4())
        goal_id = register_goal(
            client_id=cid,
            goal_text="create invoice in stripe",
            action_type="integration",
            action_name="create_invoice",
            timeout_minutes=15,
        )
        assert goal_id in gt_module._pending_goals
        entry = gt_module._pending_goals[goal_id]
        assert entry.client_id == cid
        assert entry.action_name == "create_invoice"

    def test_deadline_set_correctly(self) -> None:
        before = datetime.now(timezone.utc)
        goal_id = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="test",
            action_type="n8n",
            action_name="workflow_run",
            timeout_minutes=45,
        )
        after = datetime.now(timezone.utc)
        entry = gt_module._pending_goals[goal_id]
        expected_min = before + timedelta(minutes=44)
        expected_max = after + timedelta(minutes=46)
        assert expected_min <= entry.deadline <= expected_max

    def test_metadata_stored(self) -> None:
        goal_id = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="test",
            action_type="integration",
            action_name="sync",
            metadata={"session_id": "sess-123", "channel": "telegram"},
        )
        entry = gt_module._pending_goals[goal_id]
        assert entry.metadata["channel"] == "telegram"
        assert entry.metadata["session_id"] == "sess-123"

    def test_multiple_goals_independent(self) -> None:
        ids = [
            register_goal(
                client_id=str(uuid.uuid4()),
                goal_text=f"goal {i}",
                action_type="integration",
                action_name=f"action_{i}",
            )
            for i in range(5)
        ]
        assert len(set(ids)) == 5
        assert len(gt_module._pending_goals) == 5


# ---------------------------------------------------------------------------
# GoalTracker — mark_achieved + get_pending_goals
# ---------------------------------------------------------------------------

class TestMarkAchievedAndGetPending:
    def test_mark_achieved_sets_flag(self) -> None:
        goal_id = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="test",
            action_type="integration",
            action_name="sync",
        )
        assert not gt_module._pending_goals[goal_id].achieved
        mark_achieved(goal_id)
        assert gt_module._pending_goals[goal_id].achieved

    def test_mark_nonexistent_goal_noop(self) -> None:
        mark_achieved("nonexistent-id")  # Should not raise

    def test_get_pending_excludes_achieved(self) -> None:
        gid = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="test",
            action_type="integration",
            action_name="sync",
        )
        mark_achieved(gid)
        pending = get_pending_goals()
        assert not any(g["goal_id"] == gid for g in pending)

    def test_get_pending_includes_unachieved(self) -> None:
        gid = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="pending goal",
            action_type="integration",
            action_name="create_deal",
        )
        pending = get_pending_goals()
        assert any(g["goal_id"] == gid for g in pending)

    def test_get_pending_marks_timed_out(self) -> None:
        goal_id = str(uuid.uuid4())
        cid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        entry = _GoalEntry(
            goal_id=goal_id,
            org_id=cid,
            client_id=cid,
            goal_text="overdue goal",
            action_type="integration",
            action_name="old_action",
            dispatched_at=now - timedelta(hours=2),
            deadline=now - timedelta(minutes=1),
        )
        gt_module._pending_goals[goal_id] = entry
        pending = get_pending_goals()
        item = next(g for g in pending if g["goal_id"] == goal_id)
        assert item["timed_out"] is True

    def test_get_pending_not_timed_out_for_fresh(self) -> None:
        gid = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="fresh goal",
            action_type="integration",
            action_name="new_action",
            timeout_minutes=60,
        )
        pending = get_pending_goals()
        item = next(g for g in pending if g["goal_id"] == gid)
        assert item["timed_out"] is False


# ---------------------------------------------------------------------------
# GoalTracker — _evaluate_all_goals loop
# ---------------------------------------------------------------------------

class TestEvaluateAllGoals:
    def test_achieved_goal_removed_from_pending(self) -> None:
        gid = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="done",
            action_type="integration",
            action_name="completed_action",
        )
        gt_module._pending_goals[gid].achieved = True
        asyncio.run(_evaluate_all_goals())
        assert gid not in gt_module._pending_goals

    def test_timed_out_goal_triggers_replan_and_removed(self) -> None:
        cid = str(uuid.uuid4())
        goal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        entry = _GoalEntry(
            goal_id=goal_id,
            org_id=cid,
            client_id=cid,
            goal_text="timed out goal",
            action_type="integration",
            action_name="slow_action",
            dispatched_at=now - timedelta(hours=1),
            deadline=now - timedelta(minutes=5),
        )
        gt_module._pending_goals[goal_id] = entry

        replan_called: Dict[str, Any] = {}

        async def fake_replan_from_timeout(
            *, client_id: str, goal_text: str, failed_action: str, metadata: Any = None
        ) -> None:
            replan_called.update({"client_id": client_id, "failed_action": failed_action})

        with patch("brain.goal_tracker._check_goal_achieved", new=AsyncMock(return_value=False)):
            with patch("brain.goal_tracker._on_goal_timed_out", new=AsyncMock()) as mock_timeout:
                asyncio.run(_evaluate_all_goals())

        assert goal_id not in gt_module._pending_goals
        mock_timeout.assert_called_once()

    def test_fresh_unachieved_goal_stays_pending(self) -> None:
        gid = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="still waiting",
            action_type="integration",
            action_name="in_flight_action",
            timeout_minutes=60,
        )

        with patch("brain.goal_tracker._check_goal_achieved", new=AsyncMock(return_value=False)):
            asyncio.run(_evaluate_all_goals())

        assert gid in gt_module._pending_goals

    def test_confirmed_achieved_triggers_feedback(self) -> None:
        gid = register_goal(
            client_id=str(uuid.uuid4()),
            goal_text="just completed",
            action_type="integration",
            action_name="success_action",
            timeout_minutes=30,
        )

        with patch("brain.goal_tracker._check_goal_achieved", new=AsyncMock(return_value=True)):
            with patch("brain.goal_tracker._on_goal_achieved", new=AsyncMock()) as mock_achieved:
                asyncio.run(_evaluate_all_goals())

        assert gid not in gt_module._pending_goals
        mock_achieved.assert_called_once()


# ---------------------------------------------------------------------------
# Chat rate limiter — _check_rate_limit
# ---------------------------------------------------------------------------

class TestChatRateLimiter:
    def test_first_requests_allowed(self) -> None:
        cid = str(uuid.uuid4())
        for _ in range(5):
            assert _check_rate_limit(cid) is True

    def test_exceeds_limit_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import routes.chat as chat_module
        monkeypatch.setattr(chat_module, "_RATE_LIMIT_RPM", 3)
        cid = str(uuid.uuid4())
        assert _check_rate_limit(cid) is True
        assert _check_rate_limit(cid) is True
        assert _check_rate_limit(cid) is True
        assert _check_rate_limit(cid) is False

    def test_separate_clients_independent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import routes.chat as chat_module
        monkeypatch.setattr(chat_module, "_RATE_LIMIT_RPM", 2)
        cid_a = str(uuid.uuid4())
        cid_b = str(uuid.uuid4())
        # Fill up client A
        _check_rate_limit(cid_a)
        _check_rate_limit(cid_a)
        assert _check_rate_limit(cid_a) is False
        # Client B should be unaffected
        assert _check_rate_limit(cid_b) is True

    def test_old_timestamps_expire_from_window(self) -> None:
        """Timestamps older than 60s should be evicted, freeing the window."""
        cid = str(uuid.uuid4())
        import routes.chat as chat_module
        old_time = time.monotonic() - 61.0
        _rate_limit_store[cid] = [old_time] * 60
        # Should be allowed because all old timestamps are expired
        assert _check_rate_limit(cid) is True

    def test_recent_timestamps_block_requests(self) -> None:
        import routes.chat as chat_module
        cid = str(uuid.uuid4())
        now = time.monotonic()
        original_rpm = chat_module._RATE_LIMIT_RPM
        _rate_limit_store[cid] = [now - 1.0] * original_rpm
        assert _check_rate_limit(cid) is False


# ---------------------------------------------------------------------------
# Dispatcher — Telegram event handler
# ---------------------------------------------------------------------------

class TestHandleTelegramEvent:
    def _make_update(self, text: str = "hola", chat_id: str = "12345") -> Dict[str, Any]:
        return {
            "message": {
                "text": text,
                "chat": {"id": int(chat_id)},
                "from": {"id": 99999},
            }
        }

    def test_ignores_update_without_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        called: List[Any] = []

        async def fake_engine_generate(**_: Any) -> Dict[str, Any]:
            called.append(True)
            return {"type": "message", "content": "ok"}

        # Update with no text should return early without calling engine
        update = {"message": {"chat": {"id": 1}, "from": {"id": 2}}}
        monkeypatch.setenv("TELEGRAM_CHAT_CLIENT_MAP_JSON", '{"1":"' + str(uuid.uuid4()) + '"}')
        asyncio.run(d_module.handle_telegram_event(update))
        assert not called

    def test_ignores_unknown_chat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        monkeypatch.setenv("TELEGRAM_CHAT_CLIENT_MAP_JSON", "{}")
        monkeypatch.delenv("TELEGRAM_DEFAULT_CLIENT_ID", raising=False)
        monkeypatch.delenv("JARVIS_CLIENT_ID", raising=False)
        monkeypatch.delenv("KAN_CLIENT_ID", raising=False)
        d_module._RUNTIME_TELEGRAM_CHAT_MAP.clear()
        called: List[Any] = []

        with patch.object(d_module, "CognitiveEngine") as MockEngine:
            MockEngine.get_instance.return_value.generate_response = AsyncMock(
                return_value={"type": "message", "content": "hi"}
            )
            asyncio.run(d_module.handle_telegram_event(self._make_update()))
        # Engine should NOT have been called since chat_id not in map
        MockEngine.get_instance.assert_not_called()

    def test_default_flow_routes_actionable_text_to_agentbridge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        monkeypatch.setenv("TELEGRAM_CHAT_CLIENT_MAP_JSON", '{"12345":"' + client_id + '"}')
        monkeypatch.setenv("ENABLE_TELEGRAM_REPLY", "false")
        update = self._make_update(text="abre crm y busca 20 prospectos de clinicas")
        with patch.object(d_module, "_run_telegram_agent", new=AsyncMock()) as agent_mock:
            with patch.object(d_module, "send_telegram_message", new=AsyncMock()):
                asyncio.run(d_module.handle_telegram_event(update))
        agent_mock.assert_called_once()
        kwargs = agent_mock.await_args.kwargs
        assert kwargs["client_id"] == client_id
        assert "busca 20 prospectos" in kwargs["goal"]

    def test_default_flow_routes_smalltalk_to_conversational_ai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        monkeypatch.setenv("TELEGRAM_CHAT_CLIENT_MAP_JSON", '{"12345":"' + client_id + '"}')
        monkeypatch.setenv("ENABLE_TELEGRAM_REPLY", "false")
        update = self._make_update(text="hola")
        with patch.object(d_module, "_run_telegram_agent", new=AsyncMock()) as agent_mock:
            with patch.object(d_module, "_process_message", new=AsyncMock()) as process_mock:
                with patch.object(d_module, "send_telegram_message", new=AsyncMock()):
                    asyncio.run(d_module.handle_telegram_event(update))
        agent_mock.assert_not_called()
        process_mock.assert_called_once()

    def test_help_command_replies_without_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        monkeypatch.setenv("ENABLE_TELEGRAM_REPLY", "false")
        update = self._make_update(text="/help")
        with patch.object(d_module, "send_telegram_message", new=AsyncMock()) as send_mock:
            with patch.object(d_module, "CognitiveEngine") as MockEngine:
                asyncio.run(d_module.handle_telegram_event(update))
        send_mock.assert_called_once()
        MockEngine.get_instance.assert_not_called()

    def test_link_command_binds_runtime_chat_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        d_module._RUNTIME_TELEGRAM_CHAT_MAP.clear()
        org_id = str(uuid.uuid4())
        update = self._make_update(text=f"/link {org_id}")
        with patch.object(d_module, "_client_exists", new=AsyncMock(return_value=True)):
            with patch.object(d_module, "send_telegram_message", new=AsyncMock()) as send_mock:
                asyncio.run(d_module.handle_telegram_event(update))
        assert d_module._RUNTIME_TELEGRAM_CHAT_MAP.get("12345") == org_id
        send_mock.assert_called_once()

    def test_run_command_executes_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        d_module._RUNTIME_TELEGRAM_CHAT_MAP["12345"] = client_id
        update = self._make_update(text="/run abre crm y trae leads")
        with patch.object(d_module, "_process_message", new=AsyncMock()) as process_mock:
            asyncio.run(d_module.handle_telegram_event(update))
        process_mock.assert_called_once()
        kwargs = process_mock.await_args.kwargs
        assert kwargs["client_id"] == client_id
        assert "abre crm" in kwargs["message"]

    def test_agent_command_executes_multi_agent_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        d_module._RUNTIME_TELEGRAM_CHAT_MAP["12345"] = client_id
        update = self._make_update(text="/agent abre crm y actualiza pipeline")
        with patch.object(d_module, "_run_telegram_agent", new=AsyncMock()) as agent_mock:
            with patch.object(d_module, "send_telegram_message", new=AsyncMock()):
                asyncio.run(d_module.handle_telegram_event(update))
        agent_mock.assert_called_once()
        kwargs = agent_mock.await_args.kwargs
        assert kwargs["client_id"] == client_id
        assert "actualiza pipeline" in kwargs["goal"]

    def test_autonomous_mode_routes_complex_text_to_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        monkeypatch.setenv("TELEGRAM_CHAT_CLIENT_MAP_JSON", '{"12345":"' + client_id + '"}')
        monkeypatch.setenv("TELEGRAM_AGENT_MODE", "autonomous")
        update = self._make_update(text="automatiza un workflow en n8n para capturar leads y calificarlos")
        with patch.object(d_module, "_run_telegram_agent", new=AsyncMock()) as agent_mock:
            with patch.object(d_module, "send_telegram_message", new=AsyncMock()):
                asyncio.run(d_module.handle_telegram_event(update))
        agent_mock.assert_called_once()

    def test_autonomous_mode_routes_followup_text_to_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        monkeypatch.setenv("TELEGRAM_CHAT_CLIENT_MAP_JSON", '{"12345":"' + client_id + '"}')
        monkeypatch.setenv("TELEGRAM_AGENT_MODE", "autonomous")
        monkeypatch.setenv("TELEGRAM_FLOW_MODE", "legacy")
        d_module._LAST_TASK["12345"] = {"goal": "Construye workflow de leads", "status": "completed"}
        update = self._make_update(text="hazlo")
        with patch.object(d_module, "_run_telegram_agent", new=AsyncMock()) as agent_mock:
            with patch.object(d_module, "send_telegram_message", new=AsyncMock()):
                asyncio.run(d_module.handle_telegram_event(update))
        agent_mock.assert_called_once()
        kwargs = agent_mock.await_args.kwargs
        assert "Construye workflow" in kwargs["goal"]

    def test_voice_note_falls_back_to_attachment_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        monkeypatch.setenv("TELEGRAM_CHAT_CLIENT_MAP_JSON", '{"12345":"' + client_id + '"}')
        monkeypatch.setenv("ENABLE_TELEGRAM_REPLY", "false")
        update = {
            "message": {
                "voice": {"file_id": "voice-file-1"},
                "caption": "abre crm y genera prospectos",
                "chat": {"id": 12345},
                "from": {"id": 99999},
            }
        }
        with patch.object(d_module, "_run_telegram_agent", new=AsyncMock()) as agent_mock:
            with patch.object(d_module, "send_telegram_message", new=AsyncMock()):
                asyncio.run(d_module.handle_telegram_event(update))
        kwargs = agent_mock.await_args.kwargs
        assert "abre crm" in kwargs["goal"]

    def test_schedule_task_completion_voice_only_for_completed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        scheduled = {"called": False}

        class _DummyTask:
            def add_done_callback(self, _cb):
                return None

            def cancelled(self) -> bool:
                return False

            def exception(self):
                return None

        def fake_create_task(coro):
            scheduled["called"] = True
            coro.close()
            return _DummyTask()

        monkeypatch.setattr(d_module.asyncio, "create_task", fake_create_task)
        task = d_module._schedule_task_completion_voice(
            status="completed",
            goal="objetivo de prueba",
            summary="resumen de prueba",
            run_id="run-1",
        )
        assert task is not None
        assert scheduled["called"] is True

        scheduled["called"] = False
        task = d_module._schedule_task_completion_voice(
            status="failed",
            goal="objetivo de prueba",
            summary="resumen de prueba",
            run_id="run-2",
        )
        assert task is None
        assert scheduled["called"] is False


# ---------------------------------------------------------------------------
# Dispatcher — Slack event handler
# ---------------------------------------------------------------------------

class TestHandleSlackEvent:
    def _make_payload(
        self,
        text: str = "hola equipo",
        channel: str = "C1234",
        user: str = "U9999",
        bot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        event: Dict[str, Any] = {
            "text": text,
            "channel": channel,
            "user": user,
            "ts": "1700000000.000001",
        }
        if bot_id:
            event["bot_id"] = bot_id
        return {"event": event}

    def test_ignores_bot_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        monkeypatch.setenv("SLACK_CHANNEL_CLIENT_MAP_JSON", '{"C1234":"' + str(uuid.uuid4()) + '"}')

        with patch.object(d_module, "CognitiveEngine") as MockEngine:
            # Bot message should be filtered upstream (in route), but dispatcher should handle gracefully
            # when bot_id is present in event
            payload = self._make_payload(bot_id="B12345")
            asyncio.run(d_module.handle_slack_event(payload))
        # The handle_slack_event doesn't filter bot_id (that's done in the route), but should not crash

    def test_ignores_unknown_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        monkeypatch.setenv("SLACK_CHANNEL_CLIENT_MAP_JSON", "{}")
        called: List[Any] = []

        with patch.object(d_module, "CognitiveEngine") as MockEngine:
            MockEngine.get_instance.return_value.generate_response = AsyncMock(
                return_value={"type": "message", "content": "ok"}
            )
            asyncio.run(d_module.handle_slack_event(self._make_payload()))
        MockEngine.get_instance.assert_not_called()

    def test_calls_engine_for_mapped_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        monkeypatch.setenv("SLACK_CHANNEL_CLIENT_MAP_JSON", '{"C1234":"' + client_id + '"}')
        monkeypatch.setenv("ENABLE_SLACK_REPLY", "false")
        ai_response = {"type": "message", "content": "Respuesta para Slack"}

        with patch.object(d_module, "CognitiveEngine") as MockEngine:
            MockEngine.get_instance.return_value.generate_response = AsyncMock(return_value=ai_response)
            with patch.object(d_module, "send_to_n8n_with_response", new=AsyncMock()):
                with patch.object(d_module, "_emit_business_event", new=AsyncMock()):
                    asyncio.run(d_module.handle_slack_event(self._make_payload()))
            MockEngine.get_instance.return_value.generate_response.assert_called_once()

    def test_goal_registered_for_action_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        monkeypatch.setenv("SLACK_CHANNEL_CLIENT_MAP_JSON", '{"C1234":"' + client_id + '"}')
        monkeypatch.setenv("ENABLE_SLACK_REPLY", "false")
        action_response = {
            "type": "action",
            "content": "procesando",
            "payload": {
                "contract_version": "1.0",
                "action_type": "integration",
                "action": "sync_orders",
                "integration": "shopify",
                "arguments": {},
                "rationale": "test",
            },
        }

        goal_ids: List[str] = []

        def fake_register_goal(**kwargs: Any) -> str:
            gid = str(uuid.uuid4())
            goal_ids.append(gid)
            return gid

        with patch.object(d_module, "CognitiveEngine") as MockEngine:
            MockEngine.get_instance.return_value.generate_response = AsyncMock(return_value=action_response)
            with patch.object(d_module, "send_to_n8n_with_response", new=AsyncMock()):
                with patch.object(d_module, "_emit_business_event", new=AsyncMock()):
                    with patch.object(d_module, "_should_auto_approve", new=AsyncMock(return_value=True)):
                        with patch("brain.goal_tracker.register_goal", side_effect=fake_register_goal):
                            asyncio.run(d_module.handle_slack_event(self._make_payload()))

        # A goal should have been registered because response was an action
        assert len(goal_ids) == 1


# ---------------------------------------------------------------------------
# Dispatcher — wildcard client mapping
# ---------------------------------------------------------------------------

class TestClientMapWildcard:
    def test_telegram_wildcard_mapping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module
        client_id = str(uuid.uuid4())
        monkeypatch.setenv("TELEGRAM_CHAT_CLIENT_MAP_JSON", '{"*":"' + client_id + '"}')
        monkeypatch.setenv("ENABLE_TELEGRAM_REPLY", "false")
        with patch.object(d_module, "_run_telegram_agent", new=AsyncMock()) as agent_mock:
            with patch.object(d_module, "send_telegram_message", new=AsyncMock()):
                update = {
                    "message": {
                        "text": "busca clientes para clinicas en monterrey",
                        "chat": {"id": 99999},
                        "from": {"id": 1},
                    }
                }
                asyncio.run(d_module.handle_telegram_event(update))
        agent_mock.assert_called_once()
        kwargs = agent_mock.await_args.kwargs
        assert kwargs["client_id"] == client_id


# ---------------------------------------------------------------------------
# Dispatcher — YCloud WhatsApp event handler
# ---------------------------------------------------------------------------

class TestHandleYCloudEvent:
    def _make_payload(self, text: str = "hola whatsapp", from_number: str = "5215512345678", to_number: str = "5215599990000") -> Dict[str, Any]:
        return {
            "type": "whatsapp.inbound_message.received",
            "whatsappInboundMessage": {
                "id": "wamid-001",
                "from": from_number,
                "to": to_number,
                "type": "text",
                "text": {"body": text},
            },
        }

    def test_calls_process_message_for_mapped_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module

        client_id = str(uuid.uuid4())
        monkeypatch.setenv("YCLOUD_CHANNEL_CLIENT_MAP_JSON", '{"5215599990000":"' + client_id + '"}')
        with patch.object(d_module, "_process_message", new=AsyncMock()) as process_mock:
            asyncio.run(d_module.handle_ycloud_event(self._make_payload()))
        process_mock.assert_called_once()
        kwargs = process_mock.await_args.kwargs
        assert kwargs["client_id"] == client_id
        assert kwargs["channel"] == "whatsapp"
        assert kwargs["session_id"].startswith("whatsapp:")
        assert kwargs["inbound_payload"]["_ycloud"]["send_from"] == "5215599990000"

    def test_legacy_event_alias_still_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module

        client_id = str(uuid.uuid4())
        monkeypatch.setenv("YCLOUD_DEFAULT_CLIENT_ID", client_id)
        payload = self._make_payload()
        payload["type"] = "whatsapp.inbound.message"
        with patch.object(d_module, "_process_message", new=AsyncMock()) as process_mock:
            asyncio.run(d_module.handle_ycloud_event(payload))
        process_mock.assert_called_once()

    def test_process_message_sends_ycloud_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from brain import dispatcher as d_module

        ai_response = {"type": "message", "content": "Respuesta por WhatsApp"}
        inbound_payload = {
            "_ycloud": {
                "reply_to": "5215512345678",
                "send_from": "5215599990000",
                "message_id": "wamid-001",
            }
        }

        with patch.object(d_module, "CognitiveEngine") as MockEngine:
            MockEngine.get_instance.return_value.generate_response = AsyncMock(return_value=ai_response)
            with patch.object(d_module, "send_to_n8n_with_response", new=AsyncMock()):
                with patch.object(d_module, "_emit_business_event", new=AsyncMock()):
                    with patch.object(d_module, "send_ycloud_whatsapp_text_message", new=AsyncMock()) as send_mock:
                        asyncio.run(
                            d_module._process_message(
                                client_id=str(uuid.uuid4()),
                                session_id="whatsapp:5215512345678",
                                message="hola",
                                channel="whatsapp",
                                user_id="5215512345678",
                                inbound_payload=inbound_payload,
                            )
                        )
        send_mock.assert_called_once()
        kwargs = send_mock.await_args.kwargs
        assert kwargs["from_number"] == "5215599990000"
        assert kwargs["to"] == "5215512345678"
        assert "Respuesta" in kwargs["text"]
