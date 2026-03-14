from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.rbac import require_org_access
from brain.security_tokens import require_permissions, require_user_principal
from database import get_session
from models import ApiIntegration, Client
from schemas.console_llm import (
    ConsoleLlmConnectRequest,
    ConsoleLlmStatusResponse,
)
from tools.secret_store import load_integration_secret, store_integration_secret

router = APIRouter(prefix="/console/llm", tags=["console-llm"])


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


def _normalize_provider(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"openai"}:
        return token
    raise HTTPException(status_code=400, detail="Unsupported provider (only openai is supported now)")


async def _find_llm_integration(
    session: AsyncSession,
    *,
    organization_id: UUID,
    provider: str,
) -> ApiIntegration | None:
    names = [provider, f"{provider}_llm", f"{provider}_api"]
    stmt = (
        select(ApiIntegration)
        .where(
            ApiIntegration.client_id == organization_id,
            ApiIntegration.name.in_(names),
        )
        .order_by(ApiIntegration.updated_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


@router.get("/status", response_model=ConsoleLlmStatusResponse)
async def console_llm_status(
    provider: str = "openai",
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permissions(principal, ["org.policies.read"])
    org_id = _principal_org(principal)
    try:
        require_org_access(principal=principal, organization_id=org_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    provider_name = _normalize_provider(provider)
    integ = await _find_llm_integration(session, organization_id=org_id, provider=provider_name)
    if not integ:
        model_name = None
        client = await session.get(Client, org_id)
        if client and client.model_name:
            model_name = str(client.model_name)
        return ConsoleLlmStatusResponse(
            provider=provider_name,
            configured=False,
            integration_name=None,
            model_name=model_name,
            secret_backend=None,
        )

    metadata = integ.integration_metadata or {}
    model_name = None
    if isinstance(metadata.get("model_name"), str):
        model_name = str(metadata.get("model_name"))
    else:
        client = await session.get(Client, org_id)
        if client and client.model_name:
            model_name = str(client.model_name)
    # Don't expose the secret value; only whether it is loadable.
    secret = await load_integration_secret(integ, client_id=str(org_id))
    return ConsoleLlmStatusResponse(
        provider=provider_name,
        configured=bool(secret),
        integration_name=integ.name,
        model_name=model_name,
        secret_backend=(
            str(metadata.get("secret_backend"))
            if isinstance(metadata.get("secret_backend"), str)
            else None
        ),
    )


@router.post("/connect", response_model=ConsoleLlmStatusResponse)
async def console_llm_connect(
    req: ConsoleLlmConnectRequest,
    _: None = Depends(_ensure_enabled),
    principal: dict = Depends(require_user_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permissions(principal, ["org.policies.write"])
    org_id = _principal_org(principal)
    actor_user_id = _principal_user(principal)
    try:
        require_org_access(principal=principal, organization_id=org_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    provider = _normalize_provider(req.provider)
    api_key = str(req.api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")
    if provider == "openai" and not api_key.startswith("sk-"):
        raise HTTPException(status_code=400, detail="Invalid OpenAI API key format")

    integ = await _find_llm_integration(session, organization_id=org_id, provider=provider)
    if not integ:
        integ = ApiIntegration(
            client_id=org_id,
            name=provider,
            base_url=("https://api.openai.com/v1" if provider == "openai" else ""),
            auth_type="bearer",
            header_name=None,
            docs_url=("https://platform.openai.com/docs" if provider == "openai" else None),
            openapi_url=None,
            openapi_spec=None,
            integration_metadata={"purpose": "llm_provider", "provider": provider},
            is_active=True,
        )
        session.add(integ)
        await session.flush()

    metadata = dict(integ.integration_metadata or {})
    metadata["purpose"] = "llm_provider"
    metadata["provider"] = provider
    if req.model_name:
        metadata["model_name"] = str(req.model_name).strip()[:120]
    if req.metadata:
        metadata["config"] = dict(req.metadata)
    integ.integration_metadata = metadata
    integ.is_active = True

    backend = await store_integration_secret(
        integ,
        client_id=str(org_id),
        integration_name=integ.name,
        secret=api_key,
    )
    metadata = dict(integ.integration_metadata or {})
    metadata["secret_backend"] = backend
    integ.integration_metadata = metadata

    client = await session.get(Client, org_id)
    if client:
        prefs = dict(client.llm_preferences or {})
        prefs["provider"] = provider
        prefs["provider_integration"] = integ.name
        client.llm_preferences = prefs
        if req.model_name and str(req.model_name).strip():
            client.model_name = str(req.model_name).strip()[:120]

    await session.commit()
    await session.refresh(integ)

    await write_audit_log(
        session,
        organization_id=org_id,
        actor_user_id=actor_user_id,
        action="console.llm.connect",
        resource="api_integration",
        resource_id=str(integ.id),
        status="ok",
        metadata={
            "provider": provider,
            "integration_name": integ.name,
            "secret_backend": backend,
            "model_name": (str(req.model_name).strip()[:120] if req.model_name else None),
        },
    )

    effective_model_name: str | None = None
    if req.model_name and str(req.model_name).strip():
        effective_model_name = str(req.model_name).strip()[:120]
    elif client and client.model_name:
        effective_model_name = str(client.model_name)
    elif isinstance(metadata.get("model_name"), str):
        effective_model_name = str(metadata.get("model_name"))

    return ConsoleLlmStatusResponse(
        provider=provider,
        configured=True,
        integration_name=integ.name,
        model_name=effective_model_name,
        secret_backend=backend,
    )
