from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/creative", tags=["creative-tracking"])
logger = logging.getLogger("kan_core.routes.creative_tracking")


def _require_tracking_api_key(
    x_api_key: str = Header("", alias="X-Api-Key"),
) -> None:
    """Reject requests when ``CREATIVE_TRACKING_API_KEY`` is configured and
    the caller doesn't provide a matching ``X-Api-Key`` header.

    When the env var is unset the endpoint is open (development mode) so local
    testing works without extra config.
    """
    expected = str(os.getenv("CREATIVE_TRACKING_API_KEY") or "").strip()
    if not expected:
        return
    if x_api_key != expected:
        logger.warning(
            "[CREATIVE_TRACKING] auth rejected: invalid or missing X-Api-Key"
        )
        raise HTTPException(status_code=403, detail="Forbidden — invalid API key")


class AttributionRequest(BaseModel):
    lead_id: str = Field(..., min_length=1)
    campaign_id: str = Field(..., min_length=1)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    source: str = Field(default="manual")


class AttributionResponse(BaseModel):
    ok: bool
    lead_id: str
    campaign_id: str
    confidence: float
    source: str


@router.post(
    "/attribute",
    response_model=AttributionResponse,
    dependencies=[Depends(_require_tracking_api_key)],
)
async def manual_attribution(req: AttributionRequest):
    """Register or override the campaign attribution for a lead.

    Intended for operators or n8n workflows that need to fix attribution
    when the ``ref:cmp-xyz`` tag is missing from the initial message.

    **Auth:** requires ``X-Api-Key`` header matching
    ``CREATIVE_TRACKING_API_KEY`` when that env var is set.
    """
    try:
        from brain.creative_attribution import register_attribution

        ok = register_attribution(
            lead_id=req.lead_id,
            campaign_id=req.campaign_id,
            source=req.source,
            confidence=req.confidence,
        )
        if not ok:
            raise HTTPException(
                status_code=422,
                detail="Attribution registration failed — check lead_id and campaign_id",
            )
        logger.info(
            "[CREATIVE_TRACKING] manual_attribution_registered lead_id=%s "
            "campaign_id=%s confidence=%.2f source=%s",
            req.lead_id,
            req.campaign_id,
            req.confidence,
            req.source,
        )
        return AttributionResponse(
            ok=True,
            lead_id=req.lead_id,
            campaign_id=req.campaign_id,
            confidence=req.confidence,
            source=req.source,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[CREATIVE_TRACKING] manual attribution failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class MetricsResponse(BaseModel):
    campaign_id: str
    impressions: int = 0
    clicks: int = 0
    conversations: int = 0
    replies: int = 0
    qualified_replies: int = 0
    meetings_scheduled: int = 0
    shows: int = 0
    closes: int = 0
    total_revenue_usd: float = 0.0
    ctr: Optional[float] = None
    response_rate: Optional[float] = None
    booking_rate: Optional[float] = None
    show_rate: Optional[float] = None
    close_rate: Optional[float] = None
    unattributed_events: int = 0


@router.get("/metrics/{campaign_id}", response_model=MetricsResponse)
async def get_campaign_metrics(campaign_id: str):
    """Return cached funnel metrics for a campaign."""
    try:
        from brain.creative_event_log import (
            get_cached_metrics,
            get_unattributed_event_count,
        )

        cached = get_cached_metrics(campaign_id)
        if not cached:
            raise HTTPException(
                status_code=404,
                detail=f"No metrics found for campaign_id={campaign_id}",
            )
        return MetricsResponse(
            campaign_id=campaign_id,
            impressions=int(cached.get("impressions") or 0),
            clicks=int(cached.get("clicks") or 0),
            conversations=int(cached.get("conversations") or 0),
            replies=int(cached.get("replies") or 0),
            qualified_replies=int(cached.get("qualified_replies") or 0),
            meetings_scheduled=int(cached.get("meetings_scheduled") or 0),
            shows=int(cached.get("shows") or 0),
            closes=int(cached.get("closes") or 0),
            total_revenue_usd=float(cached.get("total_revenue_usd") or 0.0),
            ctr=cached.get("ctr"),
            response_rate=cached.get("response_rate"),
            booking_rate=cached.get("booking_rate"),
            show_rate=cached.get("show_rate"),
            close_rate=cached.get("close_rate"),
            unattributed_events=get_unattributed_event_count(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[CREATIVE_TRACKING] get_campaign_metrics failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
