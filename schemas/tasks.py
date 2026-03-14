from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


TaskStatus = Literal["active", "paused", "archived"]
TaskTargetEnv = Literal["sandbox", "production"]
TaskDefinitionMode = Literal[
    "playbook",
    "mission",
    "integration_autopilot",
    "desktop_sequence",
    "browser_sequence",
]

TaskRunStatus = Literal["queued", "running", "paused", "completed", "failed", "cancelled"]
TaskRunTriggeredBy = Literal["schedule", "manual", "api", "ai_coo"]

TaskRunStepStatus = Literal["pending", "running", "completed", "failed", "skipped", "paused"]
TaskRunStepKind = Literal[
    "playbook",
    "desktop_command",
    "browser_command",
    "integration",
    "mission_execute",
    "wait",
    "n8n_webhook",
]


class TaskDefinition(BaseModel):
    mode: TaskDefinitionMode
    ref_id: Optional[str] = None
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    params: Dict[str, Any] = Field(default_factory=dict)


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: Optional[str] = None
    status: TaskStatus = "active"
    schedule_cron: Optional[str] = None
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    target_env: TaskTargetEnv = "production"
    require_sandbox_pass: bool = True
    definition: TaskDefinition
    capture_evidence: bool = True
    created_by_user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskPatchRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=240)
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    schedule_cron: Optional[str] = None
    timezone: Optional[str] = Field(default=None, min_length=1, max_length=64)
    target_env: Optional[TaskTargetEnv] = None
    require_sandbox_pass: Optional[bool] = None
    definition: Optional[TaskDefinition] = None
    capture_evidence: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


class TaskRead(BaseModel):
    id: str
    organization_id: str
    title: str
    description: Optional[str] = None
    status: TaskStatus
    schedule_cron: Optional[str] = None
    timezone: str
    target_env: TaskTargetEnv
    require_sandbox_pass: bool
    definition: Dict[str, Any] = Field(default_factory=dict)
    capture_evidence: bool
    created_by_user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class TaskListResponse(BaseModel):
    items: List[TaskRead] = Field(default_factory=list)


class TaskRunEnqueueRequest(BaseModel):
    environment: Optional[TaskTargetEnv] = None
    triggered_by: TaskRunTriggeredBy = "manual"
    correlation_id: Optional[str] = None


class TaskRunRead(BaseModel):
    id: str
    organization_id: str
    task_id: str
    status: TaskRunStatus
    environment: TaskTargetEnv
    triggered_by: TaskRunTriggeredBy
    requested_by_user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    result: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class TaskRunStepRead(BaseModel):
    id: str
    organization_id: str
    run_id: str
    step_order: int
    kind: TaskRunStepKind
    title: str
    action: Optional[str] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    status: TaskRunStepStatus
    attempts: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Dict[str, Any] = Field(default_factory=dict)
    reasoning: Optional[str] = None
    evidence_before_asset_id: Optional[str] = None
    evidence_after_asset_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class TaskRunStepsResponse(BaseModel):
    items: List[TaskRunStepRead] = Field(default_factory=list)


class TaskRunControlResponse(BaseModel):
    run_id: str
    status: TaskRunStatus


class EvidenceSignedUrlResponse(BaseModel):
    asset_id: str
    url: str
    expires_at: datetime

