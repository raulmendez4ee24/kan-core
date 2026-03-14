from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditLog


async def write_audit_log(
    session: AsyncSession,
    *,
    organization_id: Optional[UUID],
    actor_user_id: Optional[UUID],
    action: str,
    resource: str,
    resource_id: Optional[str],
    status: str,
    detail: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    row = AuditLog(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=action,
        resource=resource,
        resource_id=resource_id,
        status=status,
        detail=detail,
        audit_metadata=metadata or {},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
