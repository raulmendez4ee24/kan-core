from __future__ import annotations

import asyncio
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.policy_engine import load_effective_policy, policy_requires_human_approval_for_action
from brain.tasks.cron import validate_cron
from brain.tasks.evidence_service import decode_image_base64, persist_evidence_png
from execution.desktop_executor import send_to_desktop_agent
from models import (
    DesktopAgent,
    DesktopCommand,
    DesktopCommandResult,
    ExecutionJob,
    Mission,
    MissionStep,
    Task,
    TaskRun,
    TaskRunStep,
)
from recovery.recovery_service import trigger_recovery_for_failed_command
from routes.desktop_agent import _dispatch_command_if_online
from routes.integrations import run_integration_autopilot
from schemas.integration import IntegrationAutopilotRequest


def _enabled() -> bool:
    return os.getenv("ENABLE_TASK_STUDIO", "false").lower() in {"1", "true", "yes"}


def _queue_enabled() -> bool:
    return os.getenv("ENABLE_EXECUTION_QUEUE", "false").lower() in {"1", "true", "yes"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_correlation_id() -> str:
    # short, URL-safe token for logs/correlation; not a secret
    return secrets.token_urlsafe(12)


def _coerce_env(value: Optional[str], *, fallback: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"sandbox", "production"}:
        return token
    return fallback


def _normalize_definition(definition: Any) -> Dict[str, Any]:
    if isinstance(definition, dict):
        return definition
    return {}


def _definition_mode(definition: Dict[str, Any]) -> str:
    token = str(definition.get("mode") or "").strip().lower()
    return token


def _definition_ref_id(definition: Dict[str, Any]) -> Optional[str]:
    ref = definition.get("ref_id")
    if ref is None:
        return None
    token = str(ref).strip()
    return token or None


def _definition_steps(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = definition.get("steps")
    if isinstance(steps, list):
        return [x for x in steps if isinstance(x, dict)]
    return []


def _definition_params(definition: Dict[str, Any]) -> Dict[str, Any]:
    params = definition.get("params")
    return params if isinstance(params, dict) else {}


def _poll_interval_seconds() -> float:
    raw = os.getenv("TASK_ENGINE_POLL_INTERVAL_SECONDS", "0.4")
    try:
        value = float(raw)
    except ValueError:
        value = 0.4
    return max(0.15, min(value, 2.0))


def _default_step_timeout_seconds() -> float:
    raw = os.getenv("TASK_ENGINE_STEP_TIMEOUT_SECONDS", "60")
    try:
        value = float(raw)
    except ValueError:
        value = 60.0
    return max(5.0, min(value, 600.0))


def _recovery_enabled() -> bool:
    return os.getenv("TASK_ENGINE_ENABLE_RECOVERY", "true").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class EnqueueResult:
    run_id: str
    job_id: Optional[str]
    status: str
    environment: str


def validate_task_definition(definition: Dict[str, Any]) -> None:
    mode = _definition_mode(definition)
    if mode not in {
        "playbook",
        "mission",
        "integration_autopilot",
        "desktop_sequence",
        "browser_sequence",
    }:
        raise ValueError("definition.mode must be one of playbook|mission|integration_autopilot|desktop_sequence|browser_sequence")

    if mode in {"playbook", "mission"}:
        if not _definition_ref_id(definition):
            raise ValueError("definition.ref_id is required for mode playbook|mission")

    if mode in {"desktop_sequence", "browser_sequence"}:
        steps = _definition_steps(definition)
        if not steps:
            raise ValueError("definition.steps is required for mode desktop_sequence|browser_sequence")


async def create_task(
    session: AsyncSession,
    *,
    organization_id: UUID,
    title: str,
    description: Optional[str],
    status: str,
    schedule_cron: Optional[str],
    timezone_name: str,
    target_env: str,
    require_sandbox_pass: bool,
    definition: Dict[str, Any],
    capture_evidence: bool,
    created_by_user_id: Optional[UUID],
    metadata: Dict[str, Any],
) -> Task:
    if not _enabled():
        raise ValueError("Task Studio disabled")

    title_token = str(title or "").strip()
    if not title_token:
        raise ValueError("title is required")

    status_token = str(status or "active").strip().lower()
    if status_token not in {"active", "paused", "archived"}:
        raise ValueError("Invalid status")

    schedule = str(schedule_cron).strip() if schedule_cron is not None else None
    if schedule:
        validate_cron(schedule)

    target = _coerce_env(target_env, fallback="production")
    definition_payload = _normalize_definition(definition)
    validate_task_definition(definition_payload)

    row = Task(
        organization_id=organization_id,
        title=title_token,
        description=(str(description) if isinstance(description, str) else None),
        status=status_token,
        schedule_cron=schedule,
        timezone=str(timezone_name or "UTC").strip() or "UTC",
        target_env=target,
        require_sandbox_pass=bool(require_sandbox_pass),
        definition=definition_payload,
        capture_evidence=bool(capture_evidence),
        created_by_user_id=created_by_user_id,
        task_metadata=dict(metadata or {}),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    await write_audit_log(
        session,
        organization_id=organization_id,
        actor_user_id=created_by_user_id,
        action="tasks.create",
        resource="task",
        resource_id=str(row.id),
        status="ok",
        metadata={"title": row.title},
    )

    return row


async def enqueue_task_run(
    session: AsyncSession,
    *,
    organization_id: UUID,
    task_id: UUID,
    triggered_by: str,
    environment: Optional[str],
    actor_user_id: Optional[UUID],
    correlation_id: Optional[str] = None,
    bypass_sandbox_gate: bool = False,
) -> EnqueueResult:
    if not _enabled():
        raise ValueError("Task Studio disabled")
    if not _queue_enabled():
        raise ValueError("Execution queue disabled")

    task = await session.get(Task, task_id)
    if not task or task.organization_id != organization_id:
        raise ValueError("Task not found")
    if task.status not in {"active", "paused"}:
        raise ValueError("Task is archived")

    requested_env = _coerce_env(environment, fallback=str(task.target_env or "production"))
    actual_env = requested_env
    run_result: Dict[str, Any] = {}

    if (
        (not bypass_sandbox_gate)
        and bool(task.require_sandbox_pass)
        and requested_env == "production"
    ):
        actual_env = "sandbox"
        run_result = {"sandbox_first": True, "requested_env": "production"}

    corr = str(correlation_id).strip() if correlation_id else _new_correlation_id()
    now = _now()
    run = TaskRun(
        organization_id=organization_id,
        task_id=task.id,
        status="queued",
        environment=actual_env,
        triggered_by=str(triggered_by or "manual").strip().lower() or "manual",
        requested_by_user_id=actor_user_id,
        correlation_id=corr[:64],
        result=run_result,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    await session.flush()

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
    await session.refresh(run)
    await session.refresh(job)

    await write_audit_log(
        session,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action="tasks.run.enqueue",
        resource="task_run",
        resource_id=str(run.id),
        status="ok",
        metadata={
            "task_id": str(task.id),
            "environment": actual_env,
            "triggered_by": run.triggered_by,
            "correlation_id": run.correlation_id,
            "job_id": str(job.id),
        },
    )

    return EnqueueResult(
        run_id=str(run.id),
        job_id=str(job.id),
        status=run.status,
        environment=run.environment,
    )


async def _pick_agent_for_env(
    session: AsyncSession,
    *,
    organization_id: UUID,
    environment: str,
) -> Optional[UUID]:
    env_token = _coerce_env(environment, fallback="production")
    stmt = (
        select(DesktopAgent.id)
        .where(
            DesktopAgent.organization_id == organization_id,
            DesktopAgent.environment == env_token,
        )
        .order_by(
            desc(DesktopAgent.status == "online"),
            desc(DesktopAgent.last_heartbeat_at),
            desc(DesktopAgent.updated_at),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _ensure_run_steps(
    session: AsyncSession,
    *,
    run: TaskRun,
    task: Task,
) -> List[TaskRunStep]:
    stmt = (
        select(TaskRunStep)
        .where(TaskRunStep.run_id == run.id)
        .order_by(asc(TaskRunStep.step_order))
    )
    existing = list((await session.execute(stmt)).scalars().all())
    if existing:
        return existing

    definition = _normalize_definition(task.definition)
    mode = _definition_mode(definition)
    ref_id = _definition_ref_id(definition)
    params = _definition_params(definition)

    rows: List[TaskRunStep] = []
    order = 0

    if mode == "playbook":
        order += 1
        rows.append(
            TaskRunStep(
                organization_id=run.organization_id,
                run_id=run.id,
                step_order=order,
                kind="playbook",
                title="Ejecutar playbook",
                action="playbook.run",
                args={"playbook_id": ref_id, "params": params},
                status="pending",
                attempts=0,
                result={},
                reasoning="Ejecutar la mision definida como playbook.",
                step_metadata={},
            )
        )

    elif mode == "mission":
        order += 1
        rows.append(
            TaskRunStep(
                organization_id=run.organization_id,
                run_id=run.id,
                step_order=order,
                kind="mission_execute",
                title="Ejecutar mission",
                action="mission.execute",
                args={"mission_id": ref_id, "params": params},
                status="pending",
                attempts=0,
                result={},
                reasoning="Ejecutar la mision multi-paso del sistema.",
                step_metadata={},
            )
        )

    elif mode == "integration_autopilot":
        order += 1
        rows.append(
            TaskRunStep(
                organization_id=run.organization_id,
                run_id=run.id,
                step_order=order,
                kind="integration",
                title="Autopilot de integraciones",
                action="integrations.autopilot",
                args={"params": params},
                status="pending",
                attempts=0,
                result={},
                reasoning="Crear/activar integraciones y workflows automaticamente.",
                step_metadata={},
            )
        )

    elif mode in {"desktop_sequence", "browser_sequence"}:
        for item in _definition_steps(definition):
            action = str(item.get("action") or "").strip()
            if not action:
                continue
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            title = str(item.get("title") or action).strip() or action
            reasoning = item.get("reasoning")
            order += 1
            rows.append(
                TaskRunStep(
                    organization_id=run.organization_id,
                    run_id=run.id,
                    step_order=order,
                    kind=("browser_command" if mode == "browser_sequence" else "desktop_command"),
                    title=title[:255],
                    action=action,
                    args=dict(args),
                    status="pending",
                    attempts=0,
                    result={},
                    reasoning=(str(reasoning) if isinstance(reasoning, str) else None),
                    step_metadata={
                        "definition_mode": mode,
                    },
                )
            )

    if not rows:
        raise ValueError("Task has no executable steps")

    for row in rows:
        session.add(row)
    await session.commit()

    stmt = (
        select(TaskRunStep)
        .where(TaskRunStep.run_id == run.id)
        .order_by(asc(TaskRunStep.step_order))
    )
    return list((await session.execute(stmt)).scalars().all())


async def _wait_for_command_result(
    session: AsyncSession,
    *,
    command_id: UUID,
    timeout_seconds: float,
) -> Optional[DesktopCommandResult]:
    started = asyncio.get_event_loop().time()
    poll = _poll_interval_seconds()
    while True:
        stmt = select(DesktopCommandResult).where(DesktopCommandResult.command_id == command_id).limit(1)
        row = (await session.execute(stmt)).scalars().first()
        if row:
            return row
        if asyncio.get_event_loop().time() - started >= timeout_seconds:
            return None
        await asyncio.sleep(poll)


async def _dispatch_if_possible(session: AsyncSession, *, command_id: UUID) -> None:
    cmd = (await session.execute(select(DesktopCommand).where(DesktopCommand.id == command_id))).scalars().first()
    if not cmd:
        return
    await _dispatch_command_if_online(session, command=cmd)


async def _capture_screenshot_asset(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
    run_id: UUID,
    step_id: UUID,
    kind: str,
    actor_user_id: Optional[UUID],
    correlation_id: Optional[str],
) -> Optional[UUID]:
    # Best-effort only; never block execution if evidence cannot be captured.
    enqueue = await send_to_desktop_agent(
        client_id=str(organization_id),
        action="take_screenshot",
        parameters={"agent_id": str(agent_id)},
        metadata={
            "source": "task_engine_evidence",
            "task_run_id": str(run_id),
            "task_step_id": str(step_id),
            "correlation_id": correlation_id,
        },
        requested_by_user_id=(str(actor_user_id) if actor_user_id else None),
    )
    if not enqueue.get("accepted"):
        return None

    command_id_raw = enqueue.get("command_id")
    status = str(enqueue.get("status") or "")
    try:
        command_uuid = UUID(str(command_id_raw))
    except Exception:
        return None

    if status in {"approved", "sent", "received"}:
        await _dispatch_if_possible(session, command_id=command_uuid)

    if status == "pending_approval":
        return None

    timeout = _default_step_timeout_seconds()
    final = await _wait_for_command_result(session, command_id=command_uuid, timeout_seconds=timeout)
    if not final or final.status != "success":
        return None

    png = decode_image_base64(final.data)
    if not png:
        return None

    asset = await persist_evidence_png(
        session,
        organization_id=organization_id,
        run_id=run_id,
        step_id=step_id,
        kind=kind,
        png_bytes=png,
    )
    return asset.id if asset else None


async def _execute_desktop_action_step(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
    step: TaskRunStep,
    actor_user_id: Optional[UUID],
    correlation_id: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    action = str(step.action or "").strip().lower()
    args = dict(step.args or {})

    policy = await load_effective_policy(session, organization_id=organization_id)
    require_approval = policy_requires_human_approval_for_action(action=action, args=args, policy=policy)

    enqueue = await send_to_desktop_agent(
        client_id=str(organization_id),
        action=action,
        parameters={**args, "agent_id": str(agent_id)},
        metadata={
            "source": "task_engine",
            # Task Studio runs manage recovery synchronously; avoid background recovery duplication.
            "recovery_control": "managed",
            "task_run_id": str(step.run_id),
            "task_step_id": str(step.id),
            "correlation_id": correlation_id,
            "force_human_approval": bool(require_approval),
        },
        requested_by_user_id=(str(actor_user_id) if actor_user_id else None),
    )
    if not enqueue.get("accepted"):
        return "failed", {"error": enqueue.get("reason"), "detail": enqueue.get("detail")}

    command_id_raw = enqueue.get("command_id")
    status = str(enqueue.get("status") or "")
    approval_required = bool(enqueue.get("approval_required", False))
    risk_level = enqueue.get("risk_level")

    result_payload: Dict[str, Any] = {
        "command_id": command_id_raw,
        "enqueue_status": status,
        "approval_required": approval_required,
        "risk_level": risk_level,
    }

    if status == "pending_approval":
        return "paused_requires_approval", result_payload

    try:
        command_uuid = UUID(str(command_id_raw))
    except Exception:
        return "failed", {**result_payload, "error": "invalid_command_id"}

    await _dispatch_if_possible(session, command_id=command_uuid)

    timeout = _default_step_timeout_seconds()
    final = await _wait_for_command_result(session, command_id=command_uuid, timeout_seconds=timeout)
    if not final:
        return "failed", {**result_payload, "error": "timeout_waiting_for_result"}
    if final.status == "success":
        return "success", {**result_payload, "duration_ms": final.duration_ms, "data": final.data}

    # Failed: attempt recovery if policy allows.
    recovered = False
    recovery: Optional[Dict[str, Any]] = None
    if _recovery_enabled() and policy.allow_workflow_auto_recovery():
        retry = await trigger_recovery_for_failed_command(
            organization_id=str(organization_id),
            failed_command_id=str(command_uuid),
            dispatch_command=_dispatch_command_if_online,
        )
        recovered = bool(retry.success)
        recovery = {
            "success": bool(retry.success),
            "attempts_used": int(retry.attempts_used or 0),
            "final_reason": retry.final_reason,
            "final_strategy": retry.final_strategy,
        }

    if recovered:
        return "recovered", {**result_payload, "duration_ms": final.duration_ms, "error": final.error, "recovery": recovery}

    return "failed", {**result_payload, "duration_ms": final.duration_ms, "error": final.error, "data": final.data, "recovery": recovery}


async def _execute_playbook_step(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
    step: TaskRunStep,
    actor_user_id: Optional[UUID],
) -> Tuple[str, Dict[str, Any]]:
    ref_id = step.args.get("playbook_id") if isinstance(step.args, dict) else None
    try:
        playbook_id = UUID(str(ref_id))
    except Exception:
        return "failed", {"error": "invalid_playbook_id"}

    from brain.workflow_recorder.executor import run_playbook

    result = await run_playbook(
        session,
        organization_id=organization_id,
        playbook_id=playbook_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        dry_run=False,
    )
    if result.requires_approval:
        return "paused_requires_approval", {
            "workflow_playbook_run_id": str(result.run.id),
            "pending_command_id": result.pending_command_id,
        }
    if result.run.status == "completed":
        return "success", {"workflow_playbook_run_id": str(result.run.id), "result": result.run.result}
    return "failed", {"workflow_playbook_run_id": str(result.run.id), "result": result.run.result}


async def _execute_mission_step(
    session: AsyncSession,
    *,
    organization_id: UUID,
    step: TaskRunStep,
    actor_user_id: Optional[UUID],
) -> Tuple[str, Dict[str, Any]]:
    ref_id = step.args.get("mission_id") if isinstance(step.args, dict) else None
    try:
        mission_id = UUID(str(ref_id))
    except Exception:
        return "failed", {"error": "invalid_mission_id"}

    mission = (await session.execute(select(Mission).where(Mission.organization_id == organization_id, Mission.id == mission_id))).scalars().first()
    if not mission:
        return "failed", {"error": "mission_not_found"}

    steps_stmt = (
        select(MissionStep)
        .where(MissionStep.mission_id == mission.id)
        .order_by(asc(MissionStep.step_order))
    )
    mission_steps = list((await session.execute(steps_stmt)).scalars().all())
    if not mission_steps:
        return "failed", {"error": "mission_has_no_steps"}

    mission.status = "running"
    await session.commit()

    executed = 0
    results: List[Dict[str, Any]] = []
    for mstep in mission_steps:
        mstep.started_at = _now()
        mstep.status = "running"
        await session.commit()

        desired = mstep.desired_functions or {}
        desired_functions = desired.get("items", []) if isinstance(desired, dict) else desired
        request_text = mstep.request_text or mission.title
        autopilot_req = IntegrationAutopilotRequest(
            request_text=request_text,
            desired_functions=[str(x) for x in (desired_functions or [])],
            dry_run=False,
            auto_generate_workflow=True,
            continue_on_error=True,
            enable_provider_fallback=True,
            auto_repair_max_attempts=2,
        )
        try:
            resp = await run_integration_autopilot(req=autopilot_req, client_id=str(organization_id), session=session)
            mstep.result = resp.model_dump()
            statuses = {item.status for item in resp.results}
            mstep.status = (
                "completed"
                if resp.results and not statuses.issubset({"failed", "manual_action_required"})
                else "failed"
            )
        except Exception as exc:
            mstep.status = "failed"
            mstep.result = {"error": str(exc)}

        mstep.completed_at = _now()
        await session.commit()
        executed += 1
        results.append(
            {
                "step_order": mstep.step_order,
                "status": mstep.status,
                "title": mstep.title,
                "result": mstep.result,
            }
        )

    mission.status = "completed" if all(s.status == "completed" for s in mission_steps) else "failed"
    await session.commit()
    return ("success" if mission.status == "completed" else "failed"), {"executed_steps": executed, "steps": results}


async def _execute_integration_autopilot_step(
    session: AsyncSession,
    *,
    organization_id: UUID,
    step: TaskRunStep,
) -> Tuple[str, Dict[str, Any]]:
    params = step.args.get("params") if isinstance(step.args, dict) else None
    params = params if isinstance(params, dict) else {}
    request_text = str(params.get("request_text") or "").strip()
    desired_functions = params.get("desired_functions")
    desired = [str(x) for x in (desired_functions or []) if str(x).strip()] if isinstance(desired_functions, list) else []
    if not request_text:
        request_text = "Activar integraciones necesarias para el negocio."

    autopilot_req = IntegrationAutopilotRequest(
        request_text=request_text,
        desired_functions=desired,
        dry_run=False,
        auto_generate_workflow=True,
        continue_on_error=True,
        enable_provider_fallback=True,
        auto_repair_max_attempts=2,
    )
    try:
        resp = await run_integration_autopilot(req=autopilot_req, client_id=str(organization_id), session=session)
        statuses = {item.status for item in resp.results}
        ok = bool(resp.results) and not statuses.issubset({"failed", "manual_action_required"})
        return ("success" if ok else "failed"), {"result": resp.model_dump()}
    except Exception as exc:
        return "failed", {"error": str(exc)}


async def execute_task_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    worker_id: str,
) -> TaskRun:
    if not _enabled():
        raise ValueError("Task Studio disabled")

    run = await session.get(TaskRun, run_id)
    if not run:
        raise ValueError("TaskRun not found")

    if run.status in {"completed", "failed", "cancelled"}:
        return run

    task = await session.get(Task, run.task_id)
    if not task or task.organization_id != run.organization_id:
        run.status = "failed"
        run.error = "Task not found"
        run.finished_at = _now()
        await session.commit()
        return run

    if task.schedule_cron:
        try:
            validate_cron(task.schedule_cron)
        except Exception:
            pass

    if run.status == "paused":
        # Resume.
        run.status = "running"
    elif run.status == "queued":
        run.status = "running"

    if not run.started_at:
        run.started_at = _now()
    await session.commit()

    agent_id = await _pick_agent_for_env(session, organization_id=run.organization_id, environment=run.environment)

    steps = await _ensure_run_steps(session, run=run, task=task)

    correlation_id = run.correlation_id

    for step in steps:
        # Refresh run status for pause/cancel.
        await session.refresh(run)
        if run.status in {"cancelled", "failed", "completed"}:
            break
        if run.status == "paused":
            break

        if step.status in {"completed", "skipped"}:
            continue

        # If a previous attempt paused on approval, check for completion.
        if step.status == "paused":
            pending = step.result.get("command_id") if isinstance(step.result, dict) else None
            if pending:
                try:
                    cmd_uuid = UUID(str(pending))
                except Exception:
                    cmd_uuid = None
                if cmd_uuid:
                    final = await _wait_for_command_result(
                        session,
                        command_id=cmd_uuid,
                        timeout_seconds=0.1,
                    )
                    if final and final.status == "success":
                        step.status = "completed"
                        step.completed_at = _now()
                        step.result = {**dict(step.result or {}), "final": {"status": final.status, "data": final.data}}
                        await session.commit()
                        continue

            # Still paused.
            run.status = "paused"
            await session.commit()
            break

        step.status = "running"
        step.attempts = int(step.attempts or 0) + 1
        step.started_at = _now()
        await session.commit()

        if task.capture_evidence and agent_id:
            before_id = await _capture_screenshot_asset(
                session,
                organization_id=run.organization_id,
                agent_id=agent_id,
                run_id=run.id,
                step_id=step.id,
                kind="screenshot_before",
                actor_user_id=run.requested_by_user_id,
                correlation_id=correlation_id,
            )
            if before_id:
                step.evidence_before_asset_id = before_id
                await session.commit()

        outcome_status = "failed"
        outcome_payload: Dict[str, Any] = {}

        try:
            if step.kind in {"desktop_command", "browser_command"}:
                if not agent_id:
                    outcome_status = "failed"
                    outcome_payload = {"error": "no_agent_available", "environment": run.environment}
                else:
                    outcome_status, outcome_payload = await _execute_desktop_action_step(
                        session,
                        organization_id=run.organization_id,
                        agent_id=agent_id,
                        step=step,
                        actor_user_id=run.requested_by_user_id,
                        correlation_id=correlation_id,
                    )
            elif step.kind == "playbook":
                if not agent_id:
                    outcome_status = "failed"
                    outcome_payload = {"error": "no_agent_available", "environment": run.environment}
                else:
                    outcome_status, outcome_payload = await _execute_playbook_step(
                        session,
                        organization_id=run.organization_id,
                        agent_id=agent_id,
                        step=step,
                        actor_user_id=run.requested_by_user_id,
                    )
            elif step.kind == "mission_execute":
                outcome_status, outcome_payload = await _execute_mission_step(
                    session,
                    organization_id=run.organization_id,
                    step=step,
                    actor_user_id=run.requested_by_user_id,
                )
            elif step.kind == "integration":
                outcome_status, outcome_payload = await _execute_integration_autopilot_step(
                    session,
                    organization_id=run.organization_id,
                    step=step,
                )
            elif step.kind == "wait":
                seconds = step.args.get("seconds") if isinstance(step.args, dict) else None
                try:
                    wait_s = float(seconds)
                except Exception:
                    wait_s = 1.0
                await asyncio.sleep(max(0.0, min(wait_s, 60.0)))
                outcome_status, outcome_payload = "success", {"wait_seconds": wait_s}
            else:
                outcome_status, outcome_payload = "failed", {"error": f"unsupported_step_kind:{step.kind}"}
        except Exception as exc:
            outcome_status, outcome_payload = "failed", {"error": str(exc)}

        if outcome_status == "paused_requires_approval":
            step.status = "paused"
            step.result = outcome_payload
            run.status = "paused"
            await session.commit()
            break

        if task.capture_evidence and agent_id:
            after_id = await _capture_screenshot_asset(
                session,
                organization_id=run.organization_id,
                agent_id=agent_id,
                run_id=run.id,
                step_id=step.id,
                kind="screenshot_after",
                actor_user_id=run.requested_by_user_id,
                correlation_id=correlation_id,
            )
            if after_id:
                step.evidence_after_asset_id = after_id

        if outcome_status in {"success", "recovered"}:
            step.status = "completed"
            step.result = outcome_payload
            step.completed_at = _now()
            await session.commit()
            continue

        step.status = "failed"
        step.result = outcome_payload
        step.completed_at = _now()
        await session.commit()

        run.status = "failed"
        run.error = str(outcome_payload.get("error") or "step_failed")
        run.finished_at = _now()
        await session.commit()
        break

    # Finalize.
    await session.refresh(run)
    if run.status == "running":
        stmt = select(TaskRunStep.status).where(TaskRunStep.run_id == run.id)
        statuses = set((await session.execute(stmt)).scalars().all())
        if statuses and statuses.issubset({"completed", "skipped"}):
            run.status = "completed"
            run.finished_at = _now()
            run.summary = "Task run completed"
            await session.commit()

    # Sandbox-first auto-enqueue of production after successful sandbox.
    await session.refresh(run)
    if run.status == "completed" and isinstance(run.result, dict) and run.result.get("sandbox_first"):
        requested = str(run.result.get("requested_env") or "").strip().lower()
        if requested == "production":
            try:
                _ = await enqueue_task_run(
                    session,
                    organization_id=run.organization_id,
                    task_id=run.task_id,
                    triggered_by=run.triggered_by,
                    environment="production",
                    actor_user_id=run.requested_by_user_id,
                    correlation_id=f"{run.correlation_id}-prod",
                    bypass_sandbox_gate=True,
                )
            except Exception:
                pass

    return run
