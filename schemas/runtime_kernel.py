from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class RuntimeStartRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    goal: str = Field(..., min_length=3, max_length=4000)
    max_steps: int = Field(default=8, ge=1, le=50)
    execution_mode: Literal["safe", "balanced", "autonomous"] = "balanced"
    context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class RuntimeStartResponse(BaseModel):
    accepted: bool = True
    run_id: str
    session_id: str
    queued: int
    status: Literal["queued", "running", "completed", "failed", "blocked"]


class RuntimeWaitResponse(BaseModel):
    run_id: str
    session_id: str
    status: Literal["queued", "running", "completed", "failed", "blocked", "timeout", "not_found"]
    result: Dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class RuntimeReplayRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    context_patch: Dict[str, Any] = Field(default_factory=dict)
    max_steps: Optional[int] = Field(default=None, ge=1, le=50)
    execution_mode: Optional[Literal["safe", "balanced", "autonomous"]] = None
    dry_run: Optional[bool] = None
    allow_non_terminal: bool = False


class RuntimeReplayResponse(BaseModel):
    accepted: bool = True
    replay_of: str
    run_id: str
    session_id: str
    queued: int
    status: Literal["queued", "running", "completed", "failed", "blocked"]


class RuntimeEventItem(BaseModel):
    run_id: str
    session_id: str
    seq: int
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RuntimeEventsResponse(BaseModel):
    items: List[RuntimeEventItem] = Field(default_factory=list)


class RuntimeJournalItem(BaseModel):
    ts: str
    phase: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class RuntimeJournalResponse(BaseModel):
    run_id: str
    phase: Optional[str] = None
    count: int
    items: List[RuntimeJournalItem] = Field(default_factory=list)
