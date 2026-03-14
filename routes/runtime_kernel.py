from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

from brain.runtime_events import runtime_event_bus
from brain.session_runtime_kernel import runtime_kernel
from schemas.runtime_kernel import (
    RuntimeJournalItem,
    RuntimeJournalResponse,
    RuntimeEventsResponse,
    RuntimeEventItem,
    RuntimeReplayRequest,
    RuntimeReplayResponse,
    RuntimeStartRequest,
    RuntimeStartResponse,
    RuntimeWaitResponse,
)
from security import require_client_token

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.post("/start", response_model=RuntimeStartResponse)
async def runtime_start(
    req: RuntimeStartRequest,
    client_id: str = Depends(require_client_token),
):
    state = await runtime_kernel.start_run(
        client_id=client_id,
        session_id=req.session_id,
        goal=req.goal,
        max_steps=req.max_steps,
        execution_mode=req.execution_mode,
        context=req.context,
        dry_run=req.dry_run,
    )
    return RuntimeStartResponse(
        accepted=True,
        run_id=state.run_id,
        session_id=state.session_id,
        queued=runtime_kernel.queue_depth(req.session_id),
        status="queued",
    )


@router.get("/wait/{run_id}", response_model=RuntimeWaitResponse)
async def runtime_wait(
    run_id: str,
    timeout_seconds: float = Query(default=90.0, ge=1.0, le=600.0),
    client_id: str = Depends(require_client_token),
):
    _ = client_id
    state = await runtime_kernel.wait_run(run_id=run_id, timeout_seconds=timeout_seconds)
    if not state:
        from datetime import datetime, timezone

        return RuntimeWaitResponse(
            run_id=run_id,
            session_id="",
            status="not_found",
            result={},
            updated_at=datetime.now(timezone.utc),
        )
    if state.status in {"queued", "running"}:
        status = "timeout"
    elif state.status == "dry_run":
        status = "completed"
    else:
        status = state.status
    return RuntimeWaitResponse(
        run_id=state.run_id,
        session_id=state.session_id,
        status=status,
        result=dict(state.result or {}),
        updated_at=state.updated_at,
    )


@router.get("/events/{run_id}", response_model=RuntimeEventsResponse)
async def runtime_events(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    client_id: str = Depends(require_client_token),
):
    _ = client_id
    items = await runtime_event_bus.list_events_async(run_id=run_id, after_seq=after_seq)
    return RuntimeEventsResponse(items=[RuntimeEventItem.model_validate(x) for x in items])


@router.get("/journal/{run_id}", response_model=RuntimeJournalResponse)
async def runtime_journal(
    run_id: str,
    phase: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
    client_id: str = Depends(require_client_token),
):
    _ = client_id
    items = await runtime_kernel.get_run_journal(run_id=run_id, phase=phase, limit=limit)
    if items is None:
        return RuntimeJournalResponse(run_id=run_id, phase=phase, count=0, items=[])
    parsed = [RuntimeJournalItem.model_validate(x) for x in items]
    return RuntimeJournalResponse(
        run_id=run_id,
        phase=phase,
        count=len(parsed),
        items=parsed,
    )


@router.get("/journal/{run_id}/jsonl", response_class=PlainTextResponse)
async def runtime_journal_jsonl(
    run_id: str,
    phase: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
    client_id: str = Depends(require_client_token),
):
    _ = client_id
    items = await runtime_kernel.get_run_journal(run_id=run_id, phase=phase, limit=limit)
    if items is None:
        return ""
    lines = [json.dumps(dict(item), ensure_ascii=True) for item in items if isinstance(item, dict)]
    return "\n".join(lines)


@router.post("/replay/{run_id}", response_model=RuntimeReplayResponse)
async def runtime_replay(
    run_id: str,
    req: RuntimeReplayRequest,
    client_id: str = Depends(require_client_token),
):
    _ = client_id
    try:
        state = await runtime_kernel.replay_run(
            source_run_id=run_id,
            session_id=req.session_id,
            context_patch=req.context_patch,
            dry_run=req.dry_run,
            max_steps=req.max_steps,
            execution_mode=req.execution_mode,
            allow_non_terminal=bool(req.allow_non_terminal),
        )
    except ValueError as exc:
        from fastapi import HTTPException

        detail = str(exc)
        if detail == "source_run_not_found":
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=409, detail=detail) from exc
    return RuntimeReplayResponse(
        accepted=True,
        replay_of=run_id,
        run_id=state.run_id,
        session_id=state.session_id,
        queued=runtime_kernel.queue_depth(state.session_id),
        status="queued",
    )


@router.websocket("/events/ws/{run_id}")
async def runtime_events_ws(websocket: WebSocket, run_id: str):
    await websocket.accept()
    after_seq = 0
    try:
        while True:
            items = await runtime_event_bus.wait_for_events(
                run_id=run_id,
                after_seq=after_seq,
                timeout_seconds=25.0,
            )
            if not items:
                await websocket.send_json({"type": "heartbeat", "run_id": run_id})
                continue
            for item in items:
                after_seq = max(after_seq, int(item.get("seq") or 0))
                await websocket.send_json({"type": "event", "payload": item})
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            return
