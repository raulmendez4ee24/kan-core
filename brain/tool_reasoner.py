from typing import Any, Dict, List, Optional


def rank_tools(
    *,
    candidates: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    weighted: List[Dict[str, Any]] = []
    target_cost = float(context.get("max_cost", 1.0) or 1.0)
    weights = context.get("weights") if isinstance(context.get("weights"), dict) else {}
    w_conf = float(weights.get("confidence", 0.35))
    w_health = float(weights.get("health", 0.2))
    w_comp = float(weights.get("compatibility", 0.2))
    w_speed = float(weights.get("speed", 0.15))
    w_hist = float(weights.get("history", 0.1))
    w_cost = float(weights.get("cost", 0.1))
    w_friction = float(weights.get("friction", 0.05))

    for item in candidates:
        available = item.get("available", True)
        if available is False:
            weighted.append({**item, "reasoner_score": 0.0, "reasoner_unavailable": True})
            continue

        confidence = float(item.get("confidence") or item.get("score") or 0.5)
        health = float(item.get("health") or 0.7)
        cost = float(item.get("cost") or 0.1)
        compatibility = float(item.get("compatibility") or 0.7)
        speed = float(item.get("speed") or 0.6)
        history = float(item.get("history") or 0.0)
        friction = 1.0 if bool(item.get("requires_human_approval", False)) else 0.0

        cost_penalty = min(cost / max(target_cost, 0.001), 2.0) * max(w_cost, 0.0)
        friction_penalty = friction * max(w_friction, 0.0)
        value = (
            confidence * w_conf
            + health * w_health
            + compatibility * w_comp
            + speed * w_speed
            + history * w_hist
            - cost_penalty
            - friction_penalty
        )

        weighted.append({**item, "reasoner_score": round(value, 5)})

    weighted.sort(key=lambda x: float(x.get("reasoner_score") or 0.0), reverse=True)
    return weighted


def choose_tool(
    *,
    candidates: List[Dict[str, Any]],
    context: Dict[str, Any],
    min_score: float = 0.45,
) -> Optional[Dict[str, Any]]:
    ranked = rank_tools(candidates=candidates, context=context)
    if not ranked:
        return None
    selected = ranked[0]
    if float(selected.get("reasoner_score") or 0.0) < min_score:
        return None
    return selected
