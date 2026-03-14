from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select

from database import AsyncSessionLocal
from models import RuntimeEvent

logger = logging.getLogger("kan_core.runtime_events")


class RuntimeEventBus:
    def __init__(self) -> None:
        self._events: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._seq: Dict[str, int] = defaultdict(int)
        self._conds: Dict[str, asyncio.Condition] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        in_pytest = "PYTEST_CURRENT_TEST" in os.environ
        self._persist_enabled = (
            os.getenv("RUNTIME_EVENT_PERSISTENCE", "true").lower() in {"1", "true", "yes"}
            and not in_pytest
        )

    def emit(self, *, run_id: str, session_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        seq = int(self._seq[run_id]) + 1
        self._seq[run_id] = seq
        item = {
            "run_id": run_id,
            "session_id": session_id,
            "seq": seq,
            "event_type": str(event_type),
            "payload": dict(payload or {}),
            "created_at": datetime.now(timezone.utc),
        }
        self._events[run_id].append(item)
        self._schedule_persist(item)

        try:
            loop = asyncio.get_running_loop()
            cond = self._conds.get(run_id)
            if cond is None:
                cond = asyncio.Condition()
                self._conds[run_id] = cond

            async def _notify() -> None:
                async with cond:
                    cond.notify_all()

            self._track_task(loop.create_task(_notify()))
        except RuntimeError:
            pass
        return item

    def _track_task(self, task: asyncio.Task) -> None:
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _schedule_persist(self, item: Dict[str, Any]) -> None:
        if not self._persist_enabled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._track_task(loop.create_task(self._persist_event(item)))

    async def _persist_event(self, item: Dict[str, Any]) -> None:
        try:
            async with AsyncSessionLocal() as session:
                row = RuntimeEvent(
                    run_id=str(item.get("run_id") or ""),
                    session_id=str(item.get("session_id") or ""),
                    seq=int(item.get("seq") or 0),
                    event_type=str(item.get("event_type") or ""),
                    payload=dict(item.get("payload") or {}),
                )
                session.add(row)
                await session.commit()
        except Exception:
            # Runtime events must never crash autonomy flow.
            return

    def list_events(self, *, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        items = list(self._events.get(run_id) or [])
        return [x for x in items if int(x.get("seq") or 0) > int(after_seq)]

    async def list_events_async(self, *, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        in_memory = self.list_events(run_id=run_id, after_seq=after_seq)
        if in_memory:
            return in_memory
        if not self._persist_enabled:
            return in_memory
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(RuntimeEvent)
                    .where(
                        RuntimeEvent.run_id == run_id,
                        RuntimeEvent.seq > int(after_seq),
                    )
                    .order_by(RuntimeEvent.seq.asc())
                    .limit(500)
                )
                rows = list((await session.execute(stmt)).scalars().all())
            return [
                {
                    "run_id": row.run_id,
                    "session_id": row.session_id,
                    "seq": int(row.seq),
                    "event_type": row.event_type,
                    "payload": dict(row.payload or {}),
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        except Exception:
            return in_memory

    async def wait_for_events(self, *, run_id: str, after_seq: int, timeout_seconds: float) -> List[Dict[str, Any]]:
        pending = await self.list_events_async(run_id=run_id, after_seq=after_seq)
        if pending:
            return pending

        cond = self._conds.get(run_id)
        if cond is None:
            cond = asyncio.Condition()
            self._conds[run_id] = cond
        timeout = max(0.1, float(timeout_seconds))
        try:
            async with cond:
                await asyncio.wait_for(cond.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return await self.list_events_async(run_id=run_id, after_seq=after_seq)
        return await self.list_events_async(run_id=run_id, after_seq=after_seq)

    async def shutdown(self, timeout_seconds: float = 2.0) -> None:
        pending = [task for task in list(self._bg_tasks) if not task.done()]
        if not pending:
            return
        done, still_pending = await asyncio.wait(
            pending,
            timeout=max(0.1, float(timeout_seconds)),
        )
        _ = done
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)
        logger.info("RuntimeEventBus shutdown complete (pending=%s)", len(still_pending))


runtime_event_bus = RuntimeEventBus()
