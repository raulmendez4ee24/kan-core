import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.security_tokens import issue_token
from database import get_session
from schemas.security import TokenIssueRequest, TokenIssueResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.post("/token", response_model=TokenIssueResponse)
async def issue_internal_token(
    req: TokenIssueRequest,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    session: AsyncSession = Depends(get_session),
):
    expected_key = os.getenv("AUTH_ADMIN_KEY")
    if expected_key and x_admin_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    token, exp = issue_token(
        user_id=req.user_id,
        organization_id=req.organization_id,
        role=req.role,
        permissions=req.permissions,
        expires_in_minutes=req.expires_in_minutes,
    )

    org_id = _parse_uuid(req.organization_id, field="organization_id") if req.organization_id else None
    actor_id = _parse_uuid(req.user_id, field="user_id")
    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=actor_id,
        action="auth.token.issue",
        resource="token",
        resource_id=None,
        status="ok",
        metadata={"role": req.role, "permissions": req.permissions},
    )

    return TokenIssueResponse(
        token=token,
        expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
    )
