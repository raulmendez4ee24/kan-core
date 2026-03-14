import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from brain.autonomous_case_manager import create_or_update_case
from brain.collections_engine import (
    apply_payment_agreement,
    list_collections_cases,
    run_collections_cycle,
)
from database import get_session
from schemas.collections import (
    CollectionsAgreementRequest,
    CollectionsAgreementResponse,
    CollectionsCaseRead,
    CollectionsRunRequest,
    CollectionsRunResponse,
)
from security import require_client_token

router = APIRouter(prefix="/collections", tags=["collections"])


def _enabled() -> bool:
    return os.getenv("ENABLE_COLLECTIONS_AI", "true").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


def _to_case_read(row) -> CollectionsCaseRead:
    return CollectionsCaseRead(
        id=str(row.id),
        customer_id=row.customer_id,
        lead_id=(str(row.lead_id) if row.lead_id else None),
        amount_due=float(row.amount_due or 0.0),
        currency=str(row.currency or "USD"),
        due_at=row.due_at,
        status=str(row.status),
        risk_level=str(row.risk_level),
        strategy=row.strategy,
        metadata=row.case_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/run", response_model=CollectionsRunResponse)
async def collections_run(
    req: CollectionsRunRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_COLLECTIONS_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    if (
        not req.dry_run
        and os.getenv("ENABLE_AUTONOMOUS_CASE_MANAGER", "false").lower() in {"1", "true", "yes"}
    ):
        case_result = await create_or_update_case(
            session,
            organization_id=org_id,
            case_type="collections_followthrough",
            current_goal="Ejecutar ciclo de collections",
            business_context={
                "source": "collections.run",
                "case_key": "collections_run",
                "max_cases": req.max_cases,
                "min_amount_due": req.min_amount_due,
                "metadata": req.metadata,
            },
            run_now=True,
        )
        run_payload = dict(case_result.get("run") or {})
        execution_result = dict(run_payload.get("execution_result") or {})
        return CollectionsRunResponse(
            dry_run=False,
            scanned=0,
            created_cases=1 if run_payload else 0,
            scheduled_actions=1 if execution_result else 0,
            cases=[],
            generated_at=(run_payload.get("step") or {}).get("created_at") or case_result["case"]["updated_at"],
        )
    result = await run_collections_cycle(
        session,
        organization_id=org_id,
        dry_run=req.dry_run,
        max_cases=req.max_cases,
        min_amount_due=req.min_amount_due,
    )
    return CollectionsRunResponse(
        dry_run=bool(result.get("dry_run", True)),
        scanned=int(result.get("scanned", 0)),
        created_cases=int(result.get("created_cases", 0)),
        scheduled_actions=int(result.get("scheduled_actions", 0)),
        cases=[_to_case_read(item) for item in (result.get("cases") or [])],
        generated_at=result.get("generated_at"),
    )


@router.get("/cases", response_model=list[CollectionsCaseRead])
async def collections_cases(
    limit: int = Query(default=100, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_COLLECTIONS_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    rows = await list_collections_cases(session, organization_id=org_id, limit=limit)
    return [_to_case_read(row) for row in rows]


@router.post("/cases/{case_id}/agreement", response_model=CollectionsAgreementResponse)
async def collections_agreement(
    case_id: str,
    req: CollectionsAgreementRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_COLLECTIONS_AI disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    case_uuid = _parse_uuid(case_id, field="case_id")
    try:
        result = await apply_payment_agreement(
            session,
            organization_id=org_id,
            case_id=case_uuid,
            amount=req.amount,
            due_at=req.due_at,
            note=req.note,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CollectionsAgreementResponse(**result)
