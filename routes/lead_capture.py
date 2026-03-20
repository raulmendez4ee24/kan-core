"""Public lead capture endpoint for landing pages and web forms.

No authentication required — rate-limited by IP.
"""

import logging
import os
import time
from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from brain.crm_engine import upsert_lead
from database import get_session
from schemas.lead_capture import LeadCaptureRequest, LeadCaptureResponse

logger = logging.getLogger("kan_core.lead_capture")

router = APIRouter(prefix="/lead-capture", tags=["lead_capture"])

# ---------------------------------------------------------------------------
# Rate limiter (in-memory, per IP)
# ---------------------------------------------------------------------------

_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 10
_RATE_WINDOW = 60.0  # seconds


def _check_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    hits = _rate_store[ip]
    _rate_store[ip] = [t for t in hits if now - t < _RATE_WINDOW]
    if len(_rate_store[ip]) >= _RATE_LIMIT:
        return False
    _rate_store[ip].append(now)
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_enabled() -> bool:
    return os.getenv("ENABLE_LEAD_CAPTURE", "true").lower() in {"1", "true", "yes"}


def _default_org_id() -> UUID:
    for env_name in ("LEAD_CAPTURE_ORG_ID", "KAN_CLIENT_ID", "CLIENT_ID"):
        raw = str(os.getenv(env_name) or "").strip()
        if raw:
            try:
                return UUID(raw)
            except ValueError:
                continue
    raise HTTPException(
        status_code=503,
        detail="LEAD_CAPTURE_ORG_ID not configured",
    )


_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.options("/submit")
async def lead_capture_options():
    return Response(status_code=204, headers=_CORS_HEADERS)


@router.post("/submit", response_model=LeadCaptureResponse)
async def lead_capture_submit(
    req: LeadCaptureRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")

    client_ip = str(getattr(request.client, "host", "unknown"))
    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    if not req.email and not req.phone:
        raise HTTPException(status_code=400, detail="email or phone required")

    org_id = _default_org_id()

    source = req.source or req.utm_source or "landing"

    lead = await upsert_lead(
        session,
        organization_id=org_id,
        payload={
            "email": req.email,
            "phone": req.phone,
            "name": req.name,
            "source": source,
            "metadata": {
                "utm_source": req.utm_source,
                "utm_medium": req.utm_medium,
                "utm_campaign": req.utm_campaign,
                "utm_content": req.utm_content,
                "offer_id": req.offer_id,
                "capture_ip": client_ip,
                **(req.metadata or {}),
            },
        },
    )

    # Track in attribution engine (non-blocking)
    try:
        from brain.attribution_engine import track_lead_source

        await track_lead_source(
            lead_id=str(lead.id),
            channel=req.utm_medium or source,
            campaign_id=req.offer_id,
            utm_campaign=req.utm_campaign,
            source_campaign=req.utm_source,
            ad_name=req.utm_content,
        )
    except Exception as exc:
        logger.warning("lead_capture: attribution tracking failed: %s", exc)

    response = LeadCaptureResponse(ok=True, lead_id=str(lead.id))
    return Response(
        content=response.model_dump_json(),
        media_type="application/json",
        headers=_CORS_HEADERS,
    )
