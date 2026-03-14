from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID
from uuid import uuid4

from sqlalchemy import select

from database import AsyncSessionLocal
from brain.desktop_autonomous_loop import run_desktop_autonomy_loop
from brain.runtime_events import runtime_event_bus
from models import RuntimeRun
from schemas.desktop_autonomy import DesktopAutonomyRunRequest


@dataclass
class RuntimeRunState:
    run_id: str
    session_id: str
    client_id: Optional[str] = None
    status: str = "queued"
    result: Dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionRuntimeKernel:
    def __init__(self) -> None:
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._runs: Dict[str, RuntimeRunState] = {}
        self._run_events: Dict[str, asyncio.Event] = {}
        self._run_tasks: Dict[str, asyncio.Task[Any]] = {}
        self._queue_depth: Dict[str, int] = {}
        self._queue_accounted_runs: set[str] = set()
        self._persist_enabled = (
            os.getenv("RUNTIME_RUN_PERSISTENCE", "true").lower() in {"1", "true", "yes"}
            and "PYTEST_CURRENT_TEST" not in os.environ
        )
        self._journal_limit = max(20, int(os.getenv("RUNTIME_RUN_JOURNAL_LIMIT", "300")))
        self._terminal_statuses = {"completed", "failed", "blocked", "dry_run"}

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    def get_run(self, run_id: str) -> Optional[RuntimeRunState]:
        return self._runs.get(run_id)

    @staticmethod
    def _normalize_execution_mode(execution_mode: str) -> str:
        token = str(execution_mode or "").strip().lower()
        return token if token in {"safe", "balanced", "autonomous"} else "autonomous"

    @staticmethod
    def _safe_request_payload(
        *,
        client_id: str,
        session_id: str,
        goal: str,
        max_steps: int,
        execution_mode: str,
        context: Dict[str, Any],
        dry_run: bool,
    ) -> Dict[str, Any]:
        return {
            "client_id": str(client_id),
            "session_id": str(session_id),
            "goal": str(goal),
            "max_steps": max(1, min(int(max_steps), 30)),
            "execution_mode": SessionRuntimeKernel._normalize_execution_mode(execution_mode),
            "context": dict(context or {}),
            "dry_run": bool(dry_run),
        }

    def _append_journal(self, state: RuntimeRunState, *, phase: str, payload: Optional[Dict[str, Any]] = None) -> None:
        result = dict(state.result or {})
        runtime_meta = result.get("_runtime")
        if not isinstance(runtime_meta, dict):
            runtime_meta = {}
        journal = runtime_meta.get("journal")
        if not isinstance(journal, list):
            journal = []
        journal.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "phase": str(phase or "").strip() or "unknown",
                "payload": dict(payload or {}),
            }
        )
        runtime_meta["journal"] = journal[-self._journal_limit :]
        result["_runtime"] = runtime_meta
        state.result = result

    def _account_queue(self, *, run_id: str, session_id: str) -> None:
        if run_id in self._queue_accounted_runs:
            return
        self._queue_accounted_runs.add(run_id)
        self._queue_depth[session_id] = int(self._queue_depth.get(session_id, 0)) + 1

    def _release_queue(self, *, run_id: str, session_id: str) -> None:
        if run_id not in self._queue_accounted_runs:
            return
        self._queue_accounted_runs.discard(run_id)
        self._queue_depth[session_id] = max(0, int(self._queue_depth.get(session_id, 1)) - 1)

    def _schedule_run_task(self, *, run_id: str, request_payload: Dict[str, Any]) -> None:
        task = self._run_tasks.get(run_id)
        if task and (not task.done()):
            return
        self._run_tasks[run_id] = asyncio.create_task(
            self._run_serialized(
                run_id=run_id,
                request_payload=request_payload,
            )
        )

    async def start_run(
        self,
        *,
        client_id: str,
        session_id: str,
        goal: str,
        max_steps: int,
        execution_mode: str,
        context: Dict[str, Any],
        dry_run: bool,
        replay_of: Optional[str] = None,
    ) -> RuntimeRunState:
        run_id = str(uuid4())
        request_payload = self._safe_request_payload(
            client_id=client_id,
            session_id=session_id,
            goal=goal,
            max_steps=max_steps,
            execution_mode=execution_mode,
            context=context,
            dry_run=dry_run,
        )
        state = RuntimeRunState(
            run_id=run_id,
            session_id=session_id,
            client_id=client_id,
            status="queued",
            result={
                "_runtime": {
                    "request": request_payload,
                    "replay_of": replay_of,
                    "resumed": False,
                    "journal": [],
                }
            },
        )
        self._append_journal(
            state,
            phase=("replay.queued" if replay_of else "lifecycle.queued"),
            payload={"goal": goal, "replay_of": replay_of},
        )
        self._runs[run_id] = state
        self._run_events[run_id] = asyncio.Event()
        self._account_queue(run_id=run_id, session_id=session_id)
        await self._persist_run_state(state)

        runtime_event_bus.emit(
            run_id=run_id,
            session_id=session_id,
            event_type=("lifecycle.replay_queued" if replay_of else "lifecycle.queued"),
            payload={"goal": goal, "queue_depth": self._queue_depth[session_id], "replay_of": replay_of},
        )
        self._schedule_run_task(run_id=run_id, request_payload=request_payload)
        return state

    @staticmethod
    def _parse_client_uuid(client_id: Optional[str]) -> Optional[UUID]:
        if not client_id:
            return None
        try:
            return UUID(str(client_id))
        except Exception:
            return None

    async def _persist_run_state(self, state: RuntimeRunState) -> None:
        if not self._persist_enabled:
            return
        try:
            async with AsyncSessionLocal() as session:
                stmt = select(RuntimeRun).where(RuntimeRun.run_id == state.run_id).limit(1)
                row = (await session.execute(stmt)).scalars().first()
                if not row:
                    row = RuntimeRun(
                        run_id=state.run_id,
                        client_id=self._parse_client_uuid(state.client_id),
                        session_id=state.session_id,
                        status=state.status,
                        result=dict(state.result or {}),
                    )
                    session.add(row)
                else:
                    row.status = state.status
                    row.result = dict(state.result or {})
                    if not row.client_id:
                        row.client_id = self._parse_client_uuid(state.client_id)
                    row.updated_at = state.updated_at
                await session.commit()
        except Exception:
            return

    async def _load_persisted_run_state(self, run_id: str) -> Optional[RuntimeRunState]:
        if not self._persist_enabled:
            return None
        try:
            async with AsyncSessionLocal() as session:
                stmt = select(RuntimeRun).where(RuntimeRun.run_id == run_id).limit(1)
                row = (await session.execute(stmt)).scalars().first()
            if not row:
                return None
            state = RuntimeRunState(
                run_id=row.run_id,
                session_id=row.session_id,
                client_id=str(row.client_id) if row.client_id else None,
                status=row.status,
                result=dict(row.result or {}),
                updated_at=row.updated_at,
            )
            self._runs[run_id] = state
            self._run_events.setdefault(run_id, asyncio.Event())
            if state.status in {"completed", "failed", "blocked", "dry_run"}:
                self._run_events[run_id].set()
            return state
        except Exception:
            return None

    async def _run_serialized(
        self,
        *,
        run_id: str,
        request_payload: Dict[str, Any],
    ) -> None:
        state = self._runs[run_id]
        client_id = str(request_payload.get("client_id") or "")
        session_id = str(request_payload.get("session_id") or state.session_id)
        goal = str(request_payload.get("goal") or "")
        max_steps = max(1, min(int(request_payload.get("max_steps") or 8), 30))
        execution_mode = self._normalize_execution_mode(
            str(request_payload.get("execution_mode") or os.getenv("AUTONOMY_DEFAULT_EXECUTION_MODE", "autonomous"))
        )
        context = dict(request_payload.get("context") or {})
        dry_run = bool(request_payload.get("dry_run"))

        lock = self._get_lock(session_id)
        async with lock:
            state.status = "running"
            state.updated_at = datetime.now(timezone.utc)
            self._append_journal(
                state,
                phase="lifecycle.running",
                payload={"goal": goal, "execution_mode": execution_mode},
            )
            await self._persist_run_state(state)
            runtime_event_bus.emit(
                run_id=run_id,
                session_id=session_id,
                event_type="lifecycle.running",
                payload={"goal": goal},
            )

            try:
                runtime_meta = state.result.get("_runtime") if isinstance(state.result, dict) else None
                runtime_meta = dict(runtime_meta) if isinstance(runtime_meta, dict) else {}
                req = DesktopAutonomyRunRequest(
                    goal=goal,
                    max_steps=max(1, min(int(max_steps), 30)),
                    execution_mode=(execution_mode if execution_mode in {"safe", "balanced", "autonomous"} else "autonomous"),
                    context={**dict(context or {}), "runtime_kernel": {"run_id": run_id, "session_id": session_id}},
                    dry_run=bool(dry_run),
                    wait_for_results=True,
                    enable_recovery=True,
                    enable_self_healing=True,
                    enforce_quality_gate=False,
                    dynamic_risk_controls=False,
                )
                async with AsyncSessionLocal() as session:
                    response = await run_desktop_autonomy_loop(
                        client_id=client_id,
                        request=req,
                        session=session,
                    )
                state.result = response.model_dump()
                state.result["_runtime"] = runtime_meta
                state.status = str(response.status)
                state.updated_at = datetime.now(timezone.utc)
                self._append_journal(
                    state,
                    phase="lifecycle.completed",
                    payload={"status": state.status, "summary": state.result.get("summary")},
                )
                await self._persist_run_state(state)
                runtime_event_bus.emit(
                    run_id=run_id,
                    session_id=session_id,
                    event_type="lifecycle.completed",
                    payload={"status": state.status, "summary": state.result.get("summary")},
                )
            except Exception as exc:
                runtime_meta = state.result.get("_runtime") if isinstance(state.result, dict) else None
                runtime_meta = dict(runtime_meta) if isinstance(runtime_meta, dict) else {}
                state.status = "failed"
                state.result = {"error": str(exc)}
                state.result["_runtime"] = runtime_meta
                state.updated_at = datetime.now(timezone.utc)
                self._append_journal(
                    state,
                    phase="lifecycle.failed",
                    payload={"error": str(exc)},
                )
                await self._persist_run_state(state)
                runtime_event_bus.emit(
                    run_id=run_id,
                    session_id=session_id,
                    event_type="lifecycle.failed",
                    payload={"error": str(exc)},
                )
            finally:
                state.updated_at = datetime.now(timezone.utc)
                self._release_queue(run_id=run_id, session_id=session_id)
                self._run_events[run_id].set()

    async def _ensure_live_execution(self, state: RuntimeRunState) -> None:
        if state.status not in {"queued", "running"}:
            return
        task = self._run_tasks.get(state.run_id)
        if task and (not task.done()):
            return
        runtime_meta = state.result.get("_runtime") if isinstance(state.result, dict) else None
        request_payload = runtime_meta.get("request") if isinstance(runtime_meta, dict) else None
        if not isinstance(request_payload, dict):
            return
        state.session_id = str(request_payload.get("session_id") or state.session_id)
        self._account_queue(run_id=state.run_id, session_id=state.session_id)
        if isinstance(runtime_meta, dict):
            runtime_meta["resumed"] = True
        self._append_journal(
            state,
            phase="lifecycle.resumed",
            payload={"status": state.status},
        )
        await self._persist_run_state(state)
        runtime_event_bus.emit(
            run_id=state.run_id,
            session_id=state.session_id,
            event_type="lifecycle.resumed",
            payload={"status": state.status},
        )
        self._schedule_run_task(run_id=state.run_id, request_payload=request_payload)

    async def replay_run(
        self,
        *,
        source_run_id: str,
        session_id: Optional[str] = None,
        context_patch: Optional[Dict[str, Any]] = None,
        dry_run: Optional[bool] = None,
        max_steps: Optional[int] = None,
        execution_mode: Optional[str] = None,
        allow_non_terminal: bool = False,
    ) -> RuntimeRunState:
        source_state = self._runs.get(source_run_id)
        if not source_state:
            source_state = await self._load_persisted_run_state(source_run_id)
        if not source_state:
            raise ValueError("source_run_not_found")
        if (not allow_non_terminal) and str(source_state.status or "") not in self._terminal_statuses:
            raise ValueError("source_run_not_terminal")

        runtime_meta = source_state.result.get("_runtime") if isinstance(source_state.result, dict) else None
        request_payload = runtime_meta.get("request") if isinstance(runtime_meta, dict) else None
        if not isinstance(request_payload, dict):
            raise ValueError("source_run_missing_request")

        merged_context = dict(request_payload.get("context") or {})
        merged_context.update(dict(context_patch or {}))
        merged_context["replay"] = {"source_run_id": source_run_id}
        return await self.start_run(
            client_id=str(request_payload.get("client_id") or source_state.client_id or ""),
            session_id=str(session_id or request_payload.get("session_id") or source_state.session_id),
            goal=str(request_payload.get("goal") or ""),
            max_steps=int(max_steps if max_steps is not None else int(request_payload.get("max_steps") or 8)),
            execution_mode=str(execution_mode or request_payload.get("execution_mode") or os.getenv("AUTONOMY_DEFAULT_EXECUTION_MODE", "autonomous")),
            context=merged_context,
            dry_run=(bool(dry_run) if dry_run is not None else bool(request_payload.get("dry_run"))),
            replay_of=source_run_id,
        )

    async def wait_run(self, *, run_id: str, timeout_seconds: float = 60.0) -> Optional[RuntimeRunState]:
        state = self._runs.get(run_id)
        if not state:
            state = await self._load_persisted_run_state(run_id)
            if not state:
                return None
        await self._ensure_live_execution(state)
        if state.status in {"completed", "failed", "blocked", "dry_run"}:
            return state
        evt = self._run_events.get(run_id)
        if not evt:
            return state
        try:
            await asyncio.wait_for(evt.wait(), timeout=max(0.1, float(timeout_seconds)))
        except asyncio.TimeoutError:
            return state
        return self._runs.get(run_id)

    async def get_run_journal(
        self,
        *,
        run_id: str,
        phase: Optional[str] = None,
        limit: int = 200,
    ) -> Optional[list[Dict[str, Any]]]:
        state = self._runs.get(run_id)
        if not state:
            state = await self._load_persisted_run_state(run_id)
            if not state:
                return None
        runtime_meta = state.result.get("_runtime") if isinstance(state.result, dict) else None
        journal = runtime_meta.get("journal") if isinstance(runtime_meta, dict) else None
        if not isinstance(journal, list):
            return []
        token = str(phase or "").strip().lower()
        rows = []
        for item in journal:
            if not isinstance(item, dict):
                continue
            if token and str(item.get("phase") or "").strip().lower() != token:
                continue
            rows.append(dict(item))
        safe_limit = max(1, min(int(limit), 5000))
        if len(rows) > safe_limit:
            rows = rows[-safe_limit:]
        return rows

    def queue_depth(self, session_id: str) -> int:
        return int(self._queue_depth.get(session_id, 0))


runtime_kernel = SessionRuntimeKernel()
