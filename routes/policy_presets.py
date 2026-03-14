import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.policy_presets import apply_openclaw_allow_all_preset
from database import get_session
from models import OrgPolicy
from schemas.policies import OrgPolicyRead
from security import require_client_token

router = APIRouter(prefix="/policies", tags=["policies"])


def _enabled() -> bool:
    return os.getenv("ENABLE_ENTERPRISE_POLICIES", "false").lower() in {"1", "true", "yes"}


@router.post("/presets/openclaw-allow-all", response_model=OrgPolicyRead)
async def apply_openclaw_allow_all_preset_for_org(
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_ENTERPRISE_POLICIES disabled")

    try:
        org_uuid = UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid client_id") from exc

    stmt = select(OrgPolicy).where(OrgPolicy.organization_id == org_uuid).limit(1)
    row = (await session.execute(stmt)).scalars().first()

    existing = row.policy if row and isinstance(row.policy, dict) else {}
    payload = apply_openclaw_allow_all_preset(existing)

    if row:
        row.policy = payload
    else:
        row = OrgPolicy(organization_id=org_uuid, policy=payload, updated_by_user_id=None)
        session.add(row)

    await session.commit()
    await session.refresh(row)

    await write_audit_log(
        session,
        organization_id=org_uuid,
        actor_user_id=None,
        action="org.policies.apply_preset_openclaw_allow_all",
        resource="org_policy",
        resource_id=str(row.id),
        status="ok",
        metadata={"preset": "openclaw_allow_all", "keys": sorted(list(payload.keys()))[:50]},
    )

    return OrgPolicyRead(
        organization_id=str(row.organization_id),
        policy=row.policy if isinstance(row.policy, dict) else {},
        updated_by_user_id=None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

