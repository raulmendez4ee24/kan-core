from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class DesktopAutonomyRunRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    max_steps: int = Field(default=6, ge=1, le=30)
    execution_mode: Literal["safe", "balanced", "autonomous"] = "safe"
    start_url: Optional[str] = None
    agent_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    wait_for_results: bool = True
    step_timeout_seconds: float = Field(default=25.0, ge=5.0, le=120.0)
    enable_recovery: bool = True
    stop_on_failure: bool = True
    enable_self_healing: bool = True
    self_heal_max_attempts: int = Field(default=2, ge=1, le=5)
    quality_min_score: float = Field(default=0.65, ge=0.0, le=1.0)
    enforce_quality_gate: bool = True
    dynamic_risk_controls: bool = True


class DesktopAutonomyStep(BaseModel):
    step_index: int = Field(..., ge=1)
    phase: Literal["observe", "think", "act", "evaluate"]
    status: Literal["ok", "queued", "blocked", "failed", "skipped", "planned"]
    action: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    command_id: Optional[str] = None
    detail: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DesktopAutonomyRunResponse(BaseModel):
    run_id: str
    goal: str
    status: Literal["completed", "failed", "blocked", "dry_run", "runner_offline"]
    steps: List[DesktopAutonomyStep] = Field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DesktopAutonomyResearchRunRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    max_steps: int = Field(default=6, ge=1, le=30)
    execution_mode: Literal["safe", "balanced", "autonomous"] = "balanced"
    start_url: Optional[str] = None
    agent_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    wait_for_results: bool = True
    step_timeout_seconds: float = Field(default=25.0, ge=5.0, le=120.0)
    enable_recovery: bool = True
    stop_on_failure: bool = True
    enable_self_healing: bool = True
    self_heal_max_attempts: int = Field(default=2, ge=1, le=5)
    quality_min_score: float = Field(default=0.65, ge=0.0, le=1.0)
    enforce_quality_gate: bool = True
    dynamic_risk_controls: bool = True
    min_sources: int = Field(default=5, ge=1, le=25)
    max_results_per_query: int = Field(default=5, ge=1, le=10)
    include_brave_fallback: bool = True
    require_verified_sources: bool = True


class ResearchSourceItem(BaseModel):
    title: str
    url: str
    snippet: str = ""
    provider: str
    query: str
    published_at: Optional[str] = None


class DesktopAutonomyResearchSummary(BaseModel):
    queries: List[str] = Field(default_factory=list)
    providers_used: List[str] = Field(default_factory=list)
    total_sources: int = 0
    unique_sources: int = 0
    verified: bool = False
    notes: List[str] = Field(default_factory=list)
    top_sources: List[ResearchSourceItem] = Field(default_factory=list)


class DesktopAutonomyResearchRunResponse(BaseModel):
    status: Literal["completed", "blocked", "failed", "dry_run"]
    detail: str
    research: DesktopAutonomyResearchSummary
    execution: Optional[DesktopAutonomyRunResponse] = None
