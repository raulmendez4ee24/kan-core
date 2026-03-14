from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AlertRuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=160)
    metric_key: str = Field(..., min_length=1, max_length=120)
    operator: Literal["gt", "gte", "lt", "lte", "eq"] = "gte"
    threshold: float
    severity: Literal["info", "warning", "critical"] = "warning"
    channels: List[Literal["slack", "email", "n8n"]] = Field(default_factory=lambda: ["n8n"])
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AlertRuleRead(BaseModel):
    id: str
    name: str
    metric_key: str
    operator: str
    threshold: float
    severity: str
    channels: List[str] = Field(default_factory=list)
    is_active: bool
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AlertIncidentRead(BaseModel):
    id: str
    rule_id: Optional[str] = None
    incident_key: str
    title: str
    description: str
    severity: str
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
