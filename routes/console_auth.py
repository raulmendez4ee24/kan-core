import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.passwords import verify_password
from brain.rbac import resolve_role_and_permissions
from brain.security_tokens import issue_token, require_user_principal
from database import get_session
from models import User
from schemas.console import ConsoleLoginRequest
from schemas.security import TokenIssueResponse, UserPrincipal

router = APIRouter(prefix="/console/auth", tags=["console-auth"])


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


@router.post("/login", response_model=TokenIssueResponse)
async def console_login(
    req: ConsoleLoginRequest,
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled()

    email = str(req.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email is required")

    stmt = select(User).where(User.email == email).limit(1)
    user = (await session.execute(stmt)).scalars().first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    org_id_raw = req.organization_id or (str(user.organization_id) if user.organization_id else None)
    if not org_id_raw:
        raise HTTPException(status_code=400, detail="organization_id required for this user")
    org_uuid = _parse_uuid(org_id_raw, field="organization_id")

    role, perms = await resolve_role_and_permissions(
        session,
        organization_id=org_uuid,
        user_id=user.id,
    )
    token, exp = issue_token(
        user_id=str(user.id),
        organization_id=str(org_uuid),
        role=role,
        permissions=perms,
        expires_in_minutes=int(os.getenv("CONSOLE_TOKEN_EXPIRES_IN_MINUTES", "240") or 240),
    )

    await write_audit_log(
        session,
        organization_id=org_uuid,
        actor_user_id=user.id,
        action="console.auth.login",
        resource="user_session",
        resource_id=None,
        status="ok",
        metadata={"email": email, "role": role},
    )

    return TokenIssueResponse(
        token=token,
        expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
    )


@router.get("/me", response_model=UserPrincipal)
async def console_me(
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
):
    return UserPrincipal(
        user_id=str(principal.get("sub") or ""),
        organization_id=(str(principal.get("org")) if principal.get("org") else None),
        role=str(principal.get("role") or ""),
        permissions=[str(x) for x in (principal.get("permissions") or [])],
        exp=int(principal.get("exp") or 0),
    )
