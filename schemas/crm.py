from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LeadUpsertRequest(BaseModel):
    external_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    industry: Optional[str] = None
    owner: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    score: Optional[float] = None
    conversion_probability: Optional[float] = None


class LeadPatchRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    industry: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    score: Optional[float] = None
    conversion_probability: Optional[float] = None


class LeadRead(BaseModel):
    id: str
    external_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str
    source: Optional[str] = None
    industry: Optional[str] = None
    owner: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    score: float
    conversion_probability: float
    last_interaction_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class InteractionCreateRequest(BaseModel):
    lead_id: Optional[str] = None
    channel: str = "chat"
    direction: str = "inbound"
    content: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InteractionRead(BaseModel):
    id: str
    lead_id: Optional[str] = None
    channel: str
    direction: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class FollowUpsRunRequest(BaseModel):
    max_jobs: int = Field(default=20, ge=1, le=200)
    dry_run: bool = False


class FollowUpJobRead(BaseModel):
    id: str
    lead_id: Optional[str] = None
    status: str
    scheduled_for: datetime
    executed_at: Optional[datetime] = None
    decision_reason: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] = Field(default_factory=dict)


class FollowUpsRunResponse(BaseModel):
    jobs: List[FollowUpJobRead] = Field(default_factory=list)
    executed: int
    skipped: int
