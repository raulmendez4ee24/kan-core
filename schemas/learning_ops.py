from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class LearningOptimizeRunRequest(BaseModel):
    refresh_global: bool = True
    calibrate_org: bool = True
    # Optional override for platform admins in future; ignored for client-auth calls today.
    target_organization_id: Optional[str] = Field(default=None, max_length=64)


class LearningOptimizeRunResponse(BaseModel):
    status: str = "ok"
    refreshed: Dict[str, Any] = Field(default_factory=dict)
    calibrated: Dict[str, Any] = Field(default_factory=dict)

