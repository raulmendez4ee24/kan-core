from typing import Any, Dict, List

from pydantic import BaseModel, Field


class RuntimeHookRegisterRequest(BaseModel):
    event: str = Field(..., min_length=1, max_length=80)
    mode: str = Field(default="annotate", pattern="^(annotate|noop)$")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RuntimeHookRegistrationRead(BaseModel):
    event: str
    mode: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    organization_id: str
    version: int


class RuntimeHooksListResponse(BaseModel):
    events: List[str] = Field(default_factory=list)
    registrations: List[RuntimeHookRegistrationRead] = Field(default_factory=list)


class RuntimeHookRollbackRequest(BaseModel):
    target_version: int = Field(..., ge=1, le=100000)


class RuntimeHookRollbackResponse(BaseModel):
    event: str
    rolled_back_to_version: int
    new_active_version: int
    registration: RuntimeHookRegistrationRead
