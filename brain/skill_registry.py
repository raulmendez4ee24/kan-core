from __future__ import annotations

from typing import Any, Dict, List

from brain.skill_loader import list_skill_names, load_skill

_CASE_SKILL_MAP = {
    "payment_followthrough": ["payment_recovery", "rollback_procedures"],
    "collections_followthrough": ["collections_followthrough", "rollback_procedures"],
    "onboarding_execution": ["onboarding_execution"],
    "support_resolution": ["support_resolution"],
    "workflow_recovery": ["workflow_recovery", "rollback_procedures"],
    "ops_anomaly_response": ["workflow_recovery"],
    "campaign_execution": ["meta_ads_growth", "marketing_execution", "creative_hooks"],
    "sales_execution": ["sales_execution"],
}


def match_skills(
    *,
    case_type: str,
    business_context: Dict[str, Any] | None = None,
    max_skills: int = 3,
) -> List[Dict[str, Any]]:
    context = dict(business_context or {})
    names = list(_CASE_SKILL_MAP.get(str(case_type or "").strip().lower(), []))
    searchable = " ".join(
        str(context.get(key) or "").strip().lower()
        for key in ("source", "anomaly_type", "intent_hint", "current_mode")
    )
    if "rollback" in searchable and "rollback_procedures" not in names:
        names.append("rollback_procedures")
    items: List[Dict[str, Any]] = []
    for name in names[: max(1, max_skills)]:
        loaded = load_skill(name)
        if loaded:
            items.append(loaded)
    return items


def list_skills() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for name in list_skill_names():
        loaded = load_skill(name)
        if loaded:
            items.append(loaded)
    return items
