"""Attribution API routes — funnel metrics, ROI, and source tracking."""

import os

from fastapi import APIRouter, Depends, HTTPException, Query

from brain import attribution_engine
from schemas.attribution import (
    FunnelResponse,
    RoiResponse,
    SourceSummary,
    SourcesListResponse,
)
from security import require_client_token

router = APIRouter(prefix="/attribution", tags=["attribution"])


def _is_enabled() -> bool:
    return os.getenv("ENABLE_ATTRIBUTION_API", "true").lower() in {"1", "true", "yes"}


@router.get("/funnel/{source_key}", response_model=FunnelResponse)
async def attribution_funnel(
    source_key: str,
    client_id: str = Depends(require_client_token),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")

    funnel = await attribution_engine.get_full_funnel(source_key)
    return FunnelResponse(
        source_key=funnel.source_key,
        leads=funnel.leads,
        conversations=funnel.conversations,
        replies=funnel.replies,
        bookings=funnel.bookings,
        closes=funnel.closes,
        revenue=funnel.revenue,
        spend=funnel.spend,
        conversation_rate=funnel.conversation_rate,
        reply_rate=funnel.reply_rate,
        booking_rate=funnel.booking_rate,
        close_rate=funnel.close_rate,
        roas=funnel.roas,
        roi=funnel.roi,
    )


@router.get("/roi/campaign/{campaign_id}", response_model=RoiResponse)
async def attribution_campaign_roi(
    campaign_id: str,
    client_id: str = Depends(require_client_token),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")

    roi = await attribution_engine.get_campaign_roi(campaign_id)
    return RoiResponse(
        key=roi.key,
        leads=roi.leads,
        conversations=roi.conversations,
        replies=roi.replies,
        bookings=roi.bookings,
        closes=roi.closes,
        revenue=roi.revenue,
        spend=roi.spend,
        booking_rate=roi.booking_rate,
        close_rate=roi.close_rate,
        roas=roi.roas,
        roi=roi.roi,
    )


@router.get("/roi/channel/{channel}", response_model=RoiResponse)
async def attribution_channel_roi(
    channel: str,
    client_id: str = Depends(require_client_token),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")

    roi = await attribution_engine.get_channel_roi(channel)
    return RoiResponse(
        key=roi.key,
        leads=roi.leads,
        conversations=roi.conversations,
        replies=roi.replies,
        bookings=roi.bookings,
        closes=roi.closes,
        revenue=roi.revenue,
        spend=roi.spend,
        booking_rate=roi.booking_rate,
        close_rate=roi.close_rate,
        roas=roi.roas,
        roi=roi.roi,
    )


@router.get("/sources", response_model=SourcesListResponse)
async def attribution_sources(
    limit: int = Query(default=200, ge=1, le=1000),
    client_id: str = Depends(require_client_token),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")

    rows = await attribution_engine.list_tracked_sources(limit=limit)
    return SourcesListResponse(
        sources=[
            SourceSummary(
                source_key=row["source_key"],
                channel=row["channel"],
                lead_count=row["lead_count"],
                total_spend=row["total_spend"],
            )
            for row in rows
        ]
    )
