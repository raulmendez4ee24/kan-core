from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ObjectiveCreateRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=2000)
    metric_key: str = Field(default="sales", min_length=1, max_length=120)
    target_value: Optional[float] = None
    autonomy_mode: Literal["recommend_only", "confirm", "autonomous"] = "confirm"
    priority: int = Field(default=5, ge=1, le=10)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ObjectiveUpdateRequest(BaseModel):
    goal: Optional[str] = Field(default=None, min_length=3, max_length=2000)
    metric_key: Optional[str] = Field(default=None, min_length=1, max_length=120)
    target_value: Optional[float] = None
    status: Optional[Literal["active", "paused", "completed", "archived"]] = None
    autonomy_mode: Optional[Literal["recommend_only", "confirm", "autonomous"]] = None
    priority: Optional[int] = Field(default=None, ge=1, le=10)
    metadata: Optional[Dict[str, Any]] = None


class ObjectiveRead(BaseModel):
    id: str
    goal: str
    metric_key: str
    target_value: Optional[float] = None
    status: str
    autonomy_mode: str
    priority: int
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class MonitorSnapshotResponse(BaseModel):
    window_minutes: int
    window_start: datetime
    window_end: datetime
    metrics: Dict[str, Any] = Field(default_factory=dict)


class GuardrailRollbackResponse(BaseModel):
    audit_log_id: str
    source_action: str
    rollback: Dict[str, Any] = Field(default_factory=dict)


class AutonomousCaseCreateRequest(BaseModel):
    case_type: Literal[
        "auto",
        "customer_recovery",
        "payment_followthrough",
        "ops_anomaly_response",
        "workflow_recovery",
        "campaign_execution",
        "sales_execution",
        "support_resolution",
        "onboarding_execution",
        "collections_followthrough",
    ]
    current_goal: str = Field(..., min_length=3, max_length=2000)
    business_context: Dict[str, Any] = Field(default_factory=dict)
    run_now: bool = False


class AutonomousCaseRead(BaseModel):
    id: str
    organization_id: str
    case_type: str
    status: str
    priority_score: float
    risk_score: float
    current_goal: str
    business_context: Dict[str, Any] = Field(default_factory=dict)
    world_state: Dict[str, Any] = Field(default_factory=dict)
    last_decision: Dict[str, Any] = Field(default_factory=dict)
    last_execution_result: Dict[str, Any] = Field(default_factory=dict)
    resolution_summary: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class AutonomousCaseStepRead(BaseModel):
    id: str
    case_id: str
    step_index: int
    status: str
    decision_payload: Dict[str, Any] = Field(default_factory=dict)
    execution_payload: Dict[str, Any] = Field(default_factory=dict)
    execution_result: Dict[str, Any] = Field(default_factory=dict)
    outcome_evaluation: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AutonomousCaseRunResponse(BaseModel):
    case: AutonomousCaseRead
    decision: Dict[str, Any] = Field(default_factory=dict)
    execution_result: Dict[str, Any] = Field(default_factory=dict)
    outcome_evaluation: Dict[str, Any] = Field(default_factory=dict)
    supervision: Dict[str, Any] = Field(default_factory=dict)
    step: Optional[AutonomousCaseStepRead] = None


class AutonomousCaseQueueItem(BaseModel):
    case: AutonomousCaseRead
    decision: Dict[str, Any] = Field(default_factory=dict)
    execution_result: Dict[str, Any] = Field(default_factory=dict)
    supervision: Dict[str, Any] = Field(default_factory=dict)


class AutonomousCaseQueueResponse(BaseModel):
    processed: List[AutonomousCaseQueueItem] = Field(default_factory=list)
    processed_count: int = 0
    remaining_count: int = 0
    remaining: List[AutonomousCaseRead] = Field(default_factory=list)


class CaseProgressRead(BaseModel):
    case_id: str
    organization_id: str
    current_goal: str
    active_domain: str
    last_completed_step: int = 0
    next_expected_step: str = ""
    last_successful_tool: str = ""
    last_failure: Dict[str, Any] = Field(default_factory=dict)
    retry_count_by_signature: Dict[str, Any] = Field(default_factory=dict)
    rollback_options: List[Any] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    artifacts_created: List[str] = Field(default_factory=list)
    updated_at: Optional[str] = None


class SkillRead(BaseModel):
    name: str
    path: str
    summary: str


class ToolCatalogItem(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    reversible: bool = True


class ToolCatalogResponse(BaseModel):
    items: List[ToolCatalogItem] = Field(default_factory=list)


class CaseResumeResponse(BaseModel):
    case: AutonomousCaseRead
    progress: CaseProgressRead
    run: Optional[AutonomousCaseRunResponse] = None


class BusinessStateRead(BaseModel):
    snapshot_id: Optional[str] = None
    organization_id: str
    generated_at: str
    active_objectives: int
    critical_risks: List[str] = Field(default_factory=list)
    cash_exposure: float
    customer_impact_score: float
    ops_health_score: float
    sla_pressure: float
    current_bottlenecks: Dict[str, Any] = Field(default_factory=dict)
    recommended_focus: str
    confidence: float
    metrics: Dict[str, Any] = Field(default_factory=dict)
    window_minutes: int


class RollbackableGuardrailRead(BaseModel):
    audit_log_id: str
    action: str
    status: str
    created_at: datetime
    operation_type: Optional[str] = None
    action_name: Optional[str] = None
    rollback_snapshot: Dict[str, Any] = Field(default_factory=dict)
    context_signature: Optional[str] = None


class CycleRunRequest(BaseModel):
    objective_ids: List[str] = Field(default_factory=list)
    window_days: int = Field(default=30, ge=1, le=180)
    max_recommendations: int = Field(default=5, ge=1, le=20)
    include_llm_summary: bool = True


class RecommendationRead(BaseModel):
    id: str
    objective_id: Optional[str] = None
    recommendation_key: str
    priority: str
    confidence: float
    detected_issue: str
    proposal: str
    expected_impact: str
    desired_functions: List[str] = Field(default_factory=list)
    request_text: str
    requires_confirmation: bool
    status: str
    reasoning: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CycleRunItem(BaseModel):
    objective_id: str
    objective_run_id: str
    summary: str
    llm_summary: Optional[str] = None
    recommendations: List[RecommendationRead] = Field(default_factory=list)


class CycleRunResponse(BaseModel):
    runs: List[CycleRunItem] = Field(default_factory=list)


class RecommendationDryRunResponse(BaseModel):
    recommendation_id: str
    status: Literal["ok", "blocked", "failed"]
    reason: str
    risk_score: float
    autopilot_preview: Dict[str, Any] = Field(default_factory=dict)


class RecommendationImplementRequest(BaseModel):
    force: bool = False
    confirm_sensitive: bool = False


class RecommendationImplementResponse(BaseModel):
    recommendation_id: str
    status: Literal["implemented", "blocked", "failed", "skipped"]
    reason: str
    result: Dict[str, Any] = Field(default_factory=dict)


class MissionStepRead(BaseModel):
    id: str
    step_order: int
    step_type: str
    title: str
    desired_functions: List[str] = Field(default_factory=list)
    request_text: Optional[str] = None
    status: str
    result: Dict[str, Any] = Field(default_factory=dict)


class MissionCreateRequest(BaseModel):
    objective_id: Optional[str] = None
    recommendation_id: Optional[str] = None
    title: str = Field(..., min_length=3, max_length=240)
    goal: str = Field(..., min_length=3, max_length=2000)
    autonomy_mode: Literal["recommend_only", "confirm", "autonomous"] = "confirm"
    context: Dict[str, Any] = Field(default_factory=dict)


class MissionRead(BaseModel):
    id: str
    title: str
    status: str
    autonomy_mode: str
    objective_id: Optional[str] = None
    recommendation_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    steps: List[MissionStepRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: Optional[datetime] = None


class MissionExecuteResponse(BaseModel):
    mission_id: str
    status: str
    executed_steps: int
    step_results: List[MissionStepRead] = Field(default_factory=list)


class GoalCreateRequest(BaseModel):
    goal_text: str = Field(..., min_length=3, max_length=2000)
    industry_hint: Optional[str] = Field(default=None, max_length=120)
    execution_mode: Optional[Literal["safe", "semi_auto", "full_auto"]] = None
    window_days: int = Field(default=30, ge=1, le=180)
    max_recommendations: int = Field(default=5, ge=1, le=20)
    include_llm_summary: bool = False


class GoalCreateResponse(BaseModel):
    objective_id: str
    mission_id: str
    summary: str
    created_recommendations: List[str] = Field(default_factory=list)
    next_actions: List[MissionStepRead] = Field(default_factory=list)
