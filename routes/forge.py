from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from brain.forge import GeneratedOfferRecord, list_generated_offers
from database import get_session
from security import validate_client_credentials

router = APIRouter(prefix="/forge", tags=["forge"])


def _default_client_id() -> str:
    for env_name in ("KAN_CLIENT_ID", "CLIENT_ID", "YCLOUD_DEFAULT_CLIENT_ID"):
        value = str(os.getenv(env_name) or "").strip()
        if value:
            return value
    return ""


async def require_forge_token(
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


@router.get("/offers", response_model=list[GeneratedOfferRecord])
async def get_generated_offers(
    limit: int = Query(default=50, ge=1, le=500),
    _: str = Depends(require_forge_token),
):
    return list_generated_offers(limit=limit)
