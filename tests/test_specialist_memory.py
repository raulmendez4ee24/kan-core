from brain.specialist_memory import SpecialistMemory


def test_specialist_memory_summary() -> None:
    mem = SpecialistMemory(max_items_per_specialist=10)
    mem.record(
        client_id="c1",
        specialist="operator",
        goal="goal a",
        status="completed",
        confidence=0.8,
        metadata={},
    )
    mem.record(
        client_id="c1",
        specialist="operator",
        goal="goal b",
        status="failed",
        confidence=0.4,
        metadata={},
    )

    summary = mem.summary(client_id="c1", specialist="operator")
    assert summary["count"] == 2
    assert 0.0 <= summary["success_rate"] <= 1.0
    assert isinstance(summary["recent_goals"], list)
    assert isinstance(summary["recent_failures"], list)
