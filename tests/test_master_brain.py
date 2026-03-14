import asyncio
import pathlib
import sys
import uuid
from unittest.mock import AsyncMock

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
project_root_text = str(PROJECT_ROOT)
if project_root_text not in sys.path:
    sys.path.insert(0, project_root_text)

from brain.master_brain import MasterBrain


def _minimal_plan() -> list[dict]:
    return [
        {
            "id": "step-1",
            "title": "Generar prospectos",
            "tool": "auto",
            "action_type": "analysis",
            "action": "execute_goal",
            "args": {"goal": "Buscar prospectos"},
            "risk": "low",
            "success_criteria": "Prospectos listados",
        }
    ]


def _critical_plan() -> list[dict]:
    return [
        {
            "id": "step-1",
            "title": "Eliminar registro",
            "tool": "auto",
            "action_type": "analysis",
            "action": "delete_record",
            "args": {"goal": "Eliminar pedido 1001"},
            "risk": "critical",
            "success_criteria": "Registro eliminado",
        }
    ]


def test_master_brain_low_returns_strategy_without_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTONOMY_LEVEL", "low")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_LEARNING", "false")

    brain = MasterBrain(client_id=str(uuid.uuid4()))
    monkeypatch.setattr(brain, "_load_context", AsyncMock(return_value={"intent": "business"}))
    monkeypatch.setattr(
        brain,
        "_generate_strategy",
        AsyncMock(return_value={"mission_steps": [{"title": "Paso 1"}]}),
    )
    monkeypatch.setattr(brain, "_build_execution_plan", lambda _s, _c: _minimal_plan())
    monkeypatch.setattr(
        brain,
        "_choose_tools",
        AsyncMock(return_value={"selected_tool": "api", "routes": {"step-1": "n8n"}}),
    )
    exec_mock = AsyncMock(return_value=[{"status": "completed"}])
    monkeypatch.setattr(brain, "_execute_plan", exec_mock)

    result = asyncio.run(brain.run("búscame clientes"))

    assert result["status"] == "dry_run"
    assert result["needs_confirmation"] is False
    assert exec_mock.await_count == 0
    assert isinstance(result["data"].get("execution_plan"), list)


def test_master_brain_medium_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTONOMY_LEVEL", "medium")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_LEARNING", "false")

    brain = MasterBrain(client_id=str(uuid.uuid4()))
    monkeypatch.setattr(brain, "_load_context", AsyncMock(return_value={"intent": "business"}))
    monkeypatch.setattr(
        brain,
        "_generate_strategy",
        AsyncMock(return_value={"mission_steps": [{"title": "Paso 1"}]}),
    )
    monkeypatch.setattr(brain, "_build_execution_plan", lambda _s, _c: _minimal_plan())
    monkeypatch.setattr(
        brain,
        "_choose_tools",
        AsyncMock(return_value={"selected_tool": "api", "routes": {"step-1": "n8n"}}),
    )
    exec_mock = AsyncMock(return_value=[{"status": "completed"}])
    monkeypatch.setattr(brain, "_execute_plan", exec_mock)

    result = asyncio.run(brain.run("haz marketing"))

    assert result["status"] == "blocked"
    assert result["needs_confirmation"] is True
    assert "requiere confirmación" in result["summary"].lower()
    assert exec_mock.await_count == 0


def test_master_brain_high_executes_without_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTONOMY_LEVEL", "high")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_LEARNING", "false")

    brain = MasterBrain(client_id=str(uuid.uuid4()))
    monkeypatch.setattr(brain, "_load_context", AsyncMock(return_value={"intent": "business"}))
    monkeypatch.setattr(
        brain,
        "_generate_strategy",
        AsyncMock(return_value={"mission_steps": [{"title": "Paso 1"}]}),
    )
    monkeypatch.setattr(brain, "_build_execution_plan", lambda _s, _c: _minimal_plan())
    monkeypatch.setattr(
        brain,
        "_choose_tools",
        AsyncMock(return_value={"selected_tool": "api", "routes": {"step-1": "n8n"}}),
    )
    monkeypatch.setattr(
        brain,
        "_execute_plan",
        AsyncMock(
            return_value=[
                {
                    "step_id": "step-1",
                    "executor": "n8n",
                    "status": "completed",
                    "summary": "Prospectos listos",
                    "error": "",
                    "data": {"count": 5},
                    "duration_ms": 12,
                    "risk": "low",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        brain,
        "_learn",
        AsyncMock(return_value={"enabled": True, "refreshed_patterns": 1}),
    )

    result = asyncio.run(brain.run("búscame clientes"))

    assert result["status"] == "completed"
    assert result["needs_confirmation"] is False
    assert result["data"]["evaluation"]["completed_steps"] == 1
    assert result["data"]["risk_bypassed"] is True


def test_master_brain_high_executes_critical_steps_without_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTONOMY_LEVEL", "high")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_LEARNING", "false")

    brain = MasterBrain(client_id=str(uuid.uuid4()))
    monkeypatch.setattr(brain, "_load_context", AsyncMock(return_value={"intent": "business"}))
    monkeypatch.setattr(
        brain,
        "_generate_strategy",
        AsyncMock(return_value={"mission_steps": [{"title": "Paso 1"}]}),
    )
    monkeypatch.setattr(brain, "_build_execution_plan", lambda _s, _c: _critical_plan())
    monkeypatch.setattr(
        brain,
        "_choose_tools",
        AsyncMock(return_value={"selected_tool": "api", "routes": {"step-1": "n8n"}}),
    )
    exec_mock = AsyncMock(
        return_value=[
            {
                "step_id": "step-1",
                "executor": "n8n",
                "status": "completed",
                "summary": "Registro eliminado",
                "error": "",
                "data": {"deleted": True},
                "duration_ms": 9,
                "risk": "critical",
            }
        ]
    )
    monkeypatch.setattr(brain, "_execute_plan", exec_mock)
    monkeypatch.setattr(brain, "_learn", AsyncMock(return_value={"enabled": True}))

    result = asyncio.run(brain.run("elimina pedido 1001"))

    assert exec_mock.await_count == 1
    assert result["status"] == "completed"
    assert result["needs_confirmation"] is False
    assert result["data"]["risk_bypassed"] is True


def test_master_brain_execute_web_agent_uses_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASTER_BRAIN_WEB_MAX_STEPS", "5")
    monkeypatch.setenv("MASTER_BRAIN_WEB_TIMEOUT_SEC", "7")

    created = {"called": False}

    class _FakeWebAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def run(self, goal: str, start_url: str | None = None):
            created["called"] = True
            return {
                "success": True,
                "summary": f"ok: {goal}",
                "data": {"start_url": start_url or ""},
            }

    monkeypatch.setattr("browser_agent.web_agent_loop.WebAgentLoop", _FakeWebAgentLoop)
    brain = MasterBrain(client_id=str(uuid.uuid4()))
    result = asyncio.run(brain._execute_web_agent("abre https://example.com y busca pricing"))

    assert created["called"] is True
    assert result["status"] == "completed"
    assert result["data"]["start_url"].startswith("https://example.com")


def test_master_brain_execute_n8n_returns_pending_until_execution_is_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.n8n_client as n8n_client

    async def fake_send(_payload: dict) -> dict:
        return {
            "dispatched": True,
            "pending": True,
            "confirmed": False,
            "execution_id": None,
            "execution_status": "accepted",
            "response_excerpt": "",
            "webhook_url": "https://n8n.example.com/webhook/test",
        }

    monkeypatch.setattr(n8n_client, "send_to_n8n_with_response", fake_send)
    brain = MasterBrain(client_id=str(uuid.uuid4()))

    result = asyncio.run(brain._execute_n8n({"title": "Paso 1"}))

    assert result["status"] == "pending"
    assert result["data"]["pending"] is True
    assert result["data"]["execution_status"] == "accepted"


def test_master_brain_chat_intent_skips_web_research_and_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTONOMY_LEVEL", "high")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_INTENT_LLM", "false")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_LEARNING", "false")

    brain = MasterBrain(client_id=str(uuid.uuid4()))
    quick_research_mock = AsyncMock(return_value=["should-not-happen"])
    execute_mock = AsyncMock(return_value=[{"status": "completed"}])

    monkeypatch.setattr(brain, "_quick_web_research", quick_research_mock)
    monkeypatch.setattr(brain, "_execute_plan", execute_mock)

    result = asyncio.run(brain.run("Entendido que boludo"))

    assert result["status"] == "completed"
    assert result["iterations"] == 0
    assert result["data"]["intent"] == "chat"
    assert quick_research_mock.await_count == 0
    assert execute_mock.await_count == 0


def test_master_brain_allows_discovery_for_api_integration_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_INTENT_LLM", "false")
    brain = MasterBrain(client_id=str(uuid.uuid4()))

    context = {"intent": "api", "goal": "Integra la API de X"}
    normalized = brain._normalize_action_type(
        "discover_apis",
        {"url": "https://x.com/openapi.json"},
        context,
    )

    assert normalized == "discover_apis"


# ---------------------------------------------------------------------------
# Intent gate tests (Phase 2-A)
# ---------------------------------------------------------------------------

def test_intent_heuristic_capability_question_returns_chat() -> None:
    """'¿Qué puedes hacer?' and similar must be chat even though 'hacer' is an action verb."""
    brain = MasterBrain(client_id=str(uuid.uuid4()))
    assert brain._infer_intent_heuristic("Que puedes hacer entonces") == "chat"
    assert brain._infer_intent_heuristic("Cómo puedes ayudarme") == "chat"
    assert brain._infer_intent_heuristic("qué sabes hacer") == "chat"
    assert brain._infer_intent_heuristic("para que sirves") == "chat"


def test_intent_heuristic_hola_returns_chat() -> None:
    brain = MasterBrain(client_id=str(uuid.uuid4()))
    assert brain._infer_intent_heuristic("Hola") == "chat"


def test_intent_heuristic_ok_returns_chat() -> None:
    brain = MasterBrain(client_id=str(uuid.uuid4()))
    assert brain._infer_intent_heuristic("Ok") == "chat"


def test_intent_heuristic_consigue_clientes_returns_business() -> None:
    brain = MasterBrain(client_id=str(uuid.uuid4()))
    # "clientes" is a _BUSINESS_HINTS token — should classify as business even without action verb
    assert brain._infer_intent_heuristic("Consigue clientes") == "business"


def test_intent_heuristic_business_analysis_alone_does_not_classify_greeting_as_business() -> None:
    """business_analysis context must NOT promote 'Hola' to business intent."""
    brain = MasterBrain(client_id=str(uuid.uuid4()))
    context = {"business_analysis": {"insights": [{"reason": "grow revenue"}]}}
    result = brain._infer_intent_heuristic("Hola", context)
    assert result == "chat"


def test_intent_heuristic_business_analysis_with_action_verb_returns_business() -> None:
    """When business_analysis exists AND message has an action verb, business is correct."""
    brain = MasterBrain(client_id=str(uuid.uuid4()))
    context = {"business_analysis": {"insights": [{"reason": "automate ops"}]}}
    result = brain._infer_intent_heuristic("genera un reporte", context)
    assert result == "business"


# ---------------------------------------------------------------------------
# Desktop fast-path tests (Phase 2-B)
# ---------------------------------------------------------------------------

def test_desktop_fast_path_skips_strict_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Abre Google' — desktop fast-path must NOT call _generate_strategy."""
    monkeypatch.setenv("AUTONOMY_LEVEL", "high")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_LEARNING", "false")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_INTENT_LLM", "false")

    brain = MasterBrain(client_id=str(uuid.uuid4()))

    monkeypatch.setattr(
        brain,
        "_load_context",
        AsyncMock(return_value={"intent": "desktop", "business_analysis": {}}),
    )
    monkeypatch.setattr(
        brain,
        "_infer_intent",
        AsyncMock(return_value="desktop"),
    )
    generate_strategy_mock = AsyncMock(return_value={})
    monkeypatch.setattr(brain, "_generate_strategy", generate_strategy_mock)
    monkeypatch.setattr(
        brain,
        "_execute_desktop_loop",
        AsyncMock(return_value={"status": "completed", "summary": "Chrome abierto.", "error": "", "data": {}}),
    )
    monkeypatch.setattr(brain, "_learn", AsyncMock(return_value={"enabled": False}))

    result = asyncio.run(brain.run("Abre Google"))

    assert result["status"] == "completed"
    assert result["runtime"] == "desktop_autonomy"
    assert generate_strategy_mock.await_count == 0, "_generate_strategy should NOT be called for simple desktop goals"


def test_desktop_non_simple_goal_does_not_use_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long desktop goal falls through to normal strategy path."""
    monkeypatch.setenv("AUTONOMY_LEVEL", "high")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_LEARNING", "false")
    monkeypatch.setenv("MASTER_BRAIN_ENABLE_INTENT_LLM", "false")

    brain = MasterBrain(client_id=str(uuid.uuid4()))

    monkeypatch.setattr(
        brain,
        "_load_context",
        AsyncMock(return_value={"intent": "desktop", "business_analysis": {}}),
    )
    monkeypatch.setattr(brain, "_infer_intent", AsyncMock(return_value="desktop"))
    generate_strategy_mock = AsyncMock(
        return_value={"mission_steps": [], "strict_actions": [], "objectives": []}
    )
    monkeypatch.setattr(brain, "_generate_strategy", generate_strategy_mock)
    monkeypatch.setattr(brain, "_build_execution_plan", lambda _s, _c: [])
    monkeypatch.setattr(
        brain,
        "_choose_tools",
        AsyncMock(return_value={"selected_tool": "desktop", "routes": {}}),
    )
    monkeypatch.setattr(brain, "_execute_plan", AsyncMock(return_value=[]))
    monkeypatch.setattr(brain, "_learn", AsyncMock(return_value={"enabled": False}))

    result = asyncio.run(
        brain.run("Abre Chrome, navega a Google Calendar, crea un evento para el jueves a las 10am")
    )

    assert generate_strategy_mock.await_count == 1, "_generate_strategy SHOULD be called for complex goals"
