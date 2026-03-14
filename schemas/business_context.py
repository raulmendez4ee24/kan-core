from typing import Any, Dict, List

from pydantic import BaseModel, Field


class BusinessContextRunRequest(BaseModel):
    window_hours: int = Field(default=24, ge=1, le=24 * 14)
    min_prev_sales: int = Field(default=3, ge=0, le=9999)
    sales_drop_threshold: float = Field(default=0.3, ge=0.05, le=0.95)
    create_recommendations: bool = True
    recommendation_cooldown_hours: int = Field(default=24, ge=1, le=24 * 14)
    execute_campaigns: bool = False
    followups_max_jobs: int = Field(default=30, ge=1, le=200)
    followups_dry_run: bool = True


class BusinessContextRunResponse(BaseModel):
    analysis: Dict[str, Any] = Field(default_factory=dict)
    created_recommendations: List[str] = Field(default_factory=list)
    campaign: Dict[str, Any] = Field(default_factory=dict)

