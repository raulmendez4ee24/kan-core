import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from uuid import UUID

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.policy_engine import load_effective_policy
from models import WorkflowPlaybook, WorkflowRecording, WorkflowRecordingStep


def _enabled() -> bool:
    return os.getenv("ENABLE_WORKFLOW_RECORDER", "false").lower() in {"1", "true", "yes"}


def _normalize_steps(steps: List[WorkflowRecordingStep]) -> Tuple[List[Dict[str, Any]], int]:
    playbook_steps: List[Dict[str, Any]] = []
    dropped = 0

    i = 0
    while i < len(steps):
        step = steps[i]
        action = str(step.action or "").strip()
        if action == "mover_mouse":
            dropped += 1
            i += 1
            continue

        if action == "type_text":
            seq: List[str] = []
            interval = None
            start_meta = step.step_metadata if isinstance(step.step_metadata, dict) else {}
            while i < len(steps) and str(steps[i].action or "").strip() == "type_text":
                args = steps[i].args if isinstance(steps[i].args, dict) else {}
                text = args.get("text")
                if isinstance(text, str):
                    seq.append(text)
                if interval is None and args.get("interval") is not None:
                    interval = args.get("interval")
                i += 1
            if seq:
                playbook_steps.append(
                    {
                        "kind": "desktop",
                        "action": "type_text_sequence",
                        "args": {"sequence": seq, "interval": interval},
                        "metadata": {"source_recording": True, **(start_meta or {})},
                    }
                )
            continue

        playbook_steps.append(
            {
                "kind": "desktop",
                "action": action,
                "args": dict(step.args or {}),
                "metadata": dict(step.step_metadata or {}),
                "recorded_command_id": str(step.desktop_command_id) if step.desktop_command_id else None,
            }
        )
        i += 1

    return playbook_steps, dropped


async def compile_recording_to_playbook(
    session: AsyncSession,
    *,
    organization_id: UUID,
    recording_id: UUID,
    title: str | None = None,
) -> WorkflowPlaybook:
    if not _enabled():
        raise ValueError("Workflow recorder disabled")

    rec = (
        await session.execute(
            select(WorkflowRecording).where(
                WorkflowRecording.organization_id == organization_id,
                WorkflowRecording.id == recording_id,
            )
        )
    ).scalars().first()
    if not rec:
        raise ValueError("Recording not found")

    steps_stmt = (
        select(WorkflowRecordingStep)
        .where(WorkflowRecordingStep.recording_id == rec.id)
        .order_by(asc(WorkflowRecordingStep.step_order))
    )
    steps = list((await session.execute(steps_stmt)).scalars().all())
    playbook_steps, dropped = _normalize_steps(steps)

    policy = await load_effective_policy(session, organization_id=organization_id)
    snapshot = {
        "policy": policy.policy,
        "updated_at": (policy.updated_at.isoformat() if policy.updated_at else None),
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }

    pb = WorkflowPlaybook(
        organization_id=organization_id,
        agent_id=rec.agent_id,
        recording_id=rec.id,
        title=(str(title).strip()[:240] if title else str(rec.title or "Workflow playbook").strip()[:240]),
        version=1,
        steps={"items": playbook_steps},
        policy_snapshot=snapshot,
    )
    session.add(pb)
    rec.status = "compiled"
    await session.commit()
    await session.refresh(pb)
    await session.refresh(rec)
    _ = dropped
    return pb

