from datetime import datetime
from typing import Any, Dict

from pydantic import BaseModel, Field


class RoiSummaryResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    revenue_generated_usd: float
    api_cost_usd: float
    net_roi_usd: float
    time_saved_minutes: float
    conversations_attended: int
    appointments_generated: int
    conversion_rate: float
    metadata: Dict[str, Any] = Field(default_factory=dict)
