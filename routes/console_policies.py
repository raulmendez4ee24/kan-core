import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.policy_presets import apply_openclaw_allow_all_preset, apply_openclaw_preset
from brain.rbac import require_org_access
from brain.security_tokens import require_permissions, require_user_principal
from database import get_session
from models import OrgPolicy
from schemas.policies import OrgPolicyRead, OrgPolicyUpsertRequest

router = APIRouter(prefix="/console/orgs", tags=["console-policies"])


def _enabled() -> bool:
    return os.getenv("ENABLE_ENTERPRISE_CONSOLE", "false").lower() in {"1", "true", "yes"}


def _ensure_enabled() -> None:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_ENTERPRISE_CONSOLE disabled")


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.get("/{org_id}/policies", response_model=OrgPolicyRead)
async def get_org_policies(
    org_id: str,
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permissions(principal, ["org.policies.read"])
    org_uuid = _parse_uuid(org_id, field="org_id")
    try:
        require_org_access(principal=principal, organization_id=org_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    stmt = select(OrgPolicy).where(OrgPolicy.organization_id == org_uuid).limit(1)
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        # Return empty policy rather than 404 to simplify clients.
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return OrgPolicyRead(
            organization_id=str(org_uuid),
            policy={},
            updated_by_user_id=None,
            created_at=now,
            updated_at=now,
        )
    return OrgPolicyRead(
        organization_id=str(row.organization_id),
        policy=row.policy if isinstance(row.policy, dict) else {},
        updated_by_user_id=(str(row.updated_by_user_id) if row.updated_by_user_id else None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/{org_id}/policies", response_model=OrgPolicyRead)
async def upsert_org_policies(
    org_id: str,
    req: OrgPolicyUpsertRequest,
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permissions(principal, ["org.policies.write"])
    org_uuid = _parse_uuid(org_id, field="org_id")
    try:
        require_org_access(principal=principal, organization_id=org_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    stmt = select(OrgPolicy).where(OrgPolicy.organization_id == org_uuid).limit(1)
    row = (await session.execute(stmt)).scalars().first()

    actor_raw = str(principal.get("sub") or "")
    actor_uuid = _parse_uuid(actor_raw, field="user_id") if actor_raw else None
    payload = req.policy if isinstance(req.policy, dict) else {}

    if row:
        row.policy = payload
        row.updated_by_user_id = actor_uuid
    else:
        row = OrgPolicy(
            organization_id=org_uuid,
            policy=payload,
            updated_by_user_id=actor_uuid,
        )
        session.add(row)

    await session.commit()
    await session.refresh(row)

    await write_audit_log(
        session,
        organization_id=org_uuid,
        actor_user_id=actor_uuid,
        action="org.policies.upsert",
        resource="org_policy",
        resource_id=str(row.id),
        status="ok",
        metadata={"keys": sorted(list(payload.keys()))[:50]},
    )

    return OrgPolicyRead(
        organization_id=str(row.organization_id),
        policy=row.policy if isinstance(row.policy, dict) else {},
        updated_by_user_id=(str(row.updated_by_user_id) if row.updated_by_user_id else None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/{org_id}/policies/presets/openclaw", response_model=OrgPolicyRead)
async def apply_openclaw_policy_preset(
    org_id: str,
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    """Apply a 'full_auto' preset with guardrails, without wiping existing allowlists."""
    require_permissions(principal, ["org.policies.write"])
    org_uuid = _parse_uuid(org_id, field="org_id")
    try:
        require_org_access(principal=principal, organization_id=org_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    stmt = select(OrgPolicy).where(OrgPolicy.organization_id == org_uuid).limit(1)
    row = (await session.execute(stmt)).scalars().first()

    actor_raw = str(principal.get("sub") or "")
    actor_uuid = _parse_uuid(actor_raw, field="user_id") if actor_raw else None

    existing = row.policy if row and isinstance(row.policy, dict) else {}
    payload = apply_openclaw_preset(existing)

    if row:
        row.policy = payload
        row.updated_by_user_id = actor_uuid
    else:
        row = OrgPolicy(
            organization_id=org_uuid,
            policy=payload,
            updated_by_user_id=actor_uuid,
        )
        session.add(row)

    await session.commit()
    await session.refresh(row)

    await write_audit_log(
        session,
        organization_id=org_uuid,
        actor_user_id=actor_uuid,
        action="org.policies.apply_preset_openclaw",
        resource="org_policy",
        resource_id=str(row.id),
        status="ok",
        metadata={"keys": sorted(list(payload.keys()))[:50], "preset": "openclaw"},
    )

    return OrgPolicyRead(
        organization_id=str(row.organization_id),
        policy=row.policy if isinstance(row.policy, dict) else {},
        updated_by_user_id=(str(row.updated_by_user_id) if row.updated_by_user_id else None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/{org_id}/policies/presets/openclaw-allow-all", response_model=OrgPolicyRead)
async def apply_openclaw_allow_all_policy_preset(
    org_id: str,
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    """Apply an unsafe preset that allows any browser domain/program (dev/demo only)."""
    require_permissions(principal, ["org.policies.write"])
    org_uuid = _parse_uuid(org_id, field="org_id")
    try:
        require_org_access(principal=principal, organization_id=org_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    stmt = select(OrgPolicy).where(OrgPolicy.organization_id == org_uuid).limit(1)
    row = (await session.execute(stmt)).scalars().first()

    actor_raw = str(principal.get("sub") or "")
    actor_uuid = _parse_uuid(actor_raw, field="user_id") if actor_raw else None

    existing = row.policy if row and isinstance(row.policy, dict) else {}
    payload = apply_openclaw_allow_all_preset(existing)

    if row:
        row.policy = payload
        row.updated_by_user_id = actor_uuid
    else:
        row = OrgPolicy(
            organization_id=org_uuid,
            policy=payload,
            updated_by_user_id=actor_uuid,
        )
        session.add(row)

    await session.commit()
    await session.refresh(row)

    await write_audit_log(
        session,
        organization_id=org_uuid,
        actor_user_id=actor_uuid,
        action="org.policies.apply_preset_openclaw_allow_all",
        resource="org_policy",
        resource_id=str(row.id),
        status="ok",
        metadata={"keys": sorted(list(payload.keys()))[:50], "preset": "openclaw_allow_all"},
    )

    return OrgPolicyRead(
        organization_id=str(row.organization_id),
        policy=row.policy if isinstance(row.policy, dict) else {},
        updated_by_user_id=(str(row.updated_by_user_id) if row.updated_by_user_id else None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
