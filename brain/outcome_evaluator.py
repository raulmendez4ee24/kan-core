from __future__ import annotations

from typing import Any, Dict


def evaluate_outcome(
    *,
    decision: Dict[str, Any],
    execution_result: Dict[str, Any],
    business_state: Dict[str, Any],
) -> Dict[str, Any]:
    expected = str(decision.get("expected_outcome") or "").strip().lower()
    status = str(execution_result.get("status") or execution_result.get("execution_status") or "").strip().lower()
    dispatched = bool(execution_result.get("dispatched"))
    confirmed = bool(execution_result.get("confirmed"))
    completed = bool(execution_result.get("completed"))
    success = completed or confirmed or status in {"success", "completed", "restored", "accepted", "observed"}
    if not success and dispatched and status not in {"failed", "error", "crashed", "canceled"}:
        success = True

    matches = success
    severity = "low"
    reason = "expected_outcome_reached" if matches else "outcome_mismatch"
    if not matches and float(decision.get("risk_score") or 0.0) >= 0.7:
        severity = "high"
    elif not matches:
        severity = "medium"

    if expected == "payment_compensated" and success:
        reason = "payment_compensated"
    elif expected == "customer_restored" and success:
        reason = "customer_restored"

    return {
        "expected_outcome": expected,
        "matches_expected": matches,
        "severity": severity,
        "reason": reason,
        "needs_replan": bool(not matches and decision.get("requires_replan_if_failed", True)),
        "can_rollback": bool(not matches and decision.get("recommended_action") not in {"restore_customer", "refund_payment"}),
        "ops_health_score": float(business_state.get("ops_health_score") or 0.0),
    }
