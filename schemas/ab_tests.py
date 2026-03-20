from typing import List, Optional

from pydantic import BaseModel, Field


class CreateABTestRequest(BaseModel):
    test_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    variants: List[str] = Field(..., min_length=2, max_length=10)
    metric: str = Field(default="reply_rate")


class ABTestResultVariant(BaseModel):
    variant: str
    total_sent: int = 0
    delivered: int = 0
    metric_value: Optional[float] = None


class ABTestResultsResponse(BaseModel):
    test_id: str
    name: str
    status: str
    metric: str
    variants: List[ABTestResultVariant] = Field(default_factory=list)
    winner: Optional[str] = None
