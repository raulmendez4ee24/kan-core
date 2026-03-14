from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class PlanAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["browser", "desktop", "discover_apis", "wait", "analyze"]
    action: Optional[str] = None
    source: Optional[Literal["url", "browser", "terminal"]] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    url: Optional[str] = None
    browser_url: Optional[str] = None
    command: Optional[str] = None
    seconds: Optional[float] = Field(default=None, ge=0.1, le=120.0)
    description: Optional[str] = None


class AutonomyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(..., min_length=3, max_length=6000)
    actions: List[PlanAction] = Field(default_factory=list, max_length=50)
