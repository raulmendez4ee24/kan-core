from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.security_tokens import require_permissions, require_roles, require_user_principal
from database import get_session
from models import AuditLog
from schemas.security import AuditLogRead

router = APIRouter(prefix="/admin", tags=["admin"])


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.get("/audit-logs", response_model=list[AuditLogRead])
async def admin_audit_logs(
    organization_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    require_roles(principal, ["SUPER_ADMIN", "PLATFORM_ADMIN"])
    require_permissions(principal, ["admin.audit.read"])

    stmt = select(AuditLog)
    if organization_id:
        stmt = stmt.where(AuditLog.organization_id == _parse_uuid(organization_id, field="organization_id"))
    stmt = stmt.order_by(desc(AuditLog.created_at)).limit(limit)

    rows = list((await session.execute(stmt)).scalars().all())
    return [
        AuditLogRead(
            id=str(item.id),
            organization_id=str(item.organization_id) if item.organization_id else None,
            actor_user_id=str(item.actor_user_id) if item.actor_user_id else None,
            action=item.action,
            resource=item.resource,
            resource_id=item.resource_id,
            status=item.status,
            detail=item.detail,
            metadata=item.audit_metadata or {},
            created_at=item.created_at,
        )
        for item in rows
    ]
