from __future__ import annotations

from typing import Any, Dict


def derive_case_priority(
    *,
    case_type: str,
    business_state: Dict[str, Any],
    business_context: Dict[str, Any],
) -> Dict[str, float]:
    case_token = str(case_type or "").strip().lower()
    base_priority = 0.55
    base_risk = 0.35

    recommended_focus = str(business_state.get("recommended_focus") or "")
    critical_risks = list(business_state.get("critical_risks") or [])
    cash_exposure = float(business_state.get("cash_exposure") or 0.0)
    ops_health = float(business_state.get("ops_health_score") or 0.0)
    customer_impact = float(business_state.get("customer_impact_score") or 0.0)

    if case_token == "payment_followthrough":
        base_priority += 0.15
        base_risk += 0.20
        if cash_exposure > 0:
            base_priority += min(cash_exposure / 5000.0, 0.20)
    elif case_token == "customer_recovery":
        base_priority += 0.10
        base_risk += 0.15
        if customer_impact > 0:
            base_priority += min(customer_impact / 100.0, 0.15)
    elif case_token == "ops_anomaly_response":
        base_priority += 0.08
        base_risk += 0.12
        if ops_health < 0.7:
            base_priority += 0.10
            base_risk += 0.10
    elif case_token == "workflow_recovery":
        base_priority += 0.16
        base_risk += 0.18
        if ops_health < 0.75:
            base_priority += 0.12
            base_risk += 0.10
    elif case_token == "campaign_execution":
        base_priority += 0.09
        base_risk += 0.08
        if customer_impact < 40 and cash_exposure <= 0:
            base_priority += 0.05
    elif case_token == "sales_execution":
        base_priority += 0.11
        base_risk += 0.09
    elif case_token == "support_resolution":
        base_priority += 0.12
        base_risk += 0.11
        if customer_impact > 0:
            base_priority += min(customer_impact / 120.0, 0.10)
    elif case_token == "onboarding_execution":
        base_priority += 0.10
        base_risk += 0.07
    elif case_token == "collections_followthrough":
        base_priority += 0.14
        base_risk += 0.16
        if cash_exposure > 0:
            base_priority += min(cash_exposure / 6000.0, 0.18)

    if critical_risks:
        base_priority += min(len(critical_risks) * 0.03, 0.12)
        base_risk += min(len(critical_risks) * 0.03, 0.12)

    if isinstance(business_context.get("high_impact_operation"), bool) and business_context.get("high_impact_operation"):
        base_priority += 0.12
        base_risk += 0.12

    if recommended_focus and recommended_focus == case_token:
        base_priority += 0.08

    return {
        "priority_score": round(max(0.0, min(base_priority, 1.0)), 4),
        "risk_score": round(max(0.0, min(base_risk, 1.0)), 4),
    }
