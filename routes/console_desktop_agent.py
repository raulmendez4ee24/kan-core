import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.policy_engine import load_effective_policy, validate_desktop_action
from brain.rbac import require_org_access
from brain.security_tokens import require_permissions, require_user_principal
from database import get_session
from models import DesktopCommand
from routes import desktop_agent as desktop_agent_routes
from schemas.desktop_agent import (
    DesktopCommandApproveRequest,
    DesktopCommandCreateRequest,
    DesktopCommandRead,
)

router = APIRouter(prefix="/console/desktop-agent", tags=["console-desktop-agent"])


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


def _principal_org(principal: dict) -> UUID:
    raw = str(principal.get("org") or "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Token missing organization scope")
    return _parse_uuid(raw, field="organization_id")


def _principal_user(principal: dict) -> UUID:
    raw = str(principal.get("sub") or "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Token missing user scope")
    return _parse_uuid(raw, field="user_id")


@router.post("/commands", response_model=DesktopCommandRead)
async def console_create_command(
    req: DesktopCommandCreateRequest,
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permissions(principal, ["desktop.commands.create"])

    org_id = _principal_org(principal)
    try:
        require_org_access(principal=principal, organization_id=org_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    agent_id = _parse_uuid(req.agent_id, field="agent_id")
    desktop_agent_routes._validate_command_args(req.action, req.args)

    policy = await load_effective_policy(session, organization_id=org_id)
    policy_error = validate_desktop_action(action=req.action, args=req.args, policy=policy)
    if policy_error:
        raise HTTPException(status_code=403, detail=policy_error)

    agent = await desktop_agent_routes._get_agent(session, organization_id=org_id, agent_id=agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    actor_user_id = _principal_user(principal)
    command_metadata = dict(req.metadata or {})
    if str(req.action).startswith("browser_"):
        approval = command_metadata.get("approval")
        approval_payload = dict(approval) if isinstance(approval, dict) else {}
        approval_payload["required"] = True
        approval_payload.setdefault("status", "pending")
        command_metadata["approval"] = approval_payload
        command_metadata["requires_human_approval"] = True

    command = DesktopCommand(
        organization_id=org_id,
        agent_id=agent_id,
        action=req.action,
        args=req.args,
        status="pending_approval",
        timeout_ms=req.timeout_ms,
        requested_by_user_id=actor_user_id,
        command_metadata=command_metadata,
    )
    session.add(command)
    await session.commit()
    await session.refresh(command)

    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=actor_user_id,
        action="desktop_agent.command.create",
        resource="desktop_command",
        resource_id=str(command.id),
        status="ok",
        metadata={"action": req.action, "agent_id": req.agent_id, "console": True},
    )

    return desktop_agent_routes._to_command_read(command)


@router.post("/commands/{command_id}/approve", response_model=DesktopCommandRead)
async def console_approve_command(
    command_id: str,
    req: DesktopCommandApproveRequest,
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permissions(principal, ["desktop.commands.approve"])

    org_id = _principal_org(principal)
    try:
        require_org_access(principal=principal, organization_id=org_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    cmd_uuid = _parse_uuid(command_id, field="command_id")
    command = await desktop_agent_routes._get_command(session, organization_id=org_id, command_id=cmd_uuid)
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    if command.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Command is not pending approval")

    actor_user_id = _principal_user(principal)
    command.status = "approved"
    command.approved_by_user_id = actor_user_id
    command.approved_at = datetime.now(timezone.utc)
    command.error = None
    await session.commit()
    await session.refresh(command)

    await desktop_agent_routes._dispatch_command_if_online(session, command=command)

    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=actor_user_id,
        action="desktop_agent.command.approve",
        resource="desktop_command",
        resource_id=str(command.id),
        status="ok",
        detail=req.note,
        metadata={"agent_id": str(command.agent_id), "action": command.action, "console": True},
    )
    result = await desktop_agent_routes._get_command_result(session, command_id=command.id)
    return desktop_agent_routes._to_command_read(command, result=result)
