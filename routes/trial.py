import os
import secrets
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.organization_bootstrap import apply_policy_auto_preset
from brain.passwords import hash_password
from brain.security_tokens import issue_token
from database import get_session
from models import Client, OrgMembership, User
from routes.packs import _get_pack, _install_clinic_pack
from schemas.trial import TrialDemoResponse, TrialStartRequest, TrialStartResponse

router = APIRouter(prefix="/trial", tags=["trial"])


def _enabled() -> bool:
    return os.getenv("ENABLE_TRIAL_ONBOARDING", "false").lower() in {"1", "true", "yes"}


def _ensure_enabled() -> None:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_TRIAL_ONBOARDING disabled")


def _validate_trial_key(x_trial_key: Optional[str]) -> None:
    expected = os.getenv("TRIAL_SIGNUP_KEY")
    if not expected:
        return
    if x_trial_key != expected:
        raise HTTPException(status_code=401, detail="Invalid trial key")


def _normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


async def _unique_client_name(session: AsyncSession, base: str) -> str:
    base = str(base or "").strip()[:120] or "Trial"
    candidate = base
    for attempt in range(6):
        exists = (await session.execute(select(Client.id).where(Client.name == candidate).limit(1))).scalars().first()
        if not exists:
            return candidate
        suffix = secrets.token_hex(2)
        candidate = f"{base[:110]}-{suffix}"
    return f"{base[:110]}-{secrets.token_hex(3)}"


@router.post("/start", response_model=TrialStartResponse)
async def trial_start(
    req: TrialStartRequest,
    x_trial_key: Optional[str] = Header(None, alias="X-Trial-Key"),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled()
    _validate_trial_key(x_trial_key)

    org_name = str(req.organization_name or "").strip()
    if not org_name:
        raise HTTPException(status_code=400, detail="organization_name is required")

    email = _normalize_email(req.admin_email)
    if not email:
        raise HTTPException(status_code=400, detail="admin_email is required")

    existing_user = (await session.execute(select(User.id).where(User.email == email).limit(1))).scalars().first()
    if existing_user:
        raise HTTPException(status_code=409, detail="User already exists")

    # Create client/org.
    client_token = secrets.token_urlsafe(32)
    client = Client(
        id=uuid4(),
        name=await _unique_client_name(session, org_name),
        api_key_encrypted="",
        persona={},
        llm_preferences={},
        system_prompt=None,
        is_active=True,
    )
    try:
        client.set_api_key(client_token)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="MASTER_KEY missing or invalid") from exc

    session.add(client)

    # Create user + membership.
    user = User(
        id=uuid4(),
        organization_id=client.id,
        email=email,
        full_name=None,
        password_hash=hash_password(req.admin_password),
        is_active=True,
    )
    session.add(user)
    membership = OrgMembership(
        id=uuid4(),
        organization_id=client.id,
        user_id=user.id,
        role_id=None,
        is_owner=True,
    )
    session.add(membership)
    await session.commit()

    auto_policy = await apply_policy_auto_preset(
        session,
        organization_id=client.id,
        actor_user_id=user.id,
        source="trial.start",
    )

    # Issue JWT (ORG_ADMIN).
    token, exp = issue_token(
        user_id=str(user.id),
        organization_id=str(client.id),
        role="ORG_ADMIN",
        permissions=[],
        expires_in_minutes=int(os.getenv("CONSOLE_TOKEN_EXPIRES_IN_MINUTES", "240") or 240),
    )
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)

    notes: List[str] = [
        "Trial org created.",
        "Use X-Client-Id/X-Client-Token for client endpoints; use Bearer JWT for /console/* endpoints.",
    ]
    if auto_policy:
        notes.append(f"Policy preset auto-applied: {auto_policy}.")

    pack_id = str(req.install_pack_id or "").strip().lower()
    if pack_id:
        try:
            _ = _get_pack(pack_id)
        except HTTPException:
            pack_id = ""
        if pack_id == "clinic":
            try:
                await _install_clinic_pack(session, organization_id=client.id, actor_user_id=user.id)
                notes.append("Clinic pack installed.")
            except Exception as exc:
                notes.append(f"Clinic pack install failed: {exc}")

    await write_audit_log(
        session,
        organization_id=client.id,
        actor_user_id=user.id,
        action="trial.start",
        resource="client",
        resource_id=str(client.id),
        status="ok",
        metadata={"email": email, "pack_id": pack_id or None},
    )

    return TrialStartResponse(
        organization_id=str(client.id),
        client_id=str(client.id),
        client_token=client_token,
        jwt_token=token,
        jwt_expires_at=expires_at,
        notes=notes,
    )


@router.get("/{organization_id}/demo", response_model=TrialDemoResponse)
async def trial_demo(
    organization_id: str,
    pack_id: str = "clinic",
    x_trial_key: Optional[str] = Header(None, alias="X-Trial-Key"),
):
    _ensure_enabled()
    _validate_trial_key(x_trial_key)
    try:
        org_uuid = UUID(organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid organization_id") from exc

    pack_id = str(pack_id or "").strip().lower() or "clinic"
    _ = _get_pack(pack_id)

    return TrialDemoResponse(
        organization_id=str(org_uuid),
        pack_id=pack_id,
        how_to_test=[
            "1) Abre el webchat y escribe: 'Hola, quiero una cita'.",
            "2) Verifica que el bot pida nombre, motivo y disponibilidad.",
            "3) Ejecuta la Task del pack para instalar automatizaciones base.",
        ],
        suggested_message="Hola, quiero una cita para este viernes por la tarde.",
        metrics_hint={"tip": "Si instalaste el pack, revisa /tasks y corre el task de instalacion."},
    )
