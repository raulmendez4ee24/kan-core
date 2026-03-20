from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from brain.content_publisher import ContentPublisher
from security import require_client_token_with_fallback as require_content_token

router = APIRouter(prefix="/content", tags=["content"])


@router.get("/scheduled")
async def get_scheduled_content(
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_content_token),
):
    publisher = ContentPublisher()
    rows = await publisher.list_scheduled_posts(limit=limit)
    filtered = [row.model_dump(mode="json") for row in rows if row.status in {"scheduled", "published"}]
    return {
        "items": filtered,
        "count": len(filtered),
    }
