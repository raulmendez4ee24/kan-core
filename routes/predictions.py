import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.predictor import generate_lead_predictions
from brain.predictor_sales import (
    generate_best_contact_time_predictions,
    generate_lead_churn_predictions,
)
from database import get_session
from models import PredictionRecord
from schemas.predictions import (
    LeadBestContactTimeRead,
    LeadChurnPredictionRead,
    LeadPredictionRead,
    LeadsBestContactTimeResponse,
    LeadsChurnPredictionsResponse,
    LeadsPredictionsResponse,
)
from security import require_client_token

router = APIRouter(prefix="/predictions", tags=["predictions"])


def _is_enabled(flag: str, default: str = "true") -> bool:
    return os.getenv(flag, default).lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.get("/leads", response_model=LeadsPredictionsResponse)
async def lead_predictions(
    refresh: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")

    org_id = _parse_uuid(client_id, field="client_id")

    if refresh:
        records = await generate_lead_predictions(
            session,
            organization_id=org_id,
            limit=limit,
        )
    else:
        stmt = (
            select(PredictionRecord)
            .where(
                PredictionRecord.organization_id == org_id,
                PredictionRecord.entity_type == "lead",
                PredictionRecord.prediction_key == "conversion_probability",
            )
            .order_by(desc(PredictionRecord.created_at))
            .limit(limit)
        )
        records = list((await session.execute(stmt)).scalars().all())

    predictions = [
        LeadPredictionRead(
            lead_id=item.entity_id,
            conversion_probability=float(item.prediction_value or 0.0),
            confidence=float(item.confidence or 0.0),
            model_name=item.model_name,
            features=item.features or {},
            valid_until=item.valid_until,
            created_at=item.created_at,
        )
        for item in records
    ]
    return LeadsPredictionsResponse(predictions=predictions)


@router.get("/leads/churn", response_model=LeadsChurnPredictionsResponse)
async def lead_churn_predictions(
    refresh: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")
    if not _is_enabled("ENABLE_PREDICTIVE_AUTOMATION", "false"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_PREDICTIVE_AUTOMATION disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    if refresh:
        records = await generate_lead_churn_predictions(session, organization_id=org_id, limit=limit)
    else:
        stmt = (
            select(PredictionRecord)
            .where(
                PredictionRecord.organization_id == org_id,
                PredictionRecord.entity_type == "lead",
                PredictionRecord.prediction_key == "churn_risk",
            )
            .order_by(desc(PredictionRecord.created_at))
            .limit(limit)
        )
        records = list((await session.execute(stmt)).scalars().all())

    predictions = [
        LeadChurnPredictionRead(
            lead_id=item.entity_id,
            churn_risk=float(item.prediction_value or 0.0),
            confidence=float(item.confidence or 0.0),
            model_name=item.model_name,
            features=item.features or {},
            valid_until=item.valid_until,
            created_at=item.created_at,
        )
        for item in records
    ]
    return LeadsChurnPredictionsResponse(predictions=predictions)


@router.get("/leads/contact-time", response_model=LeadsBestContactTimeResponse)
async def lead_best_contact_time(
    refresh: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=500),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")
    if not _is_enabled("ENABLE_PREDICTIVE_AUTOMATION", "false"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_PREDICTIVE_AUTOMATION disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    if refresh:
        records = await generate_best_contact_time_predictions(session, organization_id=org_id, limit=limit)
    else:
        stmt = (
            select(PredictionRecord)
            .where(
                PredictionRecord.organization_id == org_id,
                PredictionRecord.entity_type == "lead",
                PredictionRecord.prediction_key == "best_contact_hour",
            )
            .order_by(desc(PredictionRecord.created_at))
            .limit(limit)
        )
        records = list((await session.execute(stmt)).scalars().all())

    predictions = [
        LeadBestContactTimeRead(
            lead_id=item.entity_id,
            best_contact_hour=int(float(item.prediction_value or 0.0)),
            confidence=float(item.confidence or 0.0),
            model_name=item.model_name,
            features=item.features or {},
            valid_until=item.valid_until,
            created_at=item.created_at,
        )
        for item in records
    ]
    return LeadsBestContactTimeResponse(predictions=predictions)
