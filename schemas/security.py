from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TokenIssueRequest(BaseModel):
    user_id: str
    organization_id: Optional[str] = None
    role: str = "ORG_USER"
    permissions: List[str] = Field(default_factory=list)
    expires_in_minutes: int = Field(default=120, ge=5, le=1440)


class TokenIssueResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserPrincipal(BaseModel):
    user_id: str
    organization_id: Optional[str] = None
    role: str
    permissions: List[str] = Field(default_factory=list)
    exp: int


class AuditLogRead(BaseModel):
    id: str
    organization_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    action: str
    resource: str
    resource_id: Optional[str] = None
    status: str
    detail: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
