from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProcessAuditJobCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=240)
    input_text: str = Field(..., min_length=20, max_length=50000)
    documents: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProcessAuditFindingRead(BaseModel):
    id: str
    finding_type: str
    severity: str
    description: str
    estimated_impact: Optional[float] = None
    recommendation: Optional[str] = None
    created_at: datetime


class ProcessAuditJobRead(BaseModel):
    id: str
    title: str
    status: str
    findings_summary: Optional[str] = None
    estimated_roi: Optional[float] = None
    implementation_plan: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None
    findings: List[ProcessAuditFindingRead] = Field(default_factory=list)


class ProcessAuditRoiResponse(BaseModel):
    job_id: str
    estimated_roi: float
    annual_savings_estimate: float
    implementation_cost_estimate: float
    assumptions: Dict[str, Any] = Field(default_factory=dict)

