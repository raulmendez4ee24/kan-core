from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class IntegrationCreate(BaseModel):
    name: str
    base_url: str
    auth_type: str = "none"  # none|bearer|apiKey|basic|oauth2
    header_name: Optional[str] = None  # for apiKey in header
    secret: Optional[str] = None
    docs_url: Optional[str] = None
    openapi_url: Optional[str] = None
    openapi_spec: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IntegrationRead(BaseModel):
    id: str
    name: str
    base_url: str
    auth_type: str
    header_name: Optional[str] = None
    docs_url: Optional[str] = None
    openapi_url: Optional[str] = None
    openapi_spec: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    created_at: Any
    updated_at: Optional[Any] = None

    model_config = ConfigDict(from_attributes=True)


class EndpointSpec(BaseModel):
    name: str
    method: str
    path: str
    query: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None


class TriggerSpec(BaseModel):
    type: Literal["webhook", "cron"] = "webhook"
    config: Dict[str, Any] = Field(default_factory=dict)


class GenerateWorkflowRequest(BaseModel):
    endpoints: Optional[List[EndpointSpec]] = None
    openapi: Optional[Any] = None  # JSON object o JSON/YAML string
    openapi_url: Optional[str] = None
    openapi_spec: Optional[Dict[str, Any]] = None
    max_endpoints: int = Field(default=120, ge=1, le=1000)
    shard_enabled: bool = True
    max_nodes_per_workflow: int = Field(default=120, ge=10, le=1000)
    endpoint_selection_strategy: Literal["priority", "alphabetical", "as_is"] = "priority"
    trigger: Optional[TriggerSpec] = None


class IntegrationAutomationInput(BaseModel):
    name: str
    base_url: str
    auth_type: str = "none"  # none|bearer|apiKey|basic|oauth2
    header_name: Optional[str] = None
    secret: Optional[str] = None
    docs_url: Optional[str] = None
    openapi_url: Optional[str] = None
    openapi_spec: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    endpoints: Optional[List[EndpointSpec]] = None
    openapi: Optional[Any] = None
    max_endpoints: int = Field(default=120, ge=1, le=1000)
    shard_enabled: bool = True
    max_nodes_per_workflow: int = Field(default=120, ge=10, le=1000)
    endpoint_selection_strategy: Literal["priority", "alphabetical", "as_is"] = "priority"
    trigger: Optional[TriggerSpec] = None


class IntegrationAutomationRequest(BaseModel):
    integrations: List[IntegrationAutomationInput]
    continue_on_error: bool = True


class IntegrationAutomationResult(BaseModel):
    name: str
    status: Literal["created", "failed"]
    endpoint_count: int = 0
    source: Optional[str] = None
    workflowId: Optional[Any] = None
    url: Optional[str] = None
    shard_count: int = 1
    shard_workflows: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None


class IntegrationAutomationResponse(BaseModel):
    results: List[IntegrationAutomationResult] = Field(default_factory=list)


class ProviderCandidate(BaseModel):
    provider_id: str
    provider_name: str
    integration_name: str
    base_url: str
    docs_url: str
    openapi_url: Optional[str] = None
    auth_type: str
    header_name: Optional[str] = None
    registration_mode: Literal["manual", "api"] = "manual"
    signup_url: Optional[str] = None
    score: float
    comparison_notes: List[str] = Field(default_factory=list)


class IntegrationFunctionPlan(BaseModel):
    function_name: str
    needs_new_function: bool = True
    existing_integration: Optional[str] = None
    candidates: List[ProviderCandidate] = Field(default_factory=list)


class IntegrationAutopilotRequest(BaseModel):
    request_text: str = Field(..., min_length=1, max_length=8000)
    desired_functions: List[str] = Field(default_factory=list)
    industry: Optional[str] = None
    allow_auto_registration: bool = True
    auto_generate_workflow: bool = True
    enable_provider_fallback: bool = True
    auto_repair_max_attempts: int = Field(default=2, ge=1, le=5)
    continue_on_error: bool = True
    dry_run: bool = False
    supplied_api_keys: Dict[str, str] = Field(default_factory=dict)
    provider_allowlist: List[str] = Field(default_factory=list)
    provider_blocklist: List[str] = Field(default_factory=list)
    max_candidates_per_function: int = Field(default=3, ge=1, le=10)
    test_timeout_seconds: float = Field(default=6.0, ge=1.0, le=30.0)
    trigger: Optional[TriggerSpec] = None
    registration_profile: Dict[str, Any] = Field(default_factory=dict)


class IntegrationAutopilotResult(BaseModel):
    function_name: str
    provider_id: Optional[str] = None
    integration_name: Optional[str] = None
    status: Literal[
        "already_covered",
        "planned",
        "integrated",
        "saved_without_workflow",
        "manual_action_required",
        "failed",
    ]
    registration_status: Literal["not_needed", "auto_registered", "manual_required", "failed"] = "not_needed"
    key_status: Literal["not_needed", "obtained", "missing", "invalid", "not_tested"] = "not_needed"
    endpoint_test_status: Literal["not_run", "passed", "failed"] = "not_run"
    attempted_providers: List[str] = Field(default_factory=list)
    auto_repair_log: List[str] = Field(default_factory=list)
    selection_explanation: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    workflowId: Optional[Any] = None
    url: Optional[str] = None
    shard_count: int = 1
    shard_workflows: List[Dict[str, Any]] = Field(default_factory=list)
    saved_integration_id: Optional[str] = None
    reason: str
    next_actions: List[str] = Field(default_factory=list)


class IntegrationAutopilotResponse(BaseModel):
    detected_functions: List[str] = Field(default_factory=list)
    plans: List[IntegrationFunctionPlan] = Field(default_factory=list)
    results: List[IntegrationAutopilotResult] = Field(default_factory=list)


class RotateSecretRequest(BaseModel):
    secret: Optional[str] = None
    use_env_var: Optional[str] = None


class RotateSecretResponse(BaseModel):
    integration_name: str
    backend: Literal["database_encrypted", "vault"]
    rotated: bool
    detail: str


class DashboardSummary(BaseModel):
    active_integrations: int
    total_runs: int
    success_runs: int
    failed_runs: int
    success_rate: float
    avg_duration_ms: float
    total_cost_usd: float


class DashboardRunLog(BaseModel):
    function_name: str
    provider_id: Optional[str] = None
    integration_name: Optional[str] = None
    status: str
    attempt_number: int
    auto_repair_used: bool
    fallback_used: bool
    duration_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    created_at: Any


class DashboardMemoryItem(BaseModel):
    function_name: str
    provider_id: str
    industry: Optional[str] = None
    success_count: int
    failure_count: int
    avg_duration_ms: Optional[float] = None
    avg_cost_usd: Optional[float] = None
    last_status: Optional[str] = None
    last_error_code: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IntegrationDashboardResponse(BaseModel):
    summary: DashboardSummary
    runs: List[DashboardRunLog] = Field(default_factory=list)
    memory: List[DashboardMemoryItem] = Field(default_factory=list)


class WorkflowStepSpec(BaseModel):
    name: str
    integration_name: str
    method: str
    path: str
    query: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None


class GenerateComplexWorkflowRequest(BaseModel):
    name: str
    steps: List[WorkflowStepSpec]
    trigger: Optional[TriggerSpec] = None


class GenerateComplexWorkflowResponse(BaseModel):
    workflowId: Optional[Any] = None
    url: Optional[str] = None
    name: str
    step_count: int
