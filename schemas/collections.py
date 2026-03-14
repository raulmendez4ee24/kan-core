from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CollectionsRunRequest(BaseModel):
    dry_run: bool = True
    max_cases: int = Field(default=50, ge=1, le=500)
    min_amount_due: float = Field(default=50.0, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CollectionsCaseRead(BaseModel):
    id: str
    customer_id: Optional[str] = None
    lead_id: Optional[str] = None
    amount_due: float
    currency: str
    due_at: Optional[datetime] = None
    status: str
    risk_level: str
    strategy: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class CollectionsRunResponse(BaseModel):
    dry_run: bool
    scanned: int
    created_cases: int
    scheduled_actions: int
    cases: List[CollectionsCaseRead] = Field(default_factory=list)
    generated_at: datetime


class CollectionsAgreementRequest(BaseModel):
    amount: float = Field(..., ge=0)
    due_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CollectionsAgreementResponse(BaseModel):
    case_id: str
    status: str
    agreement: Dict[str, Any] = Field(default_factory=dict)
    action_id: str
    updated_at: datetime

