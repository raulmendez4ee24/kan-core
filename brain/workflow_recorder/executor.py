import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.policy_engine import EffectivePolicy, load_effective_policy
from execution.desktop_executor import send_to_desktop_agent
from models import (
    DesktopCommand,
    DesktopCommandResult,
    DesktopAgent,
    WorkflowPlaybook,
    WorkflowPlaybookRun,
)
from recovery.recovery_service import trigger_recovery_for_failed_command
from routes.desktop_agent import _dispatch_command_if_online


def _enabled() -> bool:
    return os.getenv("ENABLE_WORKFLOW_RECORDER", "false").lower() in {"1", "true", "yes"}


def _wait_timeout_seconds() -> float:
    raw = os.getenv("WORKFLOW_PLAYBOOK_STEP_WAIT_TIMEOUT_SECONDS", "40")
    try:
        value = float(raw)
    except ValueError:
        value = 40.0
    return max(5.0, min(value, 180.0))


def _recovery_enabled() -> bool:
    return os.getenv("WORKFLOW_PLAYBOOK_ENABLE_RECOVERY", "true").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class PlaybookRunResult:
    run: WorkflowPlaybookRun
    requires_approval: bool
    pending_command_id: Optional[str]


async def _ensure_agent(session: AsyncSession, *, organization_id: UUID, agent_id: UUID) -> None:
    stmt = select(DesktopAgent.id).where(
        DesktopAgent.organization_id == organization_id,
        DesktopAgent.id == agent_id,
    )
    if not (await session.execute(stmt)).scalars().first():
        raise ValueError("Agent not found")


async def _load_playbook(
    session: AsyncSession,
    *,
    organization_id: UUID,
    playbook_id: UUID,
) -> WorkflowPlaybook:
    stmt = select(WorkflowPlaybook).where(
        WorkflowPlaybook.organization_id == organization_id,
        WorkflowPlaybook.id == playbook_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        raise ValueError("Playbook not found")
    return row


def _items(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        items = value.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return []


async def _wait_for_result(
    *,
    session: AsyncSession,
    command_id: UUID,
    timeout_seconds: float,
) -> Optional[DesktopCommandResult]:
    started = asyncio.get_event_loop().time()
    while True:
        stmt = select(DesktopCommandResult).where(DesktopCommandResult.command_id == command_id).limit(1)
        row = (await session.execute(stmt)).scalars().first()
        if row:
            return row
        if asyncio.get_event_loop().time() - started >= timeout_seconds:
            return None
        await asyncio.sleep(0.4)


def _policy_snapshot_from_playbook(playbook: WorkflowPlaybook) -> Dict[str, Any]:
    return playbook.policy_snapshot if isinstance(playbook.policy_snapshot, dict) else {}


async def run_playbook(
    session: AsyncSession,
    *,
    organization_id: UUID,
    playbook_id: UUID,
    agent_id: UUID,
    actor_user_id: Optional[UUID],
    dry_run: bool,
) -> PlaybookRunResult:
    if not _enabled():
        raise ValueError("Workflow recorder disabled")

    await _ensure_agent(session, organization_id=organization_id, agent_id=agent_id)
    playbook = await _load_playbook(session, organization_id=organization_id, playbook_id=playbook_id)
    steps = _items(playbook.steps)

    run = WorkflowPlaybookRun(
        organization_id=organization_id,
        playbook_id=playbook.id,
        agent_id=agent_id,
        status="running",
        current_step=0,
        result={"dry_run": bool(dry_run), "steps": []},
        created_by_user_id=actor_user_id,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    if dry_run:
        run.status = "completed"
        run.result = {
            "dry_run": True,
            "planned_steps": [
                {"step": idx + 1, "action": item.get("action"), "args": item.get("args", {})}
                for idx, item in enumerate(steps)
            ],
        }
        await session.commit()
        await session.refresh(run)
        return PlaybookRunResult(run=run, requires_approval=False, pending_command_id=None)

    policy = await load_effective_policy(session, organization_id=organization_id)
    timeout_seconds = _wait_timeout_seconds()
    step_results: List[Dict[str, Any]] = []

    for idx, item in enumerate(steps):
        run.current_step = idx + 1
        await session.commit()

        action = str(item.get("action") or "").strip()
        args = item.get("args") if isinstance(item.get("args"), dict) else {}

        # Expand compiler-level macros.
        sub_actions: List[Tuple[str, Dict[str, Any]]] = []
        if action == "type_text_sequence":
            seq = args.get("sequence")
            interval = args.get("interval")
            if isinstance(seq, list):
                for text in seq:
                    if isinstance(text, str) and text:
                        payload = {"text": text}
                        if interval is not None:
                            payload["interval"] = interval
                        sub_actions.append(("type_text", payload))
        else:
            sub_actions.append((action, args))

        for sub_idx, (sub_action, sub_args) in enumerate(sub_actions):
            result_item: Dict[str, Any] = {
                "step": idx + 1,
                "sub_step": sub_idx + 1,
                "action": sub_action,
                "args": sub_args,
                "status": "pending",
            }

            # Policy guardrails (best-effort; main validation still happens in desktop-agent layer).
            if _recovery_enabled() and isinstance(policy, EffectivePolicy):
                pass

            enqueue = await send_to_desktop_agent(
                client_id=str(organization_id),
                action=sub_action,
                parameters={**dict(sub_args), "agent_id": str(agent_id)},
                metadata={
                    "source": "workflow_playbook",
                    # The playbook executor manages recovery itself; avoid background recovery duplication.
                    "recovery_control": "managed",
                    "playbook_id": str(playbook.id),
                    "playbook_run_id": str(run.id),
                    "step": idx + 1,
                    "sub_step": sub_idx + 1,
                    "policy_snapshot": _policy_snapshot_from_playbook(playbook),
                },
                requested_by_user_id=(str(actor_user_id) if actor_user_id else None),
            )

            if not enqueue.get("accepted"):
                result_item.update({"status": "failed", "error": enqueue.get("reason"), "detail": enqueue.get("detail")})
                step_results.append(result_item)
                run.status = "failed"
                run.result = {"steps": step_results, "failed_step": idx + 1, "reason": enqueue.get("reason")}
                await session.commit()
                await session.refresh(run)
                return PlaybookRunResult(run=run, requires_approval=False, pending_command_id=None)

            command_id = enqueue.get("command_id")
            result_item["command_id"] = command_id
            status = str(enqueue.get("status") or "")
            approval_required = bool(enqueue.get("approval_required", False))
            result_item["approval_required"] = approval_required
            result_item["risk_level"] = enqueue.get("risk_level")

            if status == "pending_approval":
                result_item["status"] = "paused_requires_approval"
                step_results.append(result_item)
                run.status = "paused_requires_approval"
                run.result = {
                    "steps": step_results,
                    "pending_command_id": command_id,
                    "pending_step": idx + 1,
                }
                await session.commit()
                await session.refresh(run)
                return PlaybookRunResult(run=run, requires_approval=True, pending_command_id=str(command_id) if command_id else None)

            # If approved, try dispatch immediately (otherwise heartbeat backlog will).
            try:
                cmd_uuid = UUID(str(command_id))
            except Exception:
                cmd_uuid = None
            if cmd_uuid:
                cmd = (await session.execute(select(DesktopCommand).where(DesktopCommand.id == cmd_uuid))).scalars().first()
                if cmd:
                    await _dispatch_command_if_online(session, command=cmd)

            # Wait for command result.
            final = None
            if cmd_uuid:
                final = await _wait_for_result(session=session, command_id=cmd_uuid, timeout_seconds=timeout_seconds)
            if not final:
                result_item["status"] = "failed"
                result_item["error"] = "timeout_waiting_for_result"
                step_results.append(result_item)
                run.status = "failed"
                run.result = {"steps": step_results, "failed_step": idx + 1, "reason": "timeout_waiting_for_result"}
                await session.commit()
                await session.refresh(run)
                return PlaybookRunResult(run=run, requires_approval=False, pending_command_id=None)

            result_item["duration_ms"] = final.duration_ms
            if final.status == "success":
                result_item["status"] = "success"
                step_results.append(result_item)
                continue

            # Failed: optional recovery.
            result_item["status"] = "failed"
            result_item["error"] = final.error

            recovered = False
            if _recovery_enabled() and policy.allow_workflow_auto_recovery():
                retry = await trigger_recovery_for_failed_command(
                    organization_id=str(organization_id),
                    failed_command_id=str(cmd_uuid),
                    dispatch_command=_dispatch_command_if_online,
                )
                result_item["recovery"] = {
                    "success": bool(retry.success),
                    "attempts_used": int(retry.attempts_used or 0),
                    "final_reason": retry.final_reason,
                    "final_strategy": retry.final_strategy,
                }
                recovered = bool(retry.success)

            if recovered:
                result_item["status"] = "recovered"
                step_results.append(result_item)
                continue

            step_results.append(result_item)
            run.status = "failed"
            run.result = {"steps": step_results, "failed_step": idx + 1, "reason": "command_failed"}
            await session.commit()
            await session.refresh(run)
            return PlaybookRunResult(run=run, requires_approval=False, pending_command_id=None)

    run.status = "completed"
    run.result = {"steps": step_results}
    await session.commit()
    await session.refresh(run)
    return PlaybookRunResult(run=run, requires_approval=False, pending_command_id=None)
