from __future__ import annotations

from typing import Any, Dict, List


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def evaluate_handoff(*, agent_name: str, handoff: Dict[str, Any]) -> Dict[str, Any]:
    risk_score = _as_float(handoff.get("risk_score"))
    context = dict(handoff.get("context") or {})
    allowed_tools = list(handoff.get("allowed_tools") or [])
    flags: List[str] = []
    notes: List[str] = []
    mode = "standard"
    permitted = True
    hold_reason = None

    if risk_score >= 0.9:
        mode = "strict"
        flags.append("high_risk_case")
        notes.append("Caso de alto riesgo; exigir acciones conservadoras y pocas herramientas.")
    elif risk_score >= 0.7:
        mode = "guarded"
        flags.append("guarded_case")

    if len(allowed_tools) > 3:
        flags.append("tool_sprawl")
        notes.append("Demasiadas herramientas para una sola ejecucion; reducir superficie operativa.")

    if len(context) > 16:
        flags.append("overscoped_context")
        notes.append("Contexto demasiado amplio; el agente debe trabajar solo con lo esencial.")

    if context.get("rollback_metadata") and "run_rollback" not in set(allowed_tools):
        permitted = False
        hold_reason = "rollback_tool_missing"
        flags.append("missing_rollback_capability")
        notes.append("Se detecto compensacion potencial sin herramienta de rollback disponible.")

    return {
        "permitted": permitted,
        "mode": mode,
        "flags": flags,
        "notes": notes,
        "hold_reason": hold_reason,
        "agent_name": agent_name,
    }


def evaluate_result(
    *,
    agent_name: str,
    handoff: Dict[str, Any],
    agent_result: Dict[str, Any],
) -> Dict[str, Any]:
    flags: List[str] = []
    notes: List[str] = []
    status = str(agent_result.get("status") or "waiting").strip().lower()
    actions_taken = [str(item).strip().lower() for item in (agent_result.get("actions_taken") or []) if str(item).strip()]
    compensation_attempted = bool(agent_result.get("compensation_attempted"))
    selected_tool = str(agent_result.get("selected_tool") or "").strip().lower()

    if actions_taken:
        unique_actions = set(actions_taken)
        if len(actions_taken) >= 3 and len(unique_actions) == 1:
            flags.append("loop_suspected")
            notes.append("El agente repitio la misma accion varias veces; conviene escalar.")

    if status == "completed" and not selected_tool and not compensation_attempted:
        flags.append("missing_execution_trace")
        notes.append("El agente marco completado sin dejar herramienta seleccionada.")

    if compensation_attempted and status not in {"completed", "failed"}:
        flags.append("compensation_state_inconsistent")
        notes.append("Compensacion intentada con estado final no concluyente.")

    force_escalate = "loop_suspected" in flags or "missing_execution_trace" in flags
    return {
        "flags": flags,
        "notes": notes,
        "force_escalate": force_escalate,
        "agent_name": agent_name,
        "case_id": handoff.get("case_id"),
    }
