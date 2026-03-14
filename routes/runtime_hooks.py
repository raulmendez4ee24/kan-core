from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import RuntimeHookRegistration
from brain.runtime_hooks import runtime_hooks
from schemas.runtime_hooks import RuntimeHookRegisterRequest, RuntimeHooksListResponse
from schemas.runtime_hooks import RuntimeHookRollbackRequest, RuntimeHookRollbackResponse, RuntimeHookRegistrationRead
from security import require_client_token

router = APIRouter(prefix="/runtime/hooks", tags=["runtime-hooks"])


def _annotate_hook_factory(event: str, metadata: dict):
    def _hook(payload: dict):
        out = dict(payload or {})
        annotations = out.get("annotations")
        if not isinstance(annotations, list):
            annotations = []
        annotations.append({"event": event, **dict(metadata or {})})
        out["annotations"] = annotations
        return out

    return _hook


@router.get("", response_model=RuntimeHooksListResponse)
async def list_runtime_hooks(client_id: str = Depends(require_client_token)):
    await runtime_hooks.ensure_loaded(client_id)
    return RuntimeHooksListResponse(
        events=runtime_hooks.list_events(organization_id=client_id),
        registrations=runtime_hooks.list_registrations(organization_id=client_id),
    )


@router.post("/register", response_model=RuntimeHooksListResponse)
async def register_runtime_hook(
    req: RuntimeHookRegisterRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    event = str(req.event or "").strip()
    if not event:
        raise HTTPException(status_code=400, detail="event is required")
    try:
        org_id = UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc

    max_stmt = (
        select(func.max(RuntimeHookRegistration.version))
        .where(
            RuntimeHookRegistration.organization_id == org_id,
            RuntimeHookRegistration.event == event,
        )
    )
    current = (await session.execute(max_stmt)).scalar_one_or_none() or 0
    version = int(current) + 1

    row = RuntimeHookRegistration(
        organization_id=org_id,
        event=event,
        mode=req.mode,
        version=version,
        metadata_json=dict(req.metadata or {}),
    )
    session.add(row)
    await session.commit()
    await runtime_hooks.reload_org(client_id)
    return RuntimeHooksListResponse(
        events=runtime_hooks.list_events(organization_id=client_id),
        registrations=runtime_hooks.list_registrations(organization_id=client_id),
    )


@router.get("/{event}/versions", response_model=list[RuntimeHookRegistrationRead])
async def list_runtime_hook_versions(
    event: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    evt = str(event or "").strip()
    if not evt:
        raise HTTPException(status_code=400, detail="event is required")
    try:
        org_id = UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc

    stmt = (
        select(RuntimeHookRegistration)
        .where(
            RuntimeHookRegistration.organization_id == org_id,
            RuntimeHookRegistration.event == evt,
        )
        .order_by(RuntimeHookRegistration.version.asc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [
        RuntimeHookRegistrationRead(
            event=row.event,
            mode=row.mode,
            metadata=dict(row.metadata_json or {}),
            organization_id=str(row.organization_id),
            version=int(row.version or 1),
        )
        for row in rows
    ]


@router.post("/{event}/rollback", response_model=RuntimeHookRollbackResponse)
async def rollback_runtime_hook_version(
    event: str,
    req: RuntimeHookRollbackRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    evt = str(event or "").strip()
    if not evt:
        raise HTTPException(status_code=400, detail="event is required")
    try:
        org_id = UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc

    target_stmt = (
        select(RuntimeHookRegistration)
        .where(
            RuntimeHookRegistration.organization_id == org_id,
            RuntimeHookRegistration.event == evt,
            RuntimeHookRegistration.version == int(req.target_version),
        )
        .limit(1)
    )
    target = (await session.execute(target_stmt)).scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="target_version_not_found")

    max_stmt = (
        select(func.max(RuntimeHookRegistration.version))
        .where(
            RuntimeHookRegistration.organization_id == org_id,
            RuntimeHookRegistration.event == evt,
        )
    )
    current = int((await session.execute(max_stmt)).scalar_one_or_none() or 0)
    new_version = current + 1
    metadata = dict(target.metadata_json or {})
    metadata["_rollback"] = {
        "target_version": int(req.target_version),
        "created_from_version": int(target.version or req.target_version),
    }
    row = RuntimeHookRegistration(
        organization_id=org_id,
        event=evt,
        mode=target.mode,
        version=new_version,
        metadata_json=metadata,
    )
    session.add(row)
    await session.commit()
    await runtime_hooks.reload_org(client_id)

    return RuntimeHookRollbackResponse(
        event=evt,
        rolled_back_to_version=int(req.target_version),
        new_active_version=new_version,
        registration=RuntimeHookRegistrationRead(
            event=row.event,
            mode=row.mode,
            metadata=dict(row.metadata_json or {}),
            organization_id=str(row.organization_id),
            version=int(row.version or new_version),
        ),
    )
