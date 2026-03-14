import os
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.workflow_recorder.compiler import compile_recording_to_playbook
from brain.workflow_recorder.executor import run_playbook
from brain.workflow_recorder.recorder import start_recording, stop_recording
from database import get_session
from models import (
    WorkflowPlaybook,
    WorkflowPlaybookRun,
    WorkflowRecording,
    WorkflowRecordingStep,
)
from routes.desktop_agent import _ws_manager
from schemas.desktop_agent import WsRecordingControlEnvelope
from schemas.workflows import (
    WorkflowCompileResponse,
    WorkflowExportN8NResponse,
    WorkflowPlaybookRead,
    WorkflowPlaybookRunRead,
    WorkflowPlaybookRunRequest,
    WorkflowPlaybookRunResponse,
    WorkflowRecordingCreateRequest,
    WorkflowRecordingDetailResponse,
    WorkflowRecordingRead,
    WorkflowRecordingStepRead,
    WorkflowRecordingStopRequest,
)
from security import require_client_token
from tools.n8n_client import build_workflow_url, create_workflow

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _enabled() -> bool:
    return os.getenv("ENABLE_WORKFLOW_RECORDER", "false").lower() in {"1", "true", "yes"}


def _human_recorder_enabled() -> bool:
    return os.getenv("ENABLE_HUMAN_RECORDER", "false").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


def _to_recording_read(row: WorkflowRecording) -> WorkflowRecordingRead:
    return WorkflowRecordingRead(
        id=str(row.id),
        organization_id=str(row.organization_id),
        agent_id=str(row.agent_id),
        title=row.title,
        status=row.status,
        created_by_user_id=(str(row.created_by_user_id) if row.created_by_user_id else None),
        started_at=row.started_at,
        stopped_at=row.stopped_at,
        metadata=row.recording_metadata or {},
        n8n_workflow_id=row.n8n_workflow_id,
        n8n_workflow_url=row.n8n_workflow_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_step_read(row: WorkflowRecordingStep) -> WorkflowRecordingStepRead:
    return WorkflowRecordingStepRead(
        id=str(row.id),
        recording_id=str(row.recording_id),
        organization_id=str(row.organization_id),
        agent_id=str(row.agent_id),
        step_order=int(row.step_order),
        desktop_command_id=(str(row.desktop_command_id) if row.desktop_command_id else None),
        action=row.action,
        args=row.args or {},
        result_status=row.result_status,
        duration_ms=row.duration_ms,
        error=row.error,
        metadata=row.step_metadata or {},
        created_at=row.created_at,
    )


def _to_playbook_read(row: WorkflowPlaybook) -> WorkflowPlaybookRead:
    return WorkflowPlaybookRead(
        id=str(row.id),
        organization_id=str(row.organization_id),
        agent_id=(str(row.agent_id) if row.agent_id else None),
        recording_id=(str(row.recording_id) if row.recording_id else None),
        title=row.title,
        version=int(row.version or 1),
        steps=row.steps if isinstance(row.steps, dict) else {},
        policy_snapshot=row.policy_snapshot if isinstance(row.policy_snapshot, dict) else {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_run_read(row: WorkflowPlaybookRun) -> WorkflowPlaybookRunRead:
    return WorkflowPlaybookRunRead(
        id=str(row.id),
        organization_id=str(row.organization_id),
        playbook_id=str(row.playbook_id),
        agent_id=str(row.agent_id),
        status=row.status,
        current_step=int(row.current_step or 0),
        result=row.result if isinstance(row.result, dict) else {},
        created_by_user_id=(str(row.created_by_user_id) if row.created_by_user_id else None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/recordings", response_model=WorkflowRecordingRead)
async def workflows_start_recording(
    req: WorkflowRecordingCreateRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    agent_id = _parse_uuid(req.agent_id, field="agent_id")

    meta = dict(req.metadata or {})
    meta["notes"] = req.notes
    capture_policy = dict(req.capture_policy or {})
    meta["capture_policy"] = capture_policy
    rec = await start_recording(
        session,
        organization_id=org_id,
        agent_id=agent_id,
        title=req.title,
        created_by_user_id=None,
        metadata=meta,
    )

    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=None,
        action="workflows.recording.start",
        resource="workflow_recording",
        resource_id=str(rec.id),
        status="ok",
        metadata={"agent_id": req.agent_id, "title": req.title},
    )

    # Best-effort remote start for human-in-the-loop recorder.
    mode = str(capture_policy.get("mode") or "").strip().lower()
    if mode == "human" and _human_recorder_enabled():
        redaction = capture_policy.get("redaction") if isinstance(capture_policy.get("redaction"), dict) else {}
        if "mask_password_fields" not in redaction:
            redaction["mask_password_fields"] = True
        if "mask_all_keystrokes" not in redaction:
            # OS-level hooks cannot reliably detect password fields, so default to masking.
            redaction["mask_all_keystrokes"] = True
        envelope = WsRecordingControlEnvelope(
            recording_id=str(rec.id),
            status="start",
            mode="human",
            redaction=redaction,
        )
        try:
            await _ws_manager.send_json(agent_id, envelope.model_dump())
        except Exception:
            pass
    return _to_recording_read(rec)


@router.post("/recordings/{recording_id}/stop", response_model=WorkflowRecordingRead)
async def workflows_stop_recording(
    recording_id: str,
    req: WorkflowRecordingStopRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    rec_uuid = _parse_uuid(recording_id, field="recording_id")

    rec = await stop_recording(
        session,
        organization_id=org_id,
        recording_id=rec_uuid,
        stopped_by_user_id=None,
        note=req.note,
    )
    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=None,
        action="workflows.recording.stop",
        resource="workflow_recording",
        resource_id=str(rec.id),
        status="ok",
        detail=req.note,
        metadata={},
    )

    # Best-effort remote stop for human-in-the-loop recorder.
    meta = rec.recording_metadata if isinstance(rec.recording_metadata, dict) else {}
    capture_policy = meta.get("capture_policy") if isinstance(meta.get("capture_policy"), dict) else {}
    mode = str(capture_policy.get("mode") or "").strip().lower()
    if mode == "human" and _human_recorder_enabled():
        envelope = WsRecordingControlEnvelope(
            recording_id=str(rec.id),
            status="stop",
            mode="human",
            redaction={},
        )
        try:
            await _ws_manager.send_json(rec.agent_id, envelope.model_dump())
        except Exception:
            pass
    return _to_recording_read(rec)


@router.get("/recordings", response_model=List[WorkflowRecordingRead])
async def workflows_list_recordings(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    stmt = select(WorkflowRecording).where(WorkflowRecording.organization_id == org_id)
    if status:
        stmt = stmt.where(WorkflowRecording.status == status)
    stmt = stmt.order_by(desc(WorkflowRecording.updated_at)).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return [_to_recording_read(x) for x in rows]


@router.get("/recordings/{recording_id}", response_model=WorkflowRecordingDetailResponse)
async def workflows_get_recording(
    recording_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    rec_uuid = _parse_uuid(recording_id, field="recording_id")

    rec = (
        await session.execute(
            select(WorkflowRecording).where(
                WorkflowRecording.organization_id == org_id,
                WorkflowRecording.id == rec_uuid,
            )
        )
    ).scalars().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")

    steps_stmt = (
        select(WorkflowRecordingStep)
        .where(WorkflowRecordingStep.recording_id == rec.id)
        .order_by(asc(WorkflowRecordingStep.step_order))
    )
    steps = list((await session.execute(steps_stmt)).scalars().all())
    return WorkflowRecordingDetailResponse(
        recording=_to_recording_read(rec),
        steps=[_to_step_read(x) for x in steps],
    )


@router.post("/recordings/{recording_id}/compile", response_model=WorkflowCompileResponse)
async def workflows_compile_recording(
    recording_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    rec_uuid = _parse_uuid(recording_id, field="recording_id")

    pb = await compile_recording_to_playbook(
        session,
        organization_id=org_id,
        recording_id=rec_uuid,
    )
    items = pb.steps.get("items", []) if isinstance(pb.steps, dict) else []
    step_count = len(items) if isinstance(items, list) else 0
    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=None,
        action="workflows.recording.compile",
        resource="workflow_playbook",
        resource_id=str(pb.id),
        status="ok",
        metadata={"recording_id": recording_id, "step_count": step_count},
    )
    return WorkflowCompileResponse(
        recording_id=str(rec_uuid),
        playbook_id=str(pb.id),
        step_count=step_count,
        summary=f"Compiled {step_count} steps into playbook",
    )


@router.get("/playbooks", response_model=List[WorkflowPlaybookRead])
async def workflows_list_playbooks(
    limit: int = Query(default=50, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    stmt = (
        select(WorkflowPlaybook)
        .where(WorkflowPlaybook.organization_id == org_id)
        .order_by(desc(WorkflowPlaybook.updated_at))
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [_to_playbook_read(x) for x in rows]


@router.get("/playbooks/{playbook_id}", response_model=WorkflowPlaybookRead)
async def workflows_get_playbook(
    playbook_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    pb_uuid = _parse_uuid(playbook_id, field="playbook_id")
    row = (
        await session.execute(
            select(WorkflowPlaybook).where(
                WorkflowPlaybook.organization_id == org_id,
                WorkflowPlaybook.id == pb_uuid,
            )
        )
    ).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Playbook not found")
    return _to_playbook_read(row)


@router.post("/playbooks/{playbook_id}/run", response_model=WorkflowPlaybookRunResponse)
async def workflows_run_playbook(
    playbook_id: str,
    req: WorkflowPlaybookRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    pb_uuid = _parse_uuid(playbook_id, field="playbook_id")
    agent_uuid = _parse_uuid(req.agent_id, field="agent_id")

    result = await run_playbook(
        session,
        organization_id=org_id,
        playbook_id=pb_uuid,
        agent_id=agent_uuid,
        actor_user_id=None,
        dry_run=bool(req.dry_run),
    )

    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=None,
        action="workflows.playbook.run",
        resource="workflow_playbook_run",
        resource_id=str(result.run.id),
        status="ok" if result.run.status in {"completed", "paused_requires_approval"} else "failed",
        metadata={"playbook_id": playbook_id, "agent_id": req.agent_id, "status": result.run.status},
    )

    pending = result.pending_command_id if result.requires_approval else None
    return WorkflowPlaybookRunResponse(
        run=_to_run_read(result.run),
        requires_approval=bool(result.requires_approval),
        pending_command_id=pending,
    )


@router.get("/playbooks/runs/{run_id}", response_model=WorkflowPlaybookRunRead)
async def workflows_get_playbook_run(
    run_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    run_uuid = _parse_uuid(run_id, field="run_id")
    stmt = select(WorkflowPlaybookRun).where(
        WorkflowPlaybookRun.organization_id == org_id,
        WorkflowPlaybookRun.id == run_uuid,
    )
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return _to_run_read(row)


def _build_export_workflow(
    *,
    name: str,
    api_base_url: str,
    playbook_id: str,
    agent_id: str,
) -> dict:
    trigger = {
        "parameters": {
            "path": f"playbook-{playbook_id[:8]}",
            "httpMethod": "POST",
            "responseMode": "onReceived",
            "responseData": {"send": True, "response": 200, "responseBody": "{\"status\":\"ok\"}"},
        },
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 1,
        "position": [240, 300],
        "name": "Webhook",
    }
    http_node = {
        "parameters": {
            "url": f"{api_base_url.rstrip('/')}/workflows/playbooks/{playbook_id}/run",
            "method": "POST",
            "jsonParameters": True,
            "options": {},
            "bodyParametersJson": {"agent_id": agent_id, "dry_run": False},
            "sendHeaders": True,
            "headerParametersUi": {
                "parameter": [
                    {"name": "X-Client-Id", "value": "{{$env.KAN_CLIENT_ID}}"},
                    {"name": "X-Client-Token", "value": "{{$env.KAN_CLIENT_TOKEN}}"},
                ]
            },
        },
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4,
        "position": [640, 300],
        "name": "Run Playbook",
    }
    return {
        "name": name,
        "nodes": [trigger, http_node],
        "connections": {"Webhook": {"main": [[{"node": "Run Playbook", "type": "main", "index": 0}]]}},
        "active": False,
        "settings": {},
        "staticData": None,
    }


@router.post("/playbooks/{playbook_id}/export/n8n", response_model=WorkflowExportN8NResponse)
async def workflows_export_playbook_to_n8n(
    playbook_id: str,
    agent_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_WORKFLOW_RECORDER disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    pb_uuid = _parse_uuid(playbook_id, field="playbook_id")
    _ = _parse_uuid(agent_id, field="agent_id")

    pb = (
        await session.execute(
            select(WorkflowPlaybook).where(
                WorkflowPlaybook.organization_id == org_id,
                WorkflowPlaybook.id == pb_uuid,
            )
        )
    ).scalars().first()
    if not pb:
        raise HTTPException(status_code=404, detail="Playbook not found")

    api_base = os.getenv("KAN_API_BASE_URL") or os.getenv("PUBLIC_API_BASE_URL") or "http://localhost:8000"
    wf = _build_export_workflow(
        name=f"Playbook {playbook_id[:8]}",
        api_base_url=api_base,
        playbook_id=playbook_id,
        agent_id=agent_id,
    )
    created = await create_workflow(wf)
    if not created:
        return WorkflowExportN8NResponse(
            playbook_id=playbook_id,
            workflow_id=None,
            workflow_url=None,
            created=False,
            detail="n8n API not configured (set N8N_API_URL and N8N_API_KEY) or request failed.",
        )

    wf_id = created.get("id")
    wf_url = build_workflow_url(wf_id)

    # Persist export reference if the playbook is tied to a recording.
    if pb.recording_id:
        rec = (
            await session.execute(
                select(WorkflowRecording).where(
                    WorkflowRecording.organization_id == org_id,
                    WorkflowRecording.id == pb.recording_id,
                )
            )
        ).scalars().first()
        if rec:
            rec.n8n_workflow_id = str(wf_id) if wf_id is not None else None
            rec.n8n_workflow_url = wf_url
            await session.commit()

    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=None,
        action="workflows.playbook.export_n8n",
        resource="workflow_playbook",
        resource_id=playbook_id,
        status="ok",
        metadata={"workflow_id": wf_id, "workflow_url": wf_url},
    )

    return WorkflowExportN8NResponse(
        playbook_id=playbook_id,
        workflow_id=(str(wf_id) if wf_id is not None else None),
        workflow_url=wf_url,
        created=True,
        detail="Workflow created in n8n (requires n8n env vars KAN_CLIENT_ID/KAN_CLIENT_TOKEN to run).",
    )
