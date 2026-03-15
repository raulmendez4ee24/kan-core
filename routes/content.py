from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from brain.content_publisher import ContentPublisher
from database import get_session
from security import validate_client_credentials

router = APIRouter(prefix="/content", tags=["content"])


def _default_client_id() -> str:
    for env_name in ("KAN_CLIENT_ID", "CLIENT_ID", "YCLOUD_DEFAULT_CLIENT_ID"):
        value = str(os.getenv(env_name) or "").strip()
        if value:
            return value
    return ""


async def require_content_token(
    x_client_token: str = Header(..., alias="X-Client-Token"),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    session: AsyncSession = Depends(get_session),
) -> str:
    client_id = str(x_client_id or "").strip() or _default_client_id()
    if not client_id:
        raise HTTPException(status_code=401, detail="Missing client id")
    return await validate_client_credentials(
        session,
        x_client_id=client_id,
        x_client_token=x_client_token,
    )


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
