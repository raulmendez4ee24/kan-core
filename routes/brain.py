from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from brain.attribution_engine import get_latest_briefing, get_recent_briefings
from brain.meta_ads_autonomy import approve_pending_action, reject_pending_action
from brain.opportunity_pipeline import get_pipeline_by_status
from security import require_client_token

router = APIRouter(prefix="/brain", tags=["brain"])


@router.post("/trigger")
async def trigger_revenue_brain(request: Request, _: str = Depends(require_client_token)):
    scheduler = getattr(request.app.state, "revenue_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="RevenueBrain scheduler not initialized")
    briefing = await scheduler.run_daily_cycle()
    if briefing is None:
        raise HTTPException(status_code=500, detail="RevenueBrain daily_cycle failed")
    return {"status": "ok", "briefing": briefing.model_dump(mode="json")}


@router.get("/briefings")
async def list_briefings(
    limit: int = 7,
    _: str = Depends(require_client_token),
):
    records = await get_recent_briefings(limit=limit)
    return {
        "items": [record.model_dump(mode="json") for record in records],
        "count": len(records),
    }


@router.get("/briefings/latest")
async def latest_daily_briefing(_: str = Depends(require_client_token)):
    record = await get_latest_briefing("daily")
    if record is None:
        raise HTTPException(status_code=404, detail="No daily briefing found")
    return record.model_dump(mode="json")


@router.get("/opportunities")
async def list_opportunities(_: str = Depends(require_client_token)):
    pipeline = await get_pipeline_by_status()
    return {
        "statuses": {
            status: [record.model_dump(mode="json") for record in records]
            for status, records in pipeline.items()
        },
        "counts": {status: len(records) for status, records in pipeline.items()},
    }


@router.get("/meta-ads/diagnostic")
async def meta_ads_diagnostic(
    _: str = Depends(require_client_token),
):
    """Run full Meta Ads diagnostic: campaigns, findings, recommended actions."""
    try:
        from brain.meta_ads_operator.client import MetaAdsApiClient
        from brain.meta_ads_operator.engine import analyze_meta_ads_snapshot

        client = MetaAdsApiClient()
        snapshot = await client.fetch_snapshot()
        report = analyze_meta_ads_snapshot(snapshot)
        return report.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Meta Ads diagnostic failed: {exc}")


@router.post("/meta-ads/approve/{action_id}")
async def approve_meta_ads_action(
    action_id: str,
    _: str = Depends(require_client_token),
):
    record = await approve_pending_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Pending Meta Ads action not found")
    return {
        "status": "ok",
        "action": record,
    }


@router.post("/meta-ads/reject/{action_id}")
async def reject_meta_ads_action(
    action_id: str,
    _: str = Depends(require_client_token),
):
    record = await reject_pending_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Pending Meta Ads action not found")
    return {
        "status": "ok",
        "action": record,
    }
