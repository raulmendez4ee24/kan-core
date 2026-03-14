from schemas.optimizer import GoalMetricsSnapshot
from brain.business_optimizer import detect_opportunities


def test_detect_opportunities_prioritizes_unanswered_messages() -> None:
    metrics = GoalMetricsSnapshot(
        window_days=30,
        total_conversations=20,
        total_messages=150,
        user_messages=100,
        assistant_messages=70,
        unanswered_messages=30,
        leads_detected=40,
        appointments_detected=6,
        appointment_rate=0.15,
        active_integrations=1,
        active_functions=["crm"],
        total_runs=18,
        successful_runs=9,
        failed_runs=9,
        error_rate=50.0,
        avg_duration_ms=210.0,
        api_cost_usd=1.2,
        auto_repair_used=3,
        fallback_used=4,
    )

    recs = detect_opportunities(metrics)
    assert recs
    assert recs[0].id == "op_unanswered_messages"
    assert any(item.id == "op_low_appointment_rate" for item in recs)
    assert any(item.id == "op_high_error_rate" for item in recs)


def test_detect_opportunities_returns_growth_iteration_when_no_alerts() -> None:
    metrics = GoalMetricsSnapshot(
        window_days=30,
        total_conversations=5,
        total_messages=20,
        user_messages=10,
        assistant_messages=10,
        unanswered_messages=0,
        leads_detected=2,
        appointments_detected=1,
        appointment_rate=0.5,
        active_integrations=3,
        active_functions=["crm", "payments", "email"],
        total_runs=8,
        successful_runs=8,
        failed_runs=0,
        error_rate=0.0,
        avg_duration_ms=110.0,
        api_cost_usd=0.2,
        auto_repair_used=0,
        fallback_used=0,
    )

    recs = detect_opportunities(metrics)
    assert len(recs) == 1
    assert recs[0].id == "op_growth_iteration"
