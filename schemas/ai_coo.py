from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


ExecutionMode = Literal["safe", "semi_auto", "full_auto"]
ToolName = Literal["api", "browser", "desktop"]
RiskLevel = Literal["low", "medium", "high"]


class AiCooRunRequest(BaseModel):
    window_hours: int = Field(default=24, ge=1, le=24 * 14)
    dry_run: bool = True
    max_actions: int = Field(default=20, ge=1, le=200)
    force_execution_mode: Optional[ExecutionMode] = None


class AiCooActionPlanItem(BaseModel):
    action_type: str = Field(..., min_length=2, max_length=80)
    tool: ToolName
    risk: RiskLevel = "medium"
    requires_approval: bool = False
    reason: str = Field(default="", max_length=2000)
    payload: Dict[str, Any] = Field(default_factory=dict)


class AiCooRunResponse(BaseModel):
    organization_id: str
    computed_at: datetime
    execution_mode: ExecutionMode
    circuit_breaker_open: bool
    signals: Dict[str, Any] = Field(default_factory=dict)
    insights: List[Dict[str, Any]] = Field(default_factory=list)
    tool_snapshot: Dict[str, Any] = Field(default_factory=dict)
    action_plan: List[AiCooActionPlanItem] = Field(default_factory=list)
    executed: bool
    results: List[Dict[str, Any]] = Field(default_factory=list)

