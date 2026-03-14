import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.tasks.task_engine import create_task as engine_create_task
from brain.tasks.task_engine import enqueue_task_run
from brain.tasks.task_engine import validate_task_definition
from brain.tasks.cron import validate_cron
from database import get_session
from models import ExecutionJob, Task, TaskRun, TaskRunStep
from schemas.tasks import (
    TaskCreateRequest,
    TaskListResponse,
    TaskPatchRequest,
    TaskRead,
    TaskRunControlResponse,
    TaskRunEnqueueRequest,
    TaskRunRead,
    TaskRunStatus,
    TaskRunStepsResponse,
    TaskRunStepRead,
)
from security import require_client_token

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _enabled() -> bool:
    return os.getenv("ENABLE_TASK_STUDIO", "false").lower() in {"1", "true", "yes"}


def _queue_enabled() -> bool:
    return os.getenv("ENABLE_EXECUTION_QUEUE", "false").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


def _to_task_read(row: Task) -> TaskRead:
    return TaskRead(
        id=str(row.id),
        organization_id=str(row.organization_id),
        title=row.title,
        description=row.description,
        status=row.status,  # type: ignore[arg-type]
        schedule_cron=row.schedule_cron,
        timezone=row.timezone,
        target_env=row.target_env,  # type: ignore[arg-type]
        require_sandbox_pass=bool(row.require_sandbox_pass),
        definition=row.definition or {},
        capture_evidence=bool(row.capture_evidence),
        created_by_user_id=str(row.created_by_user_id) if row.created_by_user_id else None,
        metadata=row.task_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_run_read(row: TaskRun) -> TaskRunRead:
    return TaskRunRead(
        id=str(row.id),
        organization_id=str(row.organization_id),
        task_id=str(row.task_id),
        status=row.status,  # type: ignore[arg-type]
        environment=row.environment,  # type: ignore[arg-type]
        triggered_by=row.triggered_by,  # type: ignore[arg-type]
        requested_by_user_id=str(row.requested_by_user_id) if row.requested_by_user_id else None,
        correlation_id=row.correlation_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        summary=row.summary,
        error=row.error,
        result=row.result or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_step_read(row: TaskRunStep) -> TaskRunStepRead:
    return TaskRunStepRead(
        id=str(row.id),
        organization_id=str(row.organization_id),
        run_id=str(row.run_id),
        step_order=int(row.step_order or 0),
        kind=row.kind,  # type: ignore[arg-type]
        title=row.title,
        action=row.action,
        args=row.args or {},
        status=row.status,  # type: ignore[arg-type]
        attempts=int(row.attempts or 0),
        started_at=row.started_at,
        completed_at=row.completed_at,
        result=row.result or {},
        reasoning=row.reasoning,
        evidence_before_asset_id=str(row.evidence_before_asset_id) if row.evidence_before_asset_id else None,
        evidence_after_asset_id=str(row.evidence_after_asset_id) if row.evidence_after_asset_id else None,
        metadata=row.step_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _enqueue_existing_run(
    session: AsyncSession,
    *,
    organization_id: UUID,
    run: TaskRun,
    actor_user_id: Optional[UUID],
) -> ExecutionJob:
    now = datetime.now(timezone.utc)
    job = ExecutionJob(
        organization_id=organization_id,
        kind="task_run",
        payload={"run_id": str(run.id)},
        status="queued",
        run_after=now,
        attempts=0,
        max_attempts=3,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    await write_audit_log(
        session,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action="tasks.run.reenqueue",
        resource="execution_job",
        resource_id=str(job.id),
        status="ok",
        metadata={"run_id": str(run.id)},
    )
    return job


@router.post("", response_model=TaskRead)
async def create_task(
    req: TaskCreateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    created_by = _parse_uuid(req.created_by_user_id, field="created_by_user_id") if req.created_by_user_id else None
    try:
        row = await engine_create_task(
            session,
            organization_id=org_id,
            title=req.title,
            description=req.description,
            status=req.status,
            schedule_cron=req.schedule_cron,
            timezone_name=req.timezone,
            target_env=req.target_env,
            require_sandbox_pass=req.require_sandbox_pass,
            definition=req.definition.model_dump(),
            capture_evidence=req.capture_evidence,
            created_by_user_id=created_by,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _to_task_read(row)


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    stmt = select(Task).where(Task.organization_id == org_id).order_by(desc(Task.updated_at))
    rows = list((await session.execute(stmt)).scalars().all())
    return TaskListResponse(items=[_to_task_read(row) for row in rows])


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    task_uuid = _parse_uuid(task_id, field="task_id")
    row = (await session.execute(select(Task).where(Task.organization_id == org_id, Task.id == task_uuid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return _to_task_read(row)


@router.patch("/{task_id}", response_model=TaskRead)
async def patch_task(
    task_id: str,
    req: TaskPatchRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    task_uuid = _parse_uuid(task_id, field="task_id")
    row = (await session.execute(select(Task).where(Task.organization_id == org_id, Task.id == task_uuid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")

    payload = req.model_dump(exclude_unset=True)
    if "title" in payload and payload["title"] is not None:
        row.title = str(payload["title"]).strip()
    if "description" in payload:
        row.description = payload["description"]
    if "status" in payload and payload["status"] is not None:
        token = str(payload["status"]).strip().lower()
        if token not in {"active", "paused", "archived"}:
            raise HTTPException(status_code=400, detail="Invalid status")
        row.status = token
    if "schedule_cron" in payload:
        cron = payload["schedule_cron"]
        if cron is not None and str(cron).strip():
            try:
                validate_cron(str(cron))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        row.schedule_cron = cron
    if "timezone" in payload and payload["timezone"] is not None:
        row.timezone = str(payload["timezone"]).strip() or "UTC"
    if "target_env" in payload and payload["target_env"] is not None:
        token = str(payload["target_env"]).strip().lower()
        if token not in {"sandbox", "production"}:
            raise HTTPException(status_code=400, detail="Invalid target_env")
        row.target_env = token
    if "require_sandbox_pass" in payload and payload["require_sandbox_pass"] is not None:
        row.require_sandbox_pass = bool(payload["require_sandbox_pass"])
    if "definition" in payload and payload["definition"] is not None:
        definition = dict(payload["definition"])
        try:
            validate_task_definition(definition)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        row.definition = definition
    if "capture_evidence" in payload and payload["capture_evidence"] is not None:
        row.capture_evidence = bool(payload["capture_evidence"])
    if "metadata" in payload and payload["metadata"] is not None:
        row.task_metadata = dict(payload["metadata"])

    await session.commit()
    await session.refresh(row)
    return _to_task_read(row)


@router.post("/{task_id}/run", response_model=Dict[str, Any])
async def run_task(
    task_id: str,
    req: TaskRunEnqueueRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")
    if not _queue_enabled():
        raise HTTPException(status_code=400, detail="Feature ENABLE_EXECUTION_QUEUE disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    task_uuid = _parse_uuid(task_id, field="task_id")
    try:
        result = await enqueue_task_run(
            session,
            organization_id=org_id,
            task_id=task_uuid,
            triggered_by=req.triggered_by,
            environment=req.environment,
            actor_user_id=None,
            correlation_id=req.correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"run_id": result.run_id, "job_id": result.job_id, "status": result.status, "environment": result.environment}


@router.get("/runs/{run_id}", response_model=TaskRunRead)
async def get_task_run(
    run_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    run_uuid = _parse_uuid(run_id, field="run_id")
    row = (await session.execute(select(TaskRun).where(TaskRun.organization_id == org_id, TaskRun.id == run_uuid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="TaskRun not found")
    return _to_run_read(row)


@router.get("/runs", response_model=list[TaskRunRead])
async def list_task_runs(
    task_id: Optional[str] = None,
    limit: int = 50,
    status: Optional[TaskRunStatus] = None,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    stmt = select(TaskRun).where(TaskRun.organization_id == org_id)
    if task_id:
        stmt = stmt.where(TaskRun.task_id == _parse_uuid(task_id, field="task_id"))
    if status:
        stmt = stmt.where(TaskRun.status == str(status))
    stmt = stmt.order_by(desc(TaskRun.created_at)).limit(max(1, min(int(limit), 200)))
    rows = list((await session.execute(stmt)).scalars().all())
    return [_to_run_read(row) for row in rows]


@router.get("/runs/{run_id}/steps", response_model=TaskRunStepsResponse)
async def get_task_run_steps(
    run_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    run_uuid = _parse_uuid(run_id, field="run_id")
    stmt = (
        select(TaskRunStep)
        .where(TaskRunStep.organization_id == org_id, TaskRunStep.run_id == run_uuid)
        .order_by(asc(TaskRunStep.step_order))
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return TaskRunStepsResponse(items=[_to_step_read(row) for row in rows])


@router.post("/runs/{run_id}/pause", response_model=TaskRunControlResponse)
async def pause_task_run(
    run_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    run_uuid = _parse_uuid(run_id, field="run_id")
    row = (await session.execute(select(TaskRun).where(TaskRun.organization_id == org_id, TaskRun.id == run_uuid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="TaskRun not found")
    if row.status not in {"queued", "running"}:
        return TaskRunControlResponse(run_id=str(row.id), status=row.status)  # type: ignore[arg-type]
    row.status = "paused"
    await session.commit()
    return TaskRunControlResponse(run_id=str(row.id), status=row.status)  # type: ignore[arg-type]


@router.post("/runs/{run_id}/resume", response_model=TaskRunControlResponse)
async def resume_task_run(
    run_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")
    if not _queue_enabled():
        raise HTTPException(status_code=400, detail="Feature ENABLE_EXECUTION_QUEUE disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    run_uuid = _parse_uuid(run_id, field="run_id")
    row = (await session.execute(select(TaskRun).where(TaskRun.organization_id == org_id, TaskRun.id == run_uuid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="TaskRun not found")
    if row.status not in {"paused", "queued"}:
        return TaskRunControlResponse(run_id=str(row.id), status=row.status)  # type: ignore[arg-type]
    row.status = "queued"
    await session.commit()
    await _enqueue_existing_run(session, organization_id=org_id, run=row, actor_user_id=None)
    return TaskRunControlResponse(run_id=str(row.id), status=row.status)  # type: ignore[arg-type]


@router.post("/runs/{run_id}/cancel", response_model=TaskRunControlResponse)
async def cancel_task_run(
    run_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TASK_STUDIO disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    run_uuid = _parse_uuid(run_id, field="run_id")
    row = (await session.execute(select(TaskRun).where(TaskRun.organization_id == org_id, TaskRun.id == run_uuid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="TaskRun not found")
    if row.status in {"completed", "failed", "cancelled"}:
        return TaskRunControlResponse(run_id=str(row.id), status=row.status)  # type: ignore[arg-type]
    row.status = "cancelled"
    await session.commit()
    return TaskRunControlResponse(run_id=str(row.id), status=row.status)  # type: ignore[arg-type]
