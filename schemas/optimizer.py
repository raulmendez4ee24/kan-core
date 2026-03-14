from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class GoalMetricsSnapshot(BaseModel):
    window_days: int
    total_conversations: int
    total_messages: int
    user_messages: int
    assistant_messages: int
    unanswered_messages: int
    leads_detected: int
    appointments_detected: int
    appointment_rate: float
    active_integrations: int
    active_functions: List[str] = Field(default_factory=list)
    total_runs: int
    successful_runs: int
    failed_runs: int
    error_rate: float
    avg_duration_ms: float
    api_cost_usd: float
    auto_repair_used: int
    fallback_used: int


class GoalRecommendation(BaseModel):
    id: str
    priority: Literal["high", "medium", "low"] = "medium"
    confidence: float = 0.7
    detected_issue: str
    proposal: str
    expected_impact: str
    desired_functions: List[str] = Field(default_factory=list)
    request_text: str
    requires_confirmation: bool = True


class GoalExecutionResult(BaseModel):
    recommendation_id: str
    status: Literal["implemented", "failed", "skipped"] = "implemented"
    requested_functions: List[str] = Field(default_factory=list)
    integrated_functions: List[str] = Field(default_factory=list)
    workflow_urls: List[str] = Field(default_factory=list)
    reason: Optional[str] = None
    raw_result: Dict[str, Any] = Field(default_factory=dict)


class GoalDrivenAnalyzeRequest(BaseModel):
    goal: str = Field(default="Quiero mas ventas.", min_length=3, max_length=2000)
    window_days: int = Field(default=30, ge=1, le=180)
    max_recommendations: int = Field(default=5, ge=1, le=15)
    include_llm_summary: bool = True
    auto_execute: bool = False
    max_auto_execute: int = Field(default=1, ge=1, le=5)
    min_confidence_to_execute: float = Field(default=0.8, ge=0.0, le=1.0)
    require_dry_run_before_execute: bool = True
    only_high_priority_auto_execute: bool = True
    block_when_error_rate_above: float = Field(default=45.0, ge=0.0, le=100.0)


class GoalDrivenAnalyzeResponse(BaseModel):
    goal: str
    summary: str
    metrics: GoalMetricsSnapshot
    recommendations: List[GoalRecommendation] = Field(default_factory=list)
    llm_summary: Optional[str] = None
    executions: List[GoalExecutionResult] = Field(default_factory=list)


class GoalDrivenImplementRequest(BaseModel):
    goal: str = Field(default="Quiero mas ventas.", min_length=3, max_length=2000)
    window_days: int = Field(default=30, ge=1, le=180)
    recommendation_ids: List[str] = Field(default_factory=list)
    max_recommendations: int = Field(default=5, ge=1, le=15)
    max_execute: int = Field(default=2, ge=1, le=10)
    min_confidence_to_execute: float = Field(default=0.8, ge=0.0, le=1.0)
    require_dry_run_before_execute: bool = True
    only_high_priority_auto_execute: bool = False
    block_when_error_rate_above: float = Field(default=45.0, ge=0.0, le=100.0)


class GoalDrivenImplementResponse(BaseModel):
    goal: str
    executed: int
    selected_recommendations: List[str] = Field(default_factory=list)
    executions: List[GoalExecutionResult] = Field(default_factory=list)
