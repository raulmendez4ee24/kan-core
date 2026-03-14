"""Tests for LLM-in-Sandbox ReAct loop.

Covers:
- _parse_llm_output — THOUGHT+ACTION and FINAL_ANSWER paths
- _is_sandbox_eligible — multi-step heuristic
- LLMSandbox.run() — FINAL_ANSWER path, max_iterations, action+observe loop
- SandboxExecutor.execute() — dry-run vs commit, n8n mock
- SandboxExecutor.commit_all() — batch commit
- schemas/sandbox.py — SandboxStep and SandboxResult validation
- routes/sandbox.py — /sandbox/run and /sandbox/commit/{run_id}
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Lightweight stubs for heavy imports so tests can run without full stack
# ---------------------------------------------------------------------------

def _stub_modules():
    stubs = {
        "observability": types.ModuleType("observability"),
        "database": types.ModuleType("database"),
        "models": types.ModuleType("models"),
        "tools.retry": types.ModuleType("tools.retry"),
        "tools.n8n_client": types.ModuleType("tools.n8n_client"),
        "brain.memory": types.ModuleType("brain.memory"),
        "brain.runtime_hooks": types.ModuleType("brain.runtime_hooks"),
        "brain.continuous_improvement": types.ModuleType("brain.continuous_improvement"),
        "security": types.ModuleType("security"),
    }
    stubs["observability"].capture_exception = lambda *a, **kw: None
    stubs["observability"].init_observability = lambda *a, **kw: None

    class _FakeAsyncSessionCtx:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False

    class _FakeBase:
        pass

    stubs["database"].AsyncSessionLocal = _FakeAsyncSessionCtx
    stubs["database"].init_db = AsyncMock()
    stubs["database"].Base = _FakeBase
    stubs["database"].get_security_manager = lambda: None

    # Stub SQLAlchemy-based model classes so brain.core can be imported cleanly
    for _cls_name in (
        "Client", "ApiIntegration", "ConversationLog", "TokenUsage",
        "BusinessEvent", "BusinessMetricsSnapshot", "AutomationRun", "IntegrationRunLog", "RecommendationAction",
    ):
        setattr(stubs["models"], _cls_name, type(_cls_name, (), {}))

    stubs["tools.retry"].request_with_retry = AsyncMock()
    stubs["tools.retry"].post_json_with_retry = AsyncMock()
    stubs["tools.n8n_client"].send_to_n8n = AsyncMock(return_value=True)
    stubs["tools.n8n_client"].send_to_n8n_with_response = AsyncMock(
        return_value={"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}
    )

    class _NoOpMemory:
        async def search_context(self, *a, **kw): return []
        async def store_interaction(self, *a, **kw): return None

    stubs["brain.memory"].NoOpMemory = _NoOpMemory
    stubs["brain.memory"].VectorMemory = _NoOpMemory

    # RuntimeHooks stub — passes payload through unchanged
    class _FakeRuntimeHooks:
        def __init__(self): self._hooks = {}
        async def run(self, event, payload): return payload
        def register(self, *a, **kw): pass

    _hooks_instance = _FakeRuntimeHooks()
    stubs["brain.runtime_hooks"].runtime_hooks = _hooks_instance
    stubs["brain.runtime_hooks"].RuntimeHooks = _FakeRuntimeHooks
    stubs["brain.runtime_hooks"].HOOK_BEFORE_PROMPT = "before_prompt"
    stubs["brain.runtime_hooks"].HOOK_BEFORE_MODEL = "before_model"
    stubs["brain.runtime_hooks"].HOOK_AFTER_MODEL = "after_model"
    stubs["brain.runtime_hooks"].HOOK_BEFORE_TOOL = "before_tool"
    stubs["brain.runtime_hooks"].HOOK_AFTER_TOOL = "after_tool"
    stubs["brain.runtime_hooks"].HOOK_BEFORE_COMPACTION = "before_compaction"
    stubs["brain.runtime_hooks"].HOOK_AFTER_RUN = "after_run"

    stubs["brain.continuous_improvement"].get_confidence_threshold = AsyncMock(
        return_value=0.80
    )
    stubs["security"].require_client_token = lambda: "test-client"

    installed: Dict[str, types.ModuleType] = {}
    for name, mod in stubs.items():
        if name in sys.modules:
            continue
        sys.modules[name] = mod
        installed[name] = mod
    return installed


def _restore_stub_modules(installed: Dict[str, types.ModuleType]) -> None:
    for name, mod in installed.items():
        current = sys.modules.get(name)
        if current is mod:
            sys.modules.pop(name, None)
        if "." not in name:
            continue
        parent_name, child_name = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is None:
            continue
        if getattr(parent, child_name, None) is mod:
            delattr(parent, child_name)

_INSTALLED_STUBS = _stub_modules()

# Now safe to import our modules
from brain.llm_sandbox import LLMSandbox, _parse_llm_output   # noqa: E402
from brain.sandbox_executor import SandboxExecutor, ToolObservation  # noqa: E402
from schemas.sandbox import SandboxResult, SandboxStep          # noqa: E402

import brain.core as _core_module  # noqa: E402

_restore_stub_modules(_INSTALLED_STUBS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(responses: List[str]):
    """Return a fake CognitiveEngine that yields responses in order."""

    class FakeEngine:
        _idx = 0
        _responses = responses

        def _resolve_model_name(self, _):
            return "test-model"

        def _build_request(self, *, system_prompt, history, message, model_name, temperature):
            return {"model": model_name, "messages": [{"role": "user", "content": message}]}

        async def _call_model(self, model_name, body):
            if self._idx >= len(self._responses):
                return {"choices": [{"message": {"content": "FINAL_ANSWER: done"}}]}
            resp = self._responses[self._idx]
            self._idx += 1
            return {"choices": [{"message": {"content": resp}}]}

        def _extract_text(self, payload):
            try:
                return payload["choices"][0]["message"]["content"]
            except Exception:
                return ""

        def _extract_usage(self, payload):
            return {"total_tokens": 50}

    return FakeEngine()


# ---------------------------------------------------------------------------
# Tests: _parse_llm_output
# ---------------------------------------------------------------------------

class TestParseOutput:
    def test_final_answer(self):
        text = "THOUGHT: I have enough info.\nFINAL_ANSWER: Here is your summary."
        thought, final, action = _parse_llm_output(text)
        assert thought == "I have enough info."
        assert final == "Here is your summary."
        assert action is None

    def test_action_json(self):
        action_json = (
            '{"contract_version": "1.0", "action_type": "integration", '
            '"action": "list_orders", "integration": "shopify", '
            '"arguments": {}, "rationale": "get orders"}'
        )
        text = f"THOUGHT: Let me get the orders.\nACTION: {action_json}"
        thought, final, action = _parse_llm_output(text)
        assert thought == "Let me get the orders."
        assert final is None
        assert action is not None
        assert action["action"] == "list_orders"

    def test_final_answer_multiline(self):
        text = "THOUGHT: Done.\nFINAL_ANSWER: Line1\nLine2\nLine3"
        thought, final, action = _parse_llm_output(text)
        assert final is not None
        assert "Line1" in final
        assert "Line3" in final

    def test_no_thought_prefix_fallback(self):
        text = "Let me think. Something something.\nFINAL_ANSWER: result"
        thought, final, action = _parse_llm_output(text)
        assert final == "result"

    def test_case_insensitive_final_answer(self):
        text = "thought: thinking.\nfinal_answer: done"
        thought, final, action = _parse_llm_output(text)
        assert final == "done"

    def test_case_insensitive_action(self):
        action_json = (
            '{"contract_version": "1.0", "action_type": "integration", '
            '"action": "ping", "integration": "slack", '
            '"arguments": {}, "rationale": "test"}'
        )
        text = f"thought: do it.\naction: {action_json}"
        thought, final, action = _parse_llm_output(text)
        assert action is not None
        assert action["action"] == "ping"

    def test_invalid_json_action_returns_none(self):
        text = "THOUGHT: Try.\nACTION: not-json"
        thought, final, action = _parse_llm_output(text)
        assert action is None
        assert final is None

    def test_empty_text(self):
        thought, final, action = _parse_llm_output("")
        assert final is None
        assert action is None


# ---------------------------------------------------------------------------
# Tests: _is_sandbox_eligible
# ---------------------------------------------------------------------------

class TestSandboxEligible:
    def test_connector_and_then(self):
        assert _core_module.CognitiveEngine._is_sandbox_eligible(
            None,  # unbound call — use None for self, heuristic doesn't use self
            "fetch orders and then send to slack",
        )

    def test_spanish_connector_luego(self):
        # "y luego" is in _SANDBOX_CONNECTORS
        assert _core_module.CognitiveEngine._is_sandbox_eligible(
            None, "revisa órdenes y luego manda resumen"
        )

    def test_two_action_verbs(self):
        # "busca" + "manda" → 2 verbs
        assert _core_module.CognitiveEngine._is_sandbox_eligible(
            None, "busca clientes y manda correo"
        )

    def test_single_verb_not_eligible(self):
        assert not _core_module.CognitiveEngine._is_sandbox_eligible(
            None, "¿cuál es el clima hoy?"
        )

    def test_simple_question_not_eligible(self):
        assert not _core_module.CognitiveEngine._is_sandbox_eligible(
            None, "hola, cómo estás"
        )

    def test_case_insensitive(self):
        assert _core_module.CognitiveEngine._is_sandbox_eligible(
            None, "FETCH orders AND THEN notify slack"
        )


# ---------------------------------------------------------------------------
# Tests: LLMSandbox.run() — FINAL_ANSWER path
# ---------------------------------------------------------------------------

class TestLLMSandboxFinalAnswer:
    def test_terminates_on_final_answer(self):
        engine = _make_engine(["THOUGHT: Done.\nFINAL_ANSWER: All good."])
        sandbox = LLMSandbox(engine=engine)

        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(goal="test goal", client_id="c1", session_id="s1")
        )

        assert result.final_answer == "All good."
        assert result.stopped_reason == "final_answer"
        assert result.iterations == 1
        assert len(result.steps) == 1
        assert result.steps[0].is_final

    def test_final_answer_carries_goal(self):
        engine = _make_engine(["THOUGHT: t.\nFINAL_ANSWER: f."])
        sandbox = LLMSandbox(engine=engine)
        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(goal="my goal", client_id="c1", session_id="s1")
        )
        assert result.goal == "my goal"

    def test_client_and_session_in_result(self):
        engine = _make_engine(["THOUGHT: t.\nFINAL_ANSWER: f."])
        sandbox = LLMSandbox(engine=engine)
        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(goal="g", client_id="client-xyz", session_id="sess-abc")
        )
        assert result.client_id == "client-xyz"
        assert result.session_id == "sess-abc"

    def test_tokens_accumulated(self):
        engine = _make_engine([
            "THOUGHT: step 1.\nACTION: not-json",  # observation step
            "THOUGHT: done.\nFINAL_ANSWER: ok",
        ])
        sandbox = LLMSandbox(engine=engine)
        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(goal="g", client_id="c", session_id="s", max_iterations=5)
        )
        # 2 iterations × 50 tokens each
        assert result.total_tokens == 100


# ---------------------------------------------------------------------------
# Tests: LLMSandbox.run() — max_iterations stop
# ---------------------------------------------------------------------------

class TestLLMSandboxMaxIterations:
    def test_stops_at_max_iterations(self):
        # Always returns ACTION (never FINAL_ANSWER) — should stop at max
        engine = _make_engine([])  # empty → fallback returns FINAL_ANSWER but only after max
        engine._responses = ["THOUGHT: t.\nACTION: not-json"] * 20

        sandbox = LLMSandbox(engine=engine)
        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(
                goal="loop forever",
                client_id="c",
                session_id="s",
                max_iterations=3,
                auto_commit=False,
            )
        )
        assert result.iterations == 3
        assert result.stopped_reason == "max_iterations"
        assert result.pending_commit is True

    def test_pending_commit_set_when_stopped_early(self):
        engine = _make_engine(["THOUGHT: t.\nACTION: not-json"] * 5)
        sandbox = LLMSandbox(engine=engine)
        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(goal="g", client_id="c", session_id="s", max_iterations=2, auto_commit=False)
        )
        assert result.pending_commit is True
        assert not result.committed

    def test_stops_after_three_repeated_failures_for_same_signature(self):
        action_json = (
            '{"contract_version": "1.0", "action_type": "http", '
            '"action": "GET /v1/orders", "integration": "erp_api", '
            '"arguments": {"url": "https://erp.example.com/v1/orders"}, '
            '"rationale": "retry fetch"}'
        )
        engine = _make_engine([f"THOUGHT: retry.\nACTION: {action_json}"] * 5)
        sandbox = LLMSandbox(engine=engine)

        async def _always_fail(*args, **kwargs):
            return ToolObservation(
                status="error",
                result_preview="[error] upstream 500",
                confidence=0.0,
                committed=False,
                raw_payload={"status": "error"},
            )

        with patch("brain.llm_sandbox.SandboxExecutor.execute", new=AsyncMock(side_effect=_always_fail)):
            result = asyncio.get_event_loop().run_until_complete(
                sandbox.run(
                    goal="fetch orders",
                    client_id="c",
                    session_id="s",
                    max_iterations=5,
                    auto_commit=False,
                )
            )

        assert result.stopped_reason == "repeated_failures"
        assert result.iterations == 3
        assert result.final_answer is not None
        assert "fallo 3 veces" in result.final_answer
        assert len(result.steps) == 4
        assert result.steps[-1].is_final is True


# ---------------------------------------------------------------------------
# Tests: LLMSandbox.run() — action observation loop
# ---------------------------------------------------------------------------

class TestLLMSandboxActionLoop:
    def test_action_then_final_answer(self):
        action_json = (
            '{"contract_version": "1.0", "action_type": "integration", '
            '"action": "list_orders", "integration": "shopify", '
            '"arguments": {}, "rationale": "get orders"}'
        )
        responses = [
            f"THOUGHT: Check orders.\nACTION: {action_json}",
            "THOUGHT: Got data.\nFINAL_ANSWER: 3 orders found.",
        ]
        engine = _make_engine(responses)
        sandbox = LLMSandbox(engine=engine)

        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(
                goal="list orders",
                client_id="c1",
                session_id="s1",
                max_iterations=5,
                auto_commit=False,
            )
        )

        assert result.final_answer == "3 orders found."
        assert result.iterations == 2
        # First step has an action, second is final
        assert result.steps[0].action is not None
        assert result.steps[0].action["action"] == "list_orders"
        assert result.steps[1].is_final

    def test_observation_populated(self):
        """Steps with actions should have observations from the executor."""
        action_json = (
            '{"contract_version": "1.0", "action_type": "integration", '
            '"action": "ping", "integration": "slack", '
            '"arguments": {}, "rationale": "ping"}'
        )
        responses = [
            f"THOUGHT: ping.\nACTION: {action_json}",
            "THOUGHT: ok.\nFINAL_ANSWER: done",
        ]
        engine = _make_engine(responses)
        sandbox = LLMSandbox(engine=engine)

        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(goal="g", client_id="c", session_id="s", max_iterations=5)
        )

        action_step = result.steps[0]
        assert action_step.observation is not None
        assert len(action_step.observation) > 0

    def test_first_prompt_includes_no_premature_final_answer_rule_with_actual_limit(self):
        captured = {}

        class CapturingEngine:
            def _resolve_model_name(self, _):
                return "test-model"

            def _build_request(self, *, system_prompt, history, message, model_name, temperature):
                captured["system_prompt"] = system_prompt
                return {"model": model_name, "messages": [{"role": "user", "content": message}]}

            async def _call_model(self, model_name, body):
                return {"choices": [{"message": {"content": "THOUGHT: done.\nFINAL_ANSWER: ok"}}]}

            def _extract_text(self, payload):
                return payload["choices"][0]["message"]["content"]

            def _extract_usage(self, payload):
                return {"total_tokens": 50}

        sandbox = LLMSandbox(engine=CapturingEngine())
        result = asyncio.get_event_loop().run_until_complete(
            sandbox.run(goal="g", client_id="c", session_id="s", max_iterations=7)
        )

        assert result.stopped_reason == "final_answer"
        prompt = captured["system_prompt"]
        assert "no des FINAL_ANSWER prematura" in prompt
        assert "7 iteraciones" in prompt


# ---------------------------------------------------------------------------
# Tests: SandboxExecutor
# ---------------------------------------------------------------------------

class TestSandboxExecutorDryRun:
    """Patch brain.sandbox_executor.send_to_n8n (where it's imported) not the source module."""

    def _make_action(self):
        from schemas.action_contract import ActionContract

        return ActionContract(
            contract_version="1.0",
            action_type="integration",
            action="list_orders",
            integration="shopify",
            arguments={},
            rationale="test",
        )

    def test_dry_run_sends_sandbox_flag(self):
        import brain.sandbox_executor as executor_mod

        sent_payloads = []

        async def fake_send(payload):
            sent_payloads.append(payload)
            return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

        original = executor_mod.send_to_n8n_with_response
        executor_mod.send_to_n8n_with_response = fake_send
        try:
            executor = SandboxExecutor(client_id="c", session_id="s")
            obs = asyncio.get_event_loop().run_until_complete(
                executor.execute(self._make_action(), dry_run=True)
            )
            assert len(sent_payloads) == 1
            assert sent_payloads[0]["dry_run"] is True
            assert sent_payloads[0]["sandbox_mode"] is True
            assert obs.committed is False
            assert obs.status == "ok"
        finally:
            executor_mod.send_to_n8n_with_response = original

    def test_commit_sends_no_sandbox_flag(self):
        import brain.sandbox_executor as executor_mod

        sent_payloads = []

        async def fake_send(payload):
            sent_payloads.append(payload)
            return {"dispatched": True, "pending": False, "confirmed": True, "execution_status": "success"}

        original = executor_mod.send_to_n8n_with_response
        executor_mod.send_to_n8n_with_response = fake_send
        try:
            executor = SandboxExecutor(client_id="c", session_id="s")
            obs = asyncio.get_event_loop().run_until_complete(
                executor.execute(self._make_action(), dry_run=False)
            )
            assert len(sent_payloads) == 1
            assert sent_payloads[0]["dry_run"] is False
            assert sent_payloads[0]["sandbox_mode"] is False
            assert obs.committed is True
        finally:
            executor_mod.send_to_n8n_with_response = original

    def test_n8n_unavailable_dry_run_returns_mock(self):
        import brain.sandbox_executor as executor_mod

        async def fake_send(_):
            return {"dispatched": False, "pending": False, "confirmed": False, "execution_status": "not_dispatched"}

        original = executor_mod.send_to_n8n_with_response
        executor_mod.send_to_n8n_with_response = fake_send
        try:
            executor = SandboxExecutor(client_id="c", session_id="s")
            obs = asyncio.get_event_loop().run_until_complete(
                executor.execute(self._make_action(), dry_run=True)
            )
            assert obs.status == "ok"
            assert "mock" in obs.result_preview.lower()
            assert obs.confidence == 0.5
        finally:
            executor_mod.send_to_n8n_with_response = original

    def test_n8n_unavailable_commit_returns_error(self):
        import brain.sandbox_executor as executor_mod

        async def fake_send(_):
            return {"dispatched": False, "pending": False, "confirmed": False, "execution_status": "not_dispatched"}

        original = executor_mod.send_to_n8n_with_response
        executor_mod.send_to_n8n_with_response = fake_send
        try:
            executor = SandboxExecutor(client_id="c", session_id="s")
            obs = asyncio.get_event_loop().run_until_complete(
                executor.execute(self._make_action(), dry_run=False)
            )
            assert obs.status == "error"
            assert obs.committed is False
        finally:
            executor_mod.send_to_n8n_with_response = original

    def test_duration_ms_set(self):
        import brain.sandbox_executor as executor_mod

        async def fake_send(_):
            return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

        original = executor_mod.send_to_n8n_with_response
        executor_mod.send_to_n8n_with_response = fake_send
        try:
            executor = SandboxExecutor(client_id="c", session_id="s")
            obs = asyncio.get_event_loop().run_until_complete(
                executor.execute(self._make_action(), dry_run=True)
            )
            assert obs.duration_ms >= 0
        finally:
            executor_mod.send_to_n8n_with_response = original


class TestSandboxExecutorCommitAll:
    def _make_step(self, action_dict, is_final=False):
        return SandboxStep(
            step_index=0,
            thought="t",
            action=action_dict,
            is_final=is_final,
            observation_confidence=0.9,
        )

    def _valid_action_dict(self):
        return {
            "contract_version": "1.0",
            "action_type": "integration",
            "action": "list_orders",
            "integration": "shopify",
            "arguments": {},
            "rationale": "test",
        }

    def test_commit_all_dispatches_each_action(self):
        import brain.sandbox_executor as executor_mod

        sent = []

        async def fake_send(payload):
            sent.append(payload)
            return {"dispatched": True, "pending": False, "confirmed": True, "execution_status": "success"}

        original = executor_mod.send_to_n8n_with_response
        executor_mod.send_to_n8n_with_response = fake_send
        try:
            executor = SandboxExecutor(client_id="c", session_id="s")
            steps = [
                self._make_step(self._valid_action_dict()),
                self._make_step(self._valid_action_dict()),
                self._make_step(None),           # no action — should be skipped
                self._make_step(self._valid_action_dict(), is_final=True),  # final — skip
            ]
            count = asyncio.get_event_loop().run_until_complete(
                executor.commit_all(steps)
            )
            assert count == 2   # 2 non-final action steps
            assert len(sent) == 2
        finally:
            executor_mod.send_to_n8n_with_response = original

    def test_commit_all_returns_zero_on_failure(self):
        import brain.sandbox_executor as executor_mod

        async def fake_send(_):
            return {"dispatched": False, "pending": False, "confirmed": False, "execution_status": "not_dispatched"}

        original = executor_mod.send_to_n8n_with_response
        executor_mod.send_to_n8n_with_response = fake_send
        try:
            executor = SandboxExecutor(client_id="c", session_id="s")
            steps = [self._make_step(self._valid_action_dict())]
            count = asyncio.get_event_loop().run_until_complete(
                executor.commit_all(steps)
            )
            assert count == 0
        finally:
            executor_mod.send_to_n8n_with_response = original


# ---------------------------------------------------------------------------
# Tests: schemas/sandbox.py
# ---------------------------------------------------------------------------

class TestSandboxSchemas:
    def test_sandbox_step_defaults(self):
        step = SandboxStep(step_index=0, thought="hello")
        assert step.action is None
        assert step.observation is None
        assert step.is_final is False
        assert step.observation_confidence == 1.0

    def test_sandbox_step_confidence_clamped_by_pydantic(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SandboxStep(step_index=0, thought="t", observation_confidence=1.5)

    def test_sandbox_result_defaults(self):
        result = SandboxResult(goal="test goal")
        assert result.committed is False
        assert result.pending_commit is False
        assert result.iterations == 0
        assert result.stopped_reason == "final_answer"
        assert result.run_id  # auto-generated

    def test_sandbox_result_run_id_unique(self):
        r1 = SandboxResult(goal="g1")
        r2 = SandboxResult(goal="g2")
        assert r1.run_id != r2.run_id

    def test_sandbox_result_serializes(self):
        step = SandboxStep(step_index=0, thought="t", is_final=True)
        result = SandboxResult(
            goal="g",
            steps=[step],
            final_answer="done",
            iterations=1,
            stopped_reason="final_answer",
        )
        data = result.model_dump()
        assert data["goal"] == "g"
        assert data["final_answer"] == "done"
        assert len(data["steps"]) == 1
        assert data["steps"][0]["thought"] == "t"


# ---------------------------------------------------------------------------
# Tests: _sandbox_result_to_response (brain/core.py)
# ---------------------------------------------------------------------------

class TestSandboxResultToResponse:
    def _make_result(self, final_answer=None, stopped_reason="final_answer", iterations=2):
        return SandboxResult(
            goal="g",
            final_answer=final_answer,
            stopped_reason=stopped_reason,
            iterations=iterations,
            run_id="test-run-id",
        )

    def test_returns_final_answer_as_content(self):
        import brain.core as core_mod
        engine = core_mod.CognitiveEngine.__new__(core_mod.CognitiveEngine)
        result = self._make_result(final_answer="Here is the answer")
        resp = engine._sandbox_result_to_response(result)
        assert resp["type"] == "message"
        assert resp["content"] == "Here is the answer"

    def test_fallback_content_when_no_final_answer(self):
        import brain.core as core_mod
        engine = core_mod.CognitiveEngine.__new__(core_mod.CognitiveEngine)
        result = self._make_result(final_answer=None, stopped_reason="max_iterations")
        resp = engine._sandbox_result_to_response(result)
        assert resp["type"] == "message"
        assert "max_iterations" in resp["content"]

    def test_metadata_keys_present(self):
        import brain.core as core_mod
        engine = core_mod.CognitiveEngine.__new__(core_mod.CognitiveEngine)
        result = self._make_result(final_answer="ok")
        resp = engine._sandbox_result_to_response(result)
        assert "sandbox_run_id" in resp
        assert resp["sandbox_run_id"] == "test-run-id"
        assert "sandbox_committed" in resp
        assert "sandbox_pending_commit" in resp
        assert "sandbox_iterations" in resp
