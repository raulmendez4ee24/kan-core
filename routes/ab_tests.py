"""A/B testing API for outbound message variants."""

import os

from fastapi import APIRouter, Depends, HTTPException

from schemas.ab_tests import ABTestResultsResponse, CreateABTestRequest
from security import require_client_token

router = APIRouter(prefix="/ab-tests", tags=["ab_tests"])


def _is_enabled() -> bool:
    return os.getenv("ENABLE_AB_TESTING", "true").lower() in {"1", "true", "yes"}


def _get_hunter():
    from brain.revenue_operators.hunter import HunterOperator

    return HunterOperator()


@router.post("/create")
async def create_ab_test(
    req: CreateABTestRequest,
    client_id: str = Depends(require_client_token),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")

    hunter = _get_hunter()
    result = await hunter.create_ab_test(
        test_id=req.test_id,
        name=req.name,
        variants=req.variants,
        metric=req.metric,
    )
    return result


@router.get("/{test_id}/results", response_model=ABTestResultsResponse)
async def get_ab_test_results(
    test_id: str,
    client_id: str = Depends(require_client_token),
):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Feature disabled")

    hunter = _get_hunter()
    result = await hunter.get_ab_test_results(test_id)
    if not result:
        raise HTTPException(status_code=404, detail="Test not found")
    return result
