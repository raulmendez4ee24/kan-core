from typing import Any, Dict, List

from brain.tool_reasoner import choose_tool
from tools.n8n_client import send_to_n8n_with_response


_AGENT_TOOL_CATALOG: Dict[str, List[Dict[str, Any]]] = {
    "sales": [
        {"tool": "integrations.autopilot", "confidence": 0.86, "health": 0.78, "cost": 0.35, "compatibility": 0.9},
        {"tool": "crm.followups", "confidence": 0.75, "health": 0.9, "cost": 0.12, "compatibility": 0.82},
    ],
    "support": [
        {"tool": "chat.dispatch", "confidence": 0.83, "health": 0.86, "cost": 0.18, "compatibility": 0.86},
        {"tool": "alerts.engine", "confidence": 0.74, "health": 0.92, "cost": 0.08, "compatibility": 0.78},
    ],
    "operations": [
        {"tool": "optimizer.cycles", "confidence": 0.8, "health": 0.84, "cost": 0.21, "compatibility": 0.85},
        {"tool": "integrations.dashboard", "confidence": 0.7, "health": 0.96, "cost": 0.05, "compatibility": 0.72},
    ],
    "marketing": [
        {"tool": "email.automation", "confidence": 0.72, "health": 0.76, "cost": 0.22, "compatibility": 0.81},
    ],
    "finance": [
        {"tool": "roi.summary", "confidence": 0.78, "health": 0.9, "cost": 0.04, "compatibility": 0.8},
    ],
}


def _agent_strategy(agent: str, goal: str) -> tuple[str, str]:
    lowered = goal.lower()
    if agent == "sales":
        if "venta" in lowered or "sales" in lowered:
            return "create_growth_automation", "Maximizar conversion con CRM + seguimiento"
        return "improve_pipeline_hygiene", "Depurar y priorizar leads"
    if agent == "support":
        return "reduce_response_latency", "Reducir mensajes sin respuesta"
    if agent == "operations":
        return "stabilize_workflows", "Reducir fallos y latencia operativa"
    if agent == "marketing":
        return "nurture_campaigns", "Activar secuencias de nurturing"
    return "protect_margin", "Controlar costos API y eficiencia"


async def orchestrate_agents(
    *,
    organization_id: str,
    goal: str,
    context: Dict[str, Any],
    agents: List[str],
    dry_run: bool,
) -> Dict[str, Any]:
    actions: List[Dict[str, Any]] = []

    for agent in agents:
        strategy_code, strategy_text = _agent_strategy(agent, goal)
        selected_tool = choose_tool(
            candidates=_AGENT_TOOL_CATALOG.get(agent, []),
            context={"max_cost": float(context.get("max_cost", 1.0) or 1.0)},
            min_score=0.35,
        )
        tool_name = selected_tool.get("tool") if selected_tool else "manual.review"
        actions.append(
            {
                "agent": agent,
                "action": strategy_code,
                "rationale": strategy_text,
                "tool": str(tool_name),
                "payload": {
                    "goal": goal,
                    "organization_id": organization_id,
                    "context": context,
                },
            }
        )

    execution_result: Dict[str, Any] = {"dispatched": False, "count": 0}
    if not dry_run and actions:
        dispatch_result = await send_to_n8n_with_response(
            {
                "source": "agent_orchestrator",
                "organization_id": str(organization_id),
                "goal": goal,
                "actions": actions,
            }
        )
        execution_result = {
            "dispatched": bool(dispatch_result.get("dispatched")),
            "pending": bool(dispatch_result.get("pending")),
            "confirmed": bool(dispatch_result.get("confirmed")),
            "execution_status": str(dispatch_result.get("execution_status") or "not_dispatched"),
            "count": len(actions),
        }

    return {
        "goal": goal,
        "strategy": "multi-agent-coordination",
        "actions": actions,
        "executed": not dry_run,
        "execution_result": execution_result,
    }
