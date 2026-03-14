from brain.execution_guard import (
    observe_guarded_execution,
    plan_guarded_execution,
    reset_execution_guard_state_for_tests,
    summarize_guard_audit_entries,
)


def setup_function() -> None:
    reset_execution_guard_state_for_tests()


def teardown_function() -> None:
    reset_execution_guard_state_for_tests()


def test_plan_guarded_execution_batches_delete_lists(monkeypatch) -> None:
    monkeypatch.setenv("AUTONOMY_DELETE_MAX_RECORDS", "2")

    plan = plan_guarded_execution(
        {
            "source": "unit_test",
            "action": {"action": "delete_customer", "arguments": {"record_ids": [1, 2, 3, 4, 5]}},
        },
        channel="n8n",
    )

    assert plan["should_pause"] is False
    assert plan["batch_count"] == 3
    assert [len(item["action"]["arguments"]["record_ids"]) for item in plan["batch_payloads"]] == [2, 2, 1]
    assert plan["dry_run_preview"]["estimated_records"] == 5
    assert plan["reversible"] is False


def test_plan_guarded_execution_pauses_on_repeated_loop(monkeypatch) -> None:
    monkeypatch.setenv("AUTONOMY_RECENT_ACTION_REPEAT_LIMIT", "3")
    payload = {
        "source": "unit_test",
        "action": {"action": "delete_customer", "arguments": {"record_ids": [1]}},
    }

    first = plan_guarded_execution(payload, channel="n8n")
    second = plan_guarded_execution(payload, channel="n8n")
    third = plan_guarded_execution(payload, channel="n8n")

    assert first["should_pause"] is False
    assert second["should_pause"] is False
    assert third["should_pause"] is True
    assert third["pause_reason"] == "loop_detected"


def test_observe_guarded_execution_triggers_tripwire(monkeypatch) -> None:
    monkeypatch.setenv("AUTONOMY_OUTPUT_ANOMALY_RATIO", "2.0")
    payload = {
        "source": "unit_test",
        "action": {"action": "delete_customer", "arguments": {"record_ids": [1]}},
    }
    plan = plan_guarded_execution(payload, channel="n8n")

    observe_guarded_execution(plan, {"impact_score": 1.0})
    observe_guarded_execution(plan, {"impact_score": 1.0})
    observe_guarded_execution(plan, {"impact_score": 1.0})
    tripwire = observe_guarded_execution(plan, {"impact_score": 5.0})

    assert tripwire["triggered"] is True
    assert tripwire["baseline_avg"] == 1.0
    assert tripwire["observed_score"] == 5.0


def test_summarize_guard_audit_entries_groups_events() -> None:
    summary = summarize_guard_audit_entries(
        [
            {
                "action": "execution_guard.preflight",
                "created_at": "2026-03-01T00:00:00Z",
                "metadata": {
                    "operation_type": "delete",
                    "dry_run_preview": {"simulated": True},
                    "rollback_snapshot": {"action": "delete_customer"},
                    "reversible": False,
                },
            },
            {
                "action": "execution_guard.paused",
                "created_at": "2026-03-01T00:01:00Z",
                "metadata": {
                    "action_name": "delete_customer",
                    "operation_type": "delete",
                    "pause_reason": "loop_detected",
                    "reversible": False,
                },
            },
            {
                "action": "execution_guard.tripwire",
                "created_at": "2026-03-01T00:02:00Z",
                "metadata": {
                    "action_name": "create_payment",
                    "operation_type": "payment",
                    "tripwire": {"triggered": True},
                    "reversible": False,
                },
            },
        ]
    )

    assert summary["events"] == 3
    assert summary["preflight_count"] == 1
    assert summary["paused_count"] == 1
    assert summary["tripwire_count"] == 1
    assert summary["destructive_previews"] == 1
    assert summary["irreversible_actions"] == 3
    assert summary["by_operation"] == {"delete": 2, "payment": 1}
