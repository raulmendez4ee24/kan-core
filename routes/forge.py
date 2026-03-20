from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from brain.forge import GeneratedOfferRecord, list_generated_offers
from security import require_client_token_with_fallback as require_forge_token

router = APIRouter(prefix="/forge", tags=["forge"])


@router.get("/offers", response_model=list[GeneratedOfferRecord])
async def get_generated_offers(
    limit: int = Query(default=50, ge=1, le=500),
    _: str = Depends(require_forge_token),
):
    return list_generated_offers(limit=limit)
