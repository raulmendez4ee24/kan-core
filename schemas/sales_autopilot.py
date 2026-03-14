from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


LeadStage = Literal["new", "contacted", "qualified", "proposal_sent", "appointment_scheduled", "won", "lost"]


class SalesAutopilotRunRequest(BaseModel):
    dry_run: bool = True
    max_actions: int = Field(default=30, ge=1, le=300)
    create_followups: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SalesAutopilotAction(BaseModel):
    action: str
    status: str
    reason: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class SalesAutopilotRunResponse(BaseModel):
    dry_run: bool
    scanned_leads: int
    action_count: int
    actions: List[SalesAutopilotAction] = Field(default_factory=list)
    generated_at: datetime


class SalesPipelineSummaryItem(BaseModel):
    stage: str
    count: int
    conversion_probability_avg: float


class SalesPipelineResponse(BaseModel):
    items: List[SalesPipelineSummaryItem] = Field(default_factory=list)
    total_leads: int
    updated_at: datetime


class SalesAdvanceRequest(BaseModel):
    stage: LeadStage
    score: Optional[float] = Field(default=None, ge=0, le=1)
    reason: Optional[str] = Field(default=None, max_length=2000)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SalesAdvanceResponse(BaseModel):
    lead_id: str
    stage: str
    score: float
    event_id: str
    updated_at: datetime

