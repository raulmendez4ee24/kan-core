from uuid import UUID, uuid4

from brain.autonomous_case_manager import classify_case_type
from brain.decision_engine import DecisionContext, decide_next_step
from brain.outcome_evaluator import evaluate_outcome
from brain.self_supervisor import decide_supervision_action


def test_decision_engine_prefers_customer_restore_when_rollbackable_exists() -> None:
    envelope = decide_next_step(
        DecisionContext(
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            case_id=uuid4(),
            objective_kind="customer_recovery",
            business_state={"recommended_focus": "customer_recovery", "critical_risks": []},
            world_state={
                "rollbackable_action": {
                    "operation_type": "delete",
                    "metadata": {
                        "operation_type": "delete",
                        "action_name": "delete_customer",
                        "rollback_snapshot": {"restore_payload": {"email": "lead@example.com"}},
                    },
                }
            },
            business_context={},
            recent_history=[],
        )
    )

    assert envelope.recommended_action == "restore_customer"
    assert envelope.recommended_tool == "db_internal"


def test_supervisor_switches_tool_on_mismatch_when_fallback_exists() -> None:
    decision = {
        "expected_outcome": "payment_followup_dispatched",
        "recommended_action": "payment_followup",
        "fallback_tools": ["http"],
        "requires_replan_if_failed": True,
        "risk_score": 0.4,
    }
    result = {"status": "failed", "completed": False}
    outcome = evaluate_outcome(
        decision=decision,
        execution_result=result,
        business_state={"ops_health_score": 0.8},
    )
    supervision = decide_supervision_action(
        decision=decision,
        execution_result=result,
        outcome_evaluation=outcome,
    )

    assert outcome["matches_expected"] is False
    assert supervision["action"] == "switch_tool"
    assert supervision["next_tool"] == "http"


def test_classifier_detects_workflow_recovery_from_incident_text() -> None:
    case_type = classify_case_type(
        current_goal="Hay error en webhook y se rompio el workflow de sincronizacion",
        business_context={"source": "master_brain"},
        requested_case_type=None,
        allow_generic=False,
    )

    assert case_type == "workflow_recovery"


def test_decision_engine_uses_business_focus_to_specialize_generic_ops_case() -> None:
    envelope = decide_next_step(
        DecisionContext(
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            case_id=uuid4(),
            objective_kind="ops_anomaly_response",
            business_state={
                "recommended_focus": "workflow_recovery",
                "critical_risks": ["workflow_degradation"],
                "ops_health_score": 0.42,
            },
            world_state={},
            business_context={"anomaly_type": "workflow_failure"},
            recent_history=[],
        )
    )

    assert envelope.objective_kind == "workflow_recovery"
    assert envelope.recommended_action == "recover_workflow"
    assert envelope.recommended_tool == "internal_service"


def test_decision_engine_supports_sales_execution_domain() -> None:
    envelope = decide_next_step(
        DecisionContext(
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            case_id=uuid4(),
            objective_kind="sales_execution",
            business_state={"recommended_focus": "sales_execution", "critical_risks": [], "ops_health_score": 0.9},
            world_state={},
            business_context={"source": "sales_autopilot.run"},
            recent_history=[],
        )
    )

    assert envelope.recommended_action == "advance_sales_pipeline"
    assert envelope.expected_outcome == "sales_progressed"
