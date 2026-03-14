from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TrialStartRequest(BaseModel):
    organization_name: str = Field(min_length=1, max_length=120)
    admin_email: str = Field(min_length=3, max_length=200)
    admin_password: str = Field(min_length=6, max_length=200)
    install_pack_id: Optional[str] = "clinic"


class TrialStartResponse(BaseModel):
    organization_id: str
    client_id: str
    client_token: str
    jwt_token: str
    jwt_expires_at: datetime
    notes: List[str] = Field(default_factory=list)


class TrialDemoResponse(BaseModel):
    organization_id: str
    pack_id: str
    how_to_test: List[str] = Field(default_factory=list)
    suggested_message: str
    metrics_hint: Dict[str, Any] = Field(default_factory=dict)

