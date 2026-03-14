from __future__ import annotations

from typing import Any, Dict


def decide_supervision_action(
    *,
    decision: Dict[str, Any],
    execution_result: Dict[str, Any],
    outcome_evaluation: Dict[str, Any],
) -> Dict[str, Any]:
    if bool(outcome_evaluation.get("matches_expected")):
        return {"action": "close_case", "reason": str(outcome_evaluation.get("reason") or "success")}

    if bool(execution_result.get("rollback_attempted")):
        return {"action": "escalate", "reason": "rollback_attempt_failed"}

    if bool(execution_result.get("compensated")):
        return {"action": "close_case", "reason": "compensation_applied"}

    fallbacks = list(decision.get("fallback_tools") or [])
    if fallbacks:
        return {"action": "switch_tool", "reason": "fallback_available", "next_tool": str(fallbacks[0])}

    if bool(outcome_evaluation.get("can_rollback")):
        return {"action": "rollback_and_compensate", "reason": "rollback_recommended"}

    if bool(outcome_evaluation.get("needs_replan")):
        return {"action": "replan", "reason": str(outcome_evaluation.get("reason") or "mismatch")}

    return {"action": "escalate", "reason": str(outcome_evaluation.get("reason") or "unknown")}
