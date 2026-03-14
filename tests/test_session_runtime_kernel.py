import asyncio
import importlib
import sys
import types
from types import SimpleNamespace


def test_session_runtime_kernel_serialized_runs(monkeypatch):
    # Stub heavy imports before loading the kernel module.
    fake_loop_module = types.ModuleType("brain.desktop_autonomous_loop")

    async def fake_run_desktop_autonomy_loop(*, client_id, request, session):
        return SimpleNamespace(status="completed", model_dump=lambda: {"status": "completed", "summary": "ok"})

    fake_loop_module.run_desktop_autonomy_loop = fake_run_desktop_autonomy_loop
    monkeypatch.setitem(sys.modules, "brain.desktop_autonomous_loop", fake_loop_module)

    kernel_module = importlib.import_module("brain.session_runtime_kernel")
    SessionRuntimeKernel = kernel_module.SessionRuntimeKernel

    calls = []
    original = kernel_module.run_desktop_autonomy_loop

    async def wrapped_fake_run_desktop_autonomy_loop(*, client_id, request, session):
        calls.append({"client_id": client_id, "goal": request.goal, "session": session})
        await asyncio.sleep(0.02)
        return await original(client_id=client_id, request=request, session=session)

    class _FakeAsyncSessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(kernel_module, "run_desktop_autonomy_loop", wrapped_fake_run_desktop_autonomy_loop)
    monkeypatch.setattr(kernel_module, "AsyncSessionLocal", lambda: _FakeAsyncSessionCtx())

    async def _run():
        kernel = SessionRuntimeKernel()
        s1 = await kernel.start_run(
            client_id="c1",
            session_id="same-session",
            goal="goal one",
            max_steps=3,
            execution_mode="balanced",
            context={},
            dry_run=False,
        )
        s2 = await kernel.start_run(
            client_id="c1",
            session_id="same-session",
            goal="goal two",
            max_steps=3,
            execution_mode="balanced",
            context={},
            dry_run=False,
        )

        await kernel.wait_run(run_id=s1.run_id, timeout_seconds=2.0)
        await kernel.wait_run(run_id=s2.run_id, timeout_seconds=2.0)
        assert len(calls) == 2
        assert kernel.queue_depth("same-session") == 0

    asyncio.run(_run())


def test_session_runtime_kernel_replay_run(monkeypatch):
    fake_loop_module = types.ModuleType("brain.desktop_autonomous_loop")

    async def fake_run_desktop_autonomy_loop(*, client_id, request, session):
        return SimpleNamespace(status="completed", model_dump=lambda: {"status": "completed", "summary": request.goal})

    fake_loop_module.run_desktop_autonomy_loop = fake_run_desktop_autonomy_loop
    monkeypatch.setitem(sys.modules, "brain.desktop_autonomous_loop", fake_loop_module)

    kernel_module = importlib.import_module("brain.session_runtime_kernel")
    SessionRuntimeKernel = kernel_module.SessionRuntimeKernel

    calls = []

    async def wrapped_fake_run_desktop_autonomy_loop(*, client_id, request, session):
        calls.append({"goal": request.goal, "context": dict(request.context or {})})
        await asyncio.sleep(0.01)
        return SimpleNamespace(status="completed", model_dump=lambda: {"status": "completed", "summary": request.goal})

    class _FakeAsyncSessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(kernel_module, "run_desktop_autonomy_loop", wrapped_fake_run_desktop_autonomy_loop)
    monkeypatch.setattr(kernel_module, "AsyncSessionLocal", lambda: _FakeAsyncSessionCtx())

    async def _run():
        kernel = SessionRuntimeKernel()
        first = await kernel.start_run(
            client_id="c1",
            session_id="sess-r",
            goal="original",
            max_steps=4,
            execution_mode="balanced",
            context={"a": 1},
            dry_run=False,
        )
        await kernel.wait_run(run_id=first.run_id, timeout_seconds=2.0)
        replay = await kernel.replay_run(source_run_id=first.run_id)
        done = await kernel.wait_run(run_id=replay.run_id, timeout_seconds=2.0)
        assert done is not None
        assert done.status == "completed"
        assert len(calls) == 2
        assert calls[1]["context"]["replay"]["source_run_id"] == first.run_id

    asyncio.run(_run())


def test_session_runtime_kernel_resumes_queued_run_without_live_task(monkeypatch):
    fake_loop_module = types.ModuleType("brain.desktop_autonomous_loop")

    async def fake_run_desktop_autonomy_loop(*, client_id, request, session):
        return SimpleNamespace(status="completed", model_dump=lambda: {"status": "completed", "summary": "resumed"})

    fake_loop_module.run_desktop_autonomy_loop = fake_run_desktop_autonomy_loop
    monkeypatch.setitem(sys.modules, "brain.desktop_autonomous_loop", fake_loop_module)

    kernel_module = importlib.import_module("brain.session_runtime_kernel")
    SessionRuntimeKernel = kernel_module.SessionRuntimeKernel
    RuntimeRunState = kernel_module.RuntimeRunState

    class _FakeAsyncSessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(kernel_module, "AsyncSessionLocal", lambda: _FakeAsyncSessionCtx())
    monkeypatch.setattr(kernel_module, "run_desktop_autonomy_loop", fake_run_desktop_autonomy_loop)

    async def _run():
        kernel = SessionRuntimeKernel()
        run_id = "resume-run-1"
        state = RuntimeRunState(
            run_id=run_id,
            session_id="resume-sess",
            client_id="c1",
            status="queued",
            result={
                "_runtime": {
                    "request": {
                        "client_id": "c1",
                        "session_id": "resume-sess",
                        "goal": "resume goal",
                        "max_steps": 3,
                        "execution_mode": "balanced",
                        "context": {},
                        "dry_run": False,
                    },
                    "journal": [],
                }
            },
        )
        kernel._runs[run_id] = state
        kernel._run_events[run_id] = asyncio.Event()
        out = await kernel.wait_run(run_id=run_id, timeout_seconds=2.0)
        assert out is not None
        assert out.status == "completed"
        runtime_meta = out.result.get("_runtime", {})
        phases = [str(item.get("phase")) for item in runtime_meta.get("journal", []) if isinstance(item, dict)]
        assert "lifecycle.resumed" in phases

    asyncio.run(_run())


def test_session_runtime_kernel_get_run_journal_filters(monkeypatch):
    fake_loop_module = types.ModuleType("brain.desktop_autonomous_loop")

    async def fake_run_desktop_autonomy_loop(*, client_id, request, session):
        return SimpleNamespace(status="completed", model_dump=lambda: {"status": "completed", "summary": "ok"})

    fake_loop_module.run_desktop_autonomy_loop = fake_run_desktop_autonomy_loop
    monkeypatch.setitem(sys.modules, "brain.desktop_autonomous_loop", fake_loop_module)

    kernel_module = importlib.import_module("brain.session_runtime_kernel")
    SessionRuntimeKernel = kernel_module.SessionRuntimeKernel
    RuntimeRunState = kernel_module.RuntimeRunState

    async def _run():
        kernel = SessionRuntimeKernel()
        run_id = "journal-run-1"
        state = RuntimeRunState(
            run_id=run_id,
            session_id="sess-j",
            client_id="c1",
            status="completed",
            result={
                "_runtime": {
                    "journal": [
                        {"ts": "2026-02-23T00:00:00+00:00", "phase": "lifecycle.queued", "payload": {}},
                        {"ts": "2026-02-23T00:00:01+00:00", "phase": "lifecycle.running", "payload": {}},
                        {"ts": "2026-02-23T00:00:02+00:00", "phase": "lifecycle.running", "payload": {"x": 1}},
                    ]
                }
            },
        )
        kernel._runs[run_id] = state
        filtered = await kernel.get_run_journal(run_id=run_id, phase="lifecycle.running", limit=1)
        assert filtered is not None
        assert len(filtered) == 1
        assert filtered[0]["phase"] == "lifecycle.running"
        assert filtered[0]["payload"]["x"] == 1

    asyncio.run(_run())


def test_session_runtime_kernel_replay_blocks_non_terminal_without_override(monkeypatch):
    fake_loop_module = types.ModuleType("brain.desktop_autonomous_loop")

    async def fake_run_desktop_autonomy_loop(*, client_id, request, session):
        return SimpleNamespace(status="completed", model_dump=lambda: {"status": "completed", "summary": "ok"})

    fake_loop_module.run_desktop_autonomy_loop = fake_run_desktop_autonomy_loop
    monkeypatch.setitem(sys.modules, "brain.desktop_autonomous_loop", fake_loop_module)

    kernel_module = importlib.import_module("brain.session_runtime_kernel")
    SessionRuntimeKernel = kernel_module.SessionRuntimeKernel
    RuntimeRunState = kernel_module.RuntimeRunState

    async def _run():
        kernel = SessionRuntimeKernel()
        kernel._runs["inflight"] = RuntimeRunState(
            run_id="inflight",
            session_id="s1",
            client_id="c1",
            status="running",
            result={"_runtime": {"request": {"client_id": "c1", "session_id": "s1", "goal": "g", "max_steps": 3, "execution_mode": "balanced", "context": {}, "dry_run": False}}},
        )
        try:
            await kernel.replay_run(source_run_id="inflight")
            assert False, "expected ValueError"
        except ValueError as exc:
            assert str(exc) == "source_run_not_terminal"

    asyncio.run(_run())
