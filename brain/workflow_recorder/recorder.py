import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from learning.strategy_memory import build_ui_context
from models import (
    DesktopCommand,
    DesktopCommandResult,
    DesktopAgent,
    WorkflowRecording,
    WorkflowRecordingStep,
)


def _enabled() -> bool:
    return os.getenv("ENABLE_WORKFLOW_RECORDER", "false").lower() in {"1", "true", "yes"}


def _max_error_len() -> int:
    return max(80, min(int(os.getenv("WORKFLOW_RECORDER_MAX_ERROR_LEN", "800") or 800), 4000))


def _compact_result_data(value: Any, *, allow_screenshots: bool) -> Dict[str, Any]:
    # Avoid storing huge blobs (e.g., base64 screenshots) in recorder steps by default.
    if not isinstance(value, dict):
        return {}
    data = dict(value)
    if not allow_screenshots:
        data.pop("image_base64", None)
        data.pop("screenshot_base64", None)
    # Clamp large strings.
    for k, v in list(data.items()):
        if isinstance(v, str) and len(v) > 1200:
            data[k] = v[:1200] + "...(truncated)"
    return data


async def start_recording(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
    title: str,
    created_by_user_id: Optional[UUID],
    metadata: Optional[Dict[str, Any]] = None,
) -> WorkflowRecording:
    if not _enabled():
        raise ValueError("Workflow recorder disabled")

    agent = (
        await session.execute(
            select(DesktopAgent).where(
                DesktopAgent.organization_id == organization_id,
                DesktopAgent.id == agent_id,
            )
        )
    ).scalars().first()
    if not agent:
        raise ValueError("Agent not found")

    existing_stmt = (
        select(WorkflowRecording.id)
        .where(
            WorkflowRecording.organization_id == organization_id,
            WorkflowRecording.agent_id == agent_id,
            WorkflowRecording.status == "recording",
        )
        .limit(1)
    )
    if (await session.execute(existing_stmt)).scalars().first():
        raise ValueError("A recording is already active for this agent")

    row = WorkflowRecording(
        organization_id=organization_id,
        agent_id=agent_id,
        title=str(title or "Workflow recording").strip()[:240],
        status="recording",
        created_by_user_id=created_by_user_id,
        started_at=datetime.now(timezone.utc),
        stopped_at=None,
        recording_metadata=metadata or {},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def stop_recording(
    session: AsyncSession,
    *,
    organization_id: UUID,
    recording_id: UUID,
    stopped_by_user_id: Optional[UUID],
    note: Optional[str] = None,
) -> WorkflowRecording:
    if not _enabled():
        raise ValueError("Workflow recorder disabled")

    row = (
        await session.execute(
            select(WorkflowRecording).where(
                WorkflowRecording.organization_id == organization_id,
                WorkflowRecording.id == recording_id,
            )
        )
    ).scalars().first()
    if not row:
        raise ValueError("Recording not found")
    if row.status != "recording":
        return row

    meta = dict(row.recording_metadata or {})
    if note:
        meta["stop_note"] = str(note)[:2000]
    if stopped_by_user_id:
        meta["stopped_by_user_id"] = str(stopped_by_user_id)
    row.recording_metadata = meta
    row.status = "stopped"
    row.stopped_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return row


async def _find_active_recording(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
) -> Optional[WorkflowRecording]:
    stmt = (
        select(WorkflowRecording)
        .where(
            WorkflowRecording.organization_id == organization_id,
            WorkflowRecording.agent_id == agent_id,
            WorkflowRecording.status == "recording",
        )
        .order_by(desc(WorkflowRecording.started_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def maybe_append_step_for_result(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: UUID,
    command: DesktopCommand,
    result: DesktopCommandResult,
) -> Optional[WorkflowRecordingStep]:
    if not _enabled():
        return None

    recording = await _find_active_recording(session, organization_id=organization_id, agent_id=agent_id)
    if not recording:
        return None

    # Avoid duplicates for the same command_id (upserts can happen).
    existing_stmt = (
        select(WorkflowRecordingStep.id)
        .where(
            WorkflowRecordingStep.recording_id == recording.id,
            WorkflowRecordingStep.desktop_command_id == command.id,
        )
        .limit(1)
    )
    if (await session.execute(existing_stmt)).scalars().first():
        return None

    max_stmt = select(func.max(WorkflowRecordingStep.step_order)).where(
        WorkflowRecordingStep.recording_id == recording.id
    )
    max_order = (await session.execute(max_stmt)).scalar()
    step_order = int(max_order or 0) + 1

    capture_policy = {}
    if isinstance(recording.recording_metadata, dict):
        capture_policy = recording.recording_metadata.get("capture_policy") if isinstance(recording.recording_metadata.get("capture_policy"), dict) else {}
    allow_screenshots = bool(capture_policy.get("store_screenshots", False))

    compact_result = _compact_result_data(result.data, allow_screenshots=allow_screenshots)
    cmd_meta = command.command_metadata if isinstance(command.command_metadata, dict) else {}
    ui_ctx = build_ui_context(action=command.action, args=dict(command.args or {}), metadata=cmd_meta)

    step = WorkflowRecordingStep(
        recording_id=recording.id,
        organization_id=organization_id,
        agent_id=agent_id,
        step_order=step_order,
        desktop_command_id=command.id,
        action=str(command.action or ""),
        args=dict(command.args or {}),
        result_status=str(result.status or "success"),
        duration_ms=result.duration_ms,
        error=(str(result.error)[: _max_error_len()] if result.error else None),
        step_metadata={
            "command_metadata": cmd_meta,
            "result_data": compact_result,
            "ui_context": ui_ctx,
        },
    )
    session.add(step)
    try:
        await session.commit()
        await session.refresh(step)
        return step
    except Exception:
        # Never break command execution flow if recorder fails.
        await session.rollback()
        return None

