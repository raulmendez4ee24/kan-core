from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from brain.core import CognitiveEngine
from brain.desktop_autonomous_loop import run_desktop_autonomy_loop
from brain.runtime_events import runtime_event_bus
from brain.specialist_memory import specialist_memory
from schemas.desktop_autonomy import DesktopAutonomyRunRequest

logger = logging.getLogger("kan_core.multi_agent_runtime")


@dataclass
class AgentHandoff:
    from_agent: str
    to_agent: str
    payload: Dict[str, Any]


def _planner_stage(goal: str) -> Dict[str, Any]:
    return {
        "goal": goal,
        "subgoals": [
            f"Research and validate constraints for: {goal}",
            f"Execute user-facing workflow for: {goal}",
            f"Verify output quality and report evidence for: {goal}",
        ],
    }


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    candidate = (text or "").strip()
    if not candidate:
        return None
    if candidate.startswith("```"):
        candidate = candidate.strip("`\n ")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    blob = candidate[start : end + 1]
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _specialist_schema(specialist: str) -> str:
    if specialist == "researcher":
        return (
            '{"summary":"string","confidence":0.0,"evidence_queries":["string"],'
            '"constraints":["string"],"success_criteria":["string"]}'
        )
    if specialist == "operator":
        return (
            '{"summary":"string","confidence":0.0,'
            '"execution_intents":{"start_url":"string|null","menu_path":["string"],'
            '"fill_steps":[{"selector":"string","text":"string"}],"click_text":"string|null"},'
            '"fallback_paths":["string"]}'
        )
    return (
        '{"summary":"string","confidence":0.0,"quality_threshold":0.0,'
        '"risk_notes":["string"],"blocker_checks":["string"]}'
    )


def _fallback_specialist(specialist: str, goal: str, context: Dict[str, Any], error: str) -> Dict[str, Any]:
    shared = {
        "agent": specialist,
        "goal": goal,
        "error": error,
        "source": "fallback",
        "confidence": 0.45,
    }
    if specialist == "researcher":
        return {
            **shared,
            "summary": "Fallback research generated baseline constraints.",
            "evidence_queries": [
                goal,
                f"{goal} official documentation",
                f"{goal} troubleshooting examples",
            ],
            "constraints": ["validate_allowlists", "prefer_verified_sources"],
            "success_criteria": ["quality_gate_passed", "no_unhandled_failures"],
            "context": context,
        }
    if specialist == "operator":
        return {
            **shared,
            "summary": "Fallback operator prepared minimal execution intents.",
            "execution_intents": {
                "start_url": context.get("start_url"),
                "menu_path": list(context.get("menu_path") or []),
                "fill_steps": list(context.get("fill_steps") or []),
                "click_text": context.get("click_text"),
            },
            "fallback_paths": ["reduce_scope", "retry_with_balanced_mode"],
            "context": context,
        }
    return {
        **shared,
        "summary": "Fallback critic requires conservative execution.",
        "quality_threshold": 0.72,
        "risk_notes": ["fallback_mode_enabled"],
        "blocker_checks": [],
        "context": context,
    }


def _validate_specialist_payload(specialist: str, payload: Dict[str, Any]) -> bool:
    if specialist == "researcher":
        return isinstance(payload.get("evidence_queries"), list) and isinstance(
            payload.get("constraints"), list
        )
    if specialist == "operator":
        intents = payload.get("execution_intents")
        return isinstance(intents, dict) and isinstance(payload.get("fallback_paths"), list)
    return isinstance(payload.get("risk_notes"), list) and isinstance(
        payload.get("blocker_checks"), list
    )


def _specialist_prompt(
    specialist: str,
    goal: str,
    context: Dict[str, Any],
    memory_summary: Dict[str, Any],
    memory_recall: Dict[str, List[Dict[str, Any]]],
) -> str:
    schema = _specialist_schema(specialist)
    context_json = json.dumps(context, ensure_ascii=True)
    memory_json = json.dumps(memory_summary, ensure_ascii=True)
    recall_json = json.dumps(memory_recall, ensure_ascii=True)
    return (
        f"You are {specialist} specialist in autonomous execution. "
        "Return ONLY valid JSON, no markdown. "
        f"Schema: {schema}. "
        f"Goal: {goal}. Context: {context_json}. "
        f"Specialist memory summary: {memory_json}. "
        f"Specialist memory recall (episodic/semantic/task): {recall_json}."
    )


async def _run_specialist(
    *,
    specialist: str,
    client_id: str,
    goal: str,
    context: Dict[str, Any],
    session_suffix: str,
) -> Dict[str, Any]:
    logger.debug("multi_agent_runtime: specialist=%s starting goal=%s", specialist, goal[:80])
    memory_summary = specialist_memory.summary(client_id=client_id, specialist=specialist)
    memory_recall = specialist_memory.recall(
        client_id=client_id,
        specialist=specialist,
        goal=goal,
        max_items=4,
    )
    try:
        engine = CognitiveEngine.get_instance()
    except Exception as exc:
        return _fallback_specialist(specialist, goal, context, f"engine_unavailable: {exc}")

    try:
        raw = await engine.generate_response(
            client_id=client_id,
            session_id=f"multi_agent_{session_suffix}_{specialist}",
            message=_specialist_prompt(
                specialist,
                goal,
                context,
                memory_summary,
                memory_recall,
            ),
        )
        content = str(raw.get("content") or "")
        payload = _extract_json_object(content) or {}
        if not _validate_specialist_payload(specialist, payload):
            return _fallback_specialist(
                specialist,
                goal,
                context,
                "invalid_specialist_payload",
            )
        return {
            "agent": specialist,
            "goal": goal,
            "source": "llm",
            "summary": str(payload.get("summary") or ""),
            "confidence": float(payload.get("confidence") or 0.55),
            "memory_summary": memory_summary,
            "memory_recall": memory_recall,
            **payload,
        }
    except Exception as exc:
        return _fallback_specialist(specialist, goal, context, f"specialist_failed: {exc}")


async def _specialists_stage_parallel(
    *,
    client_id: str,
    plan: Dict[str, Any],
    context: Dict[str, Any],
    session_suffix: str,
) -> List[Dict[str, Any]]:
    """
    Run researcher and operator in PARALLEL, then collect for the critic.
    Previously these ran sequentially, which wasted time on independent tasks.
    """
    subgoals = list(plan.get("subgoals") or [])
    specialists = ["researcher", "operator", "qa_critic"]
    fallback_goal = str(plan.get("goal") or "Autonomous task")
    if not subgoals:
        subgoals = [fallback_goal, fallback_goal, fallback_goal]

    # Researcher and operator can run simultaneously; qa_critic depends on both.
    researcher_coro = _run_specialist(
        specialist="researcher",
        client_id=client_id,
        goal=subgoals[0] if len(subgoals) > 0 else fallback_goal,
        context=context,
        session_suffix=session_suffix,
    )
    operator_coro = _run_specialist(
        specialist="operator",
        client_id=client_id,
        goal=subgoals[1] if len(subgoals) > 1 else fallback_goal,
        context=context,
        session_suffix=session_suffix,
    )

    # asyncio.gather runs both concurrently.
    parallel_results = await asyncio.gather(
        researcher_coro,
        operator_coro,
        return_exceptions=True,
    )

    outputs: List[Dict[str, Any]] = []
    for i, result in enumerate(parallel_results):
        if isinstance(result, Exception):
            logger.warning(
                "multi_agent_runtime: specialist %s failed: %s", specialists[i], result
            )
            outputs.append(
                _fallback_specialist(
                    specialists[i],
                    subgoals[i] if i < len(subgoals) else fallback_goal,
                    context,
                    str(result),
                )
            )
        else:
            outputs.append(result)

    # qa_critic runs after both are done.
    critic_context = {**context, "parallel_results": outputs}
    qa_result = await _run_specialist(
        specialist="qa_critic",
        client_id=client_id,
        goal=subgoals[2] if len(subgoals) > 2 else fallback_goal,
        context=critic_context,
        session_suffix=session_suffix,
    )
    outputs.append(qa_result)
    return outputs


def _critic_stage(specialist_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    critical_notes: List[str] = []
    errors: List[str] = []
    quality_threshold = 0.72
    operator_confidence = 0.55
    for item in specialist_outputs:
        if "verify" in str(item.get("goal", "")).lower():
            critical_notes.append("enforce_quality_gate")
        if item.get("error"):
            errors.append(f"{item.get('agent')}: {item.get('error')}")
        if item.get("agent") == "operator":
            operator_confidence = float(item.get("confidence") or operator_confidence)
        if item.get("agent") == "qa_critic":
            quality_threshold = float(item.get("quality_threshold") or quality_threshold)
            blocker_checks = item.get("blocker_checks")
            if isinstance(blocker_checks, list):
                critical_notes.extend(str(x) for x in blocker_checks if str(x).strip())

    execution_mode = "autonomous"
    specialist_scores: Dict[str, float] = {}
    for item in specialist_outputs:
        specialist = str(item.get("agent") or "")
        conf = max(0.0, min(float(item.get("confidence") or 0.0), 1.0))
        has_error = bool(item.get("error"))
        score = conf if not has_error else max(0.0, conf - 0.35)
        if specialist:
            specialist_scores[specialist] = round(score, 4)

    return {
        "approved": True,
        "notes": sorted(set(critical_notes)),
        "errors": errors,
        "quality_threshold": max(0.5, min(quality_threshold, 0.95)),
        "operator_confidence": round(operator_confidence, 4),
        "specialist_scores": specialist_scores,
        "execution_mode": execution_mode,
    }


def _extract_executor_hints(
    specialists: List[Dict[str, Any]], context: Dict[str, Any]
) -> Tuple[Optional[str], Dict[str, Any]]:
    enriched_context: Dict[str, Any] = dict(context or {})
    start_url: Optional[str] = None
    for item in specialists:
        if item.get("agent") != "operator":
            continue
        intents = item.get("execution_intents")
        if not isinstance(intents, dict):
            continue
        url = intents.get("start_url")
        if isinstance(url, str) and url.strip():
            start_url = url.strip()
        menu_path = intents.get("menu_path")
        fill_steps = intents.get("fill_steps")
        click_text = intents.get("click_text")
        if isinstance(menu_path, list):
            enriched_context["menu_path"] = menu_path
        if isinstance(fill_steps, list):
            enriched_context["fill_steps"] = fill_steps
        if isinstance(click_text, str) and click_text.strip():
            enriched_context["click_text"] = click_text.strip()
    return start_url, enriched_context


async def run_multi_agent_handoff(
    *,
    client_id: str,
    goal: str,
    context: Dict[str, Any],
    max_steps: int,
    session: Any,
) -> Dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    run_id = f"multi_agent_{started_at.strftime('%Y%m%d%H%M%S%f')}"
    runtime_event_bus.emit(
        run_id=run_id,
        session_id=run_id,
        event_type="lifecycle.multi_agent_start",
        payload={"goal": goal},
    )
    plan = _planner_stage(goal)
    session_suffix = started_at.strftime("%Y%m%d%H%M%S")

    # Run researcher + operator in parallel, then qa_critic.
    specialists = await _specialists_stage_parallel(
        client_id=client_id,
        plan=plan,
        context=context,
        session_suffix=session_suffix,
    )
    critic = _critic_stage(specialists)
    runtime_event_bus.emit(
        run_id=run_id,
        session_id=run_id,
        event_type="lifecycle.multi_agent_specialists_done",
        payload={"critic_mode": critic.get("execution_mode"), "errors": critic.get("errors")},
    )
    start_url, enriched_context = _extract_executor_hints(specialists, context)

    handoffs: List[AgentHandoff] = [
        AgentHandoff(from_agent="planner", to_agent="researcher", payload=specialists[0]),
        AgentHandoff(from_agent="planner", to_agent="operator", payload=specialists[1]),  # parallel
        AgentHandoff(from_agent="researcher+operator", to_agent="qa_critic", payload=specialists[2]),
        AgentHandoff(from_agent="critic", to_agent="executor", payload=critic),
    ]

    logger.info(
        "multi_agent_runtime: specialists done (mode=%s errors=%s) — launching executor",
        critic["execution_mode"],
        critic["errors"],
    )

    request = DesktopAutonomyRunRequest(
        goal=goal,
        max_steps=max(1, min(max_steps, 30)),
        execution_mode=str(critic.get("execution_mode") or "autonomous"),
        start_url=start_url,
        context={
            **enriched_context,
            "handoff": {
                "planner": plan,
                "specialists": specialists,
                "critic": critic,
            },
        },
        dry_run=bool(context.get("dry_run", False)),
        wait_for_results=True,
        enable_recovery=True,
        enable_self_healing=True,
        quality_min_score=0.0,
        enforce_quality_gate=False,
        dynamic_risk_controls=False,
    )
    execution = await run_desktop_autonomy_loop(client_id=client_id, request=request, session=session)
    runtime_event_bus.emit(
        run_id=run_id,
        session_id=run_id,
        event_type="lifecycle.multi_agent_done",
        payload={"status": execution.status},
    )

    finished_at = datetime.now(timezone.utc)
    final_status = str(execution.status or "failed")
    for item in specialists:
        specialist = str(item.get("agent") or "")
        if not specialist:
            continue
        confidence = float(item.get("confidence") or 0.0)
        specialist_memory.record(
            client_id=client_id,
            specialist=specialist,
            goal=goal,
            status=("ok" if final_status == "completed" else final_status),
            confidence=confidence,
            metadata={
                "run_id": run_id,
                "execution_status": final_status,
                "source": str(item.get("source") or "unknown"),
                "task_action": (
                    str((item.get("execution_intents") or {}).get("click_text") or "")
                    if isinstance(item.get("execution_intents"), dict)
                    else ""
                ),
                "tags": [specialist, str(item.get("source") or "unknown")],
            },
        )
    return {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "parallel_execution": True,
        "event_run_id": run_id,
        "specialist_memory": {
            str(item.get("agent")): specialist_memory.summary(
                client_id=client_id, specialist=str(item.get("agent") or "")
            )
            for item in specialists
            if str(item.get("agent") or "").strip()
        },
        "handoffs": [
            {"from": item.from_agent, "to": item.to_agent, "payload": item.payload}
            for item in handoffs
        ],
        "execution": execution.model_dump(),
    }
