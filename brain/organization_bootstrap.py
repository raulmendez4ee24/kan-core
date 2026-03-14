import os
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.policy_presets import apply_openclaw_allow_all_preset, apply_openclaw_preset
from models import OrgPolicy


def _policies_enabled() -> bool:
    return os.getenv("ENABLE_ENTERPRISE_POLICIES", "false").lower() in {"1", "true", "yes"}


def _preset_name() -> str:
    return str(os.getenv("POLICY_AUTO_APPLY_PRESET", "")).strip().lower()


async def apply_policy_auto_preset(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_user_id: Optional[UUID] = None,
    source: str = "bootstrap",
) -> Optional[str]:
    if not _policies_enabled():
        return None

    preset = _preset_name()
    if preset not in {"openclaw", "openclaw_allow_all"}:
        return None

    stmt = select(OrgPolicy).where(OrgPolicy.organization_id == organization_id).limit(1)
    row = (await session.execute(stmt)).scalars().first()

    existing = row.policy if row and isinstance(row.policy, dict) else {}
    if preset == "openclaw_allow_all":
        payload = apply_openclaw_allow_all_preset(existing)
    else:
        payload = apply_openclaw_preset(existing)

    if row:
        row.policy = payload
        row.updated_by_user_id = actor_user_id
    else:
        row = OrgPolicy(
            organization_id=organization_id,
            policy=payload,
            updated_by_user_id=actor_user_id,
        )
        session.add(row)
    await session.commit()
    await session.refresh(row)

    await write_audit_log(
        session,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action="org.policies.auto_apply_preset",
        resource="org_policy",
        resource_id=str(row.id),
        status="ok",
        metadata={"preset": preset, "source": source},
    )
    return preset

