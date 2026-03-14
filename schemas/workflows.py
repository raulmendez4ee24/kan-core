from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowRecordingCreateRequest(BaseModel):
    agent_id: str
    title: str = Field(..., min_length=1, max_length=240)
    notes: Optional[str] = Field(default=None, max_length=2000)
    capture_policy: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowRecordingStopRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


class WorkflowRecordingRead(BaseModel):
    id: str
    organization_id: str
    agent_id: str
    title: str
    status: str
    created_by_user_id: Optional[str] = None
    started_at: datetime
    stopped_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    n8n_workflow_id: Optional[str] = None
    n8n_workflow_url: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class WorkflowRecordingStepRead(BaseModel):
    id: str
    recording_id: str
    organization_id: str
    agent_id: str
    step_order: int
    desktop_command_id: Optional[str] = None
    action: str
    args: Dict[str, Any] = Field(default_factory=dict)
    result_status: str
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class WorkflowRecordingDetailResponse(BaseModel):
    recording: WorkflowRecordingRead
    steps: List[WorkflowRecordingStepRead] = Field(default_factory=list)


class WorkflowCompileResponse(BaseModel):
    recording_id: str
    playbook_id: str
    step_count: int
    summary: str


class WorkflowPlaybookRead(BaseModel):
    id: str
    organization_id: str
    agent_id: Optional[str] = None
    recording_id: Optional[str] = None
    title: str
    version: int
    steps: Dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class WorkflowPlaybookRunRequest(BaseModel):
    agent_id: str
    dry_run: bool = False


class WorkflowPlaybookRunRead(BaseModel):
    id: str
    organization_id: str
    playbook_id: str
    agent_id: str
    status: str
    current_step: int
    result: Dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class WorkflowPlaybookRunResponse(BaseModel):
    run: WorkflowPlaybookRunRead
    requires_approval: bool = False
    pending_command_id: Optional[str] = None


class WorkflowExportN8NResponse(BaseModel):
    playbook_id: str
    workflow_id: Optional[str] = None
    workflow_url: Optional[str] = None
    created: bool = False
    detail: str

