import asyncio

from brain.runtime_events import RuntimeEventBus


def test_runtime_event_bus_emit_and_list() -> None:
    bus = RuntimeEventBus()
    bus.emit(run_id="r1", session_id="s1", event_type="lifecycle.start", payload={"a": 1})
    bus.emit(run_id="r1", session_id="s1", event_type="tool.dispatch", payload={"b": 2})

    items = bus.list_events(run_id="r1", after_seq=0)
    assert len(items) == 2
    assert items[0]["seq"] == 1
    assert items[1]["seq"] == 2


def test_runtime_event_bus_wait_for_events() -> None:
    bus = RuntimeEventBus()

    async def _run() -> None:
        async def _emit_later() -> None:
            await asyncio.sleep(0.02)
            bus.emit(run_id="r2", session_id="s2", event_type="lifecycle.running", payload={})

        asyncio.create_task(_emit_later())
        events = await bus.wait_for_events(run_id="r2", after_seq=0, timeout_seconds=0.3)
        assert len(events) == 1
        assert events[0]["event_type"] == "lifecycle.running"

    asyncio.run(_run())
