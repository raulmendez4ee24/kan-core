import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import IntegrationRunLog, RoiSnapshot
from schemas.roi import RoiSummaryResponse
from security import require_client_token

router = APIRouter(prefix="/roi", tags=["roi"])


def _is_enabled(flag: str, default: str = "true") -> bool:
    return os.getenv(flag, default).lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.get("/summary", response_model=RoiSummaryResponse)
async def roi_summary(
    days: int = Query(default=30, ge=1, le=365),
    refresh: bool = Query(default=True),
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled("ENABLE_CRM_INTERNAL"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_CRM_INTERNAL disabled")

    org_id = _parse_uuid(client_id, field="client_id")
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=days)

    if refresh:
        run_stmt = select(IntegrationRunLog).where(
            IntegrationRunLog.client_id == org_id,
            IntegrationRunLog.created_at >= period_start,
            IntegrationRunLog.created_at <= period_end,
        )
        runs = list((await session.execute(run_stmt)).scalars().all())

        conversations_attended = len([x for x in runs if x.status in {"integrated", "saved_without_workflow"}])
        api_cost = sum(float(x.cost_usd or 0.0) for x in runs)
        appointments_generated = len([x for x in runs if x.function_name in {"calendar", "crm"} and x.status == "integrated"])
        conversion_rate = (appointments_generated / conversations_attended) if conversations_attended else 0.0

        revenue_generated = appointments_generated * float(os.getenv("ROI_APPOINTMENT_VALUE_USD", "55"))
        time_saved_minutes = conversations_attended * float(os.getenv("ROI_MINUTES_SAVED_PER_RUN", "8"))

        snapshot = RoiSnapshot(
            organization_id=org_id,
            period_start=period_start,
            period_end=period_end,
            revenue_generated_usd=round(revenue_generated, 6),
            api_cost_usd=round(api_cost, 6),
            time_saved_minutes=round(time_saved_minutes, 3),
            conversations_attended=conversations_attended,
            appointments_generated=appointments_generated,
            conversion_rate=round(conversion_rate, 6),
            roi_metadata={
                "days": days,
                "source": "integration_runs",
            },
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
    else:
        stmt = (
            select(RoiSnapshot)
            .where(RoiSnapshot.organization_id == org_id)
            .order_by(RoiSnapshot.period_end.desc())
            .limit(1)
        )
        snapshot = (await session.execute(stmt)).scalars().first()
        if not snapshot:
            raise HTTPException(status_code=404, detail="No ROI snapshot available")

    net_roi = float(snapshot.revenue_generated_usd or 0.0) - float(snapshot.api_cost_usd or 0.0)
    return RoiSummaryResponse(
        period_start=snapshot.period_start,
        period_end=snapshot.period_end,
        revenue_generated_usd=float(snapshot.revenue_generated_usd or 0.0),
        api_cost_usd=float(snapshot.api_cost_usd or 0.0),
        net_roi_usd=round(net_roi, 6),
        time_saved_minutes=float(snapshot.time_saved_minutes or 0.0),
        conversations_attended=int(snapshot.conversations_attended or 0),
        appointments_generated=int(snapshot.appointments_generated or 0),
        conversion_rate=float(snapshot.conversion_rate or 0.0),
        metadata=snapshot.roi_metadata or {},
    )
