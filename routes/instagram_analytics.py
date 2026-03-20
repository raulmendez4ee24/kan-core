"""Instagram Analytics API routes."""

import os

from fastapi import APIRouter, Depends, HTTPException

from schemas.instagram_analytics import InstagramReport
from security import require_client_token

router = APIRouter(prefix="/instagram", tags=["instagram_analytics"])


def _is_enabled() -> bool:
    return os.getenv("ENABLE_INSTAGRAM_ANALYTICS", "true").lower() in {"1", "true", "yes"}


def _get_analytics():
    from brain.instagram_analytics import InstagramAnalytics

    return InstagramAnalytics()


@router.get("/report", response_model=InstagramReport)
async def instagram_report(
    client_id: str = Depends(require_client_token),
):
    """Full Instagram analysis: account info, posts with insights, issues."""
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")
    ig = _get_analytics()
    return await ig.full_analysis()


@router.get("/posts")
async def instagram_posts(
    client_id: str = Depends(require_client_token),
):
    """Last 25 posts with engagement metrics."""
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")
    ig = _get_analytics()
    posts = await ig.fetch_recent_posts(limit=25)
    result = []
    for post in posts:
        insights = await ig.fetch_post_insights(post.media_id)
        result.append({"post": post.model_dump(), "insights": insights.model_dump()})
    return result


@router.get("/issues")
async def instagram_issues(
    client_id: str = Depends(require_client_token),
):
    """Detected Instagram issues and recommendations."""
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")
    ig = _get_analytics()
    report = await ig.full_analysis()
    return {"issues": [i.model_dump() for i in report.issues], "aggregate": report.aggregate}
