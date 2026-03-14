import os
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.tasks.task_engine import create_task as engine_create_task
from database import get_session
from models import Client, Objective
from schemas.packs import PackInstallResponse, PackRead, PacksListResponse
from security import require_client_token

router = APIRouter(prefix="/packs", tags=["packs"])


def _enabled() -> bool:
    return os.getenv("ENABLE_PACKS", "false").lower() in {"1", "true", "yes"}


def _ensure_enabled() -> None:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_PACKS disabled")


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


_PACKS: List[PackRead] = [
    PackRead(
        id="clinic",
        title="Pack Clinica (MVP)",
        description="Chat + seguimiento + base de integraciones para clinicas.",
        industry="clinics",
        version="1.0",
        metadata={"mvp": True},
    )
]


def _get_pack(pack_id: str) -> PackRead:
    pack_id = str(pack_id or "").strip().lower()
    for pack in _PACKS:
        if pack.id == pack_id:
            return pack
    raise HTTPException(status_code=404, detail="Pack not found")


async def _install_clinic_pack(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_user_id: UUID | None,
) -> PackInstallResponse:
    notes: List[str] = []
    created_task_ids: List[str] = []
    created_objective_ids: List[str] = []

    client = await session.get(Client, organization_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # System prompt tuned for clinics.
    clinic_prompt = (
        "Eres un asistente para una clinica. Tu objetivo es convertir mensajes en citas.\n"
        "Reglas:\n"
        "- Pide nombre, motivo de consulta, y disponibilidad.\n"
        "- Confirma fecha/hora y datos de contacto.\n"
        "- Si el usuario pide precio, solicita informacion minima y ofrece agendar.\n"
        "- Siempre escribe claro y breve.\n"
    )
    client.system_prompt = clinic_prompt
    await session.commit()
    notes.append("system_prompt actualizado para clinica")

    # Objective to feed autonomy cycles (optional).
    objective = Objective(
        organization_id=organization_id,
        goal="Aumentar citas",
        metric_key="appointments",
        target_value=None,
        status="active",
        autonomy_mode="confirm",
        priority=7,
        objective_metadata={"source": "pack:clinic"},
    )
    session.add(objective)
    await session.commit()
    await session.refresh(objective)
    created_objective_ids.append(str(objective.id))
    notes.append("objective creado: Aumentar citas")

    # A demo task: run integration autopilot (safe: may return manual actions required).
    try:
        task = await engine_create_task(
            session,
            organization_id=organization_id,
            title="Pack Clinica: Instalar automatizacion base",
            description="Instala y/o recomienda integraciones clave (WhatsApp, CRM, Calendar) para una clinica.",
            status="active",
            schedule_cron=None,
            timezone_name="UTC",
            target_env="production",
            require_sandbox_pass=False,
            definition={
                "mode": "integration_autopilot",
                "ref_id": None,
                "steps": [],
                "params": {
                    "request_text": "Configurar captura de leads y citas para clinica, con seguimiento y recordatorios.",
                    "desired_functions": ["meta", "crm", "calendar", "email"],
                    "industry": "clinics",
                },
            },
            capture_evidence=False,
            created_by_user_id=actor_user_id,
            metadata={"pack_id": "clinic"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    created_task_ids.append(str(task.id))
    notes.append("task creado: instalar automatizacion base")

    await write_audit_log(
        session,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action="packs.install",
        resource="pack",
        resource_id="clinic",
        status="ok",
        metadata={"created_task_ids": created_task_ids, "created_objective_ids": created_objective_ids},
    )

    return PackInstallResponse(
        pack_id="clinic",
        installed=True,
        created_task_ids=created_task_ids,
        created_objective_ids=created_objective_ids,
        notes=notes,
    )


@router.get("", response_model=PacksListResponse)
async def list_packs():
    _ensure_enabled()
    return PacksListResponse(items=_PACKS)


@router.post("/{pack_id}/install", response_model=PackInstallResponse)
async def install_pack(
    pack_id: str,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    _ensure_enabled()
    org_id = _parse_uuid(client_id, field="client_id")
    pack = _get_pack(pack_id)

    if pack.id == "clinic":
        return await _install_clinic_pack(session, organization_id=org_id, actor_user_id=None)
    raise HTTPException(status_code=400, detail="Unsupported pack")
