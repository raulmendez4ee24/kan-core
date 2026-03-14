from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from schemas.desktop_autonomy import (
    DesktopAutonomyResearchSummary,
    DesktopAutonomyRunResponse,
)


class AutonomyV2RunRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    start_url: Optional[str] = None
    execution_mode: Literal["safe", "balanced", "autonomous"] = "balanced"
    max_steps: int = Field(default=8, ge=1, le=30)
    step_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)

    enable_recovery: bool = True
    stop_on_failure: bool = False
    enable_self_healing: bool = True
    self_heal_max_attempts: int = Field(default=3, ge=1, le=5)
    quality_min_score: float = Field(default=0.72, ge=0.0, le=1.0)
    enforce_quality_gate: bool = True
    dynamic_risk_controls: bool = True

    min_sources: int = Field(default=5, ge=1, le=25)
    max_results_per_query: int = Field(default=5, ge=1, le=10)
    include_brave_fallback: bool = True
    require_verified_sources: bool = True

    enable_learning: bool = True
    auto_optimize: bool = True
    context: Dict[str, Any] = Field(default_factory=dict)


class AutonomyV2AgentTrace(BaseModel):
    stage: Literal["planner", "researcher", "executor", "verifier", "optimizer"]
    status: Literal["ok", "warn", "failed", "skipped"]
    summary: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AutonomyV2RunResponse(BaseModel):
    run_id: str
    goal: str
    status: Literal["completed", "blocked", "failed", "dry_run"]
    summary: str
    traces: List[AutonomyV2AgentTrace] = Field(default_factory=list)
    research: Optional[DesktopAutonomyResearchSummary] = None
    execution: Optional[DesktopAutonomyRunResponse] = None
    quality: Dict[str, Any] = Field(default_factory=dict)
    benchmark: Dict[str, Any] = Field(default_factory=dict)
    learning: Dict[str, Any] = Field(default_factory=dict)
    next_actions: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AutonomyV2BenchmarkItem(BaseModel):
    run_id: str
    goal: str
    status: str
    quality_score: float
    quality_passed: bool
    latency_seconds: float
    sources: int
    planned_steps: int
    reliability_index: float
    timestamp: datetime
