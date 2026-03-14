from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class OrgPolicyUpsertRequest(BaseModel):
    policy: Dict[str, Any] = Field(default_factory=dict)


class OrgPolicyRead(BaseModel):
    organization_id: str
    policy: Dict[str, Any] = Field(default_factory=dict)
    updated_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

