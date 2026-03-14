from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

DesktopAction = Literal[
    "mover_mouse",
    "click",
    "type_text",
    "press_key",
    "open_program",
    "take_screenshot",
    "browser_launch",
    "browser_goto",
    "browser_click",
    "browser_fill",
    "browser_press",
    "browser_extract",
    "browser_screenshot",
    "browser_close",
]

DesktopCommandStatus = Literal[
    "pending_approval",
    "approved",
    "sent",
    "received",
    "completed",
    "failed",
    "cancelled",
]


class DesktopCommandCreateRequest(BaseModel):
    agent_id: str
    action: DesktopAction
    args: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=10000, ge=500, le=120000)
    requested_by_user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DesktopCommandApproveRequest(BaseModel):
    approver_user_id: Optional[str] = None
    note: Optional[str] = None


class DesktopCommandResultRead(BaseModel):
    id: str
    command_id: str
    status: Literal["success", "failed"]
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    received_at: datetime


class DesktopCommandRead(BaseModel):
    id: str
    organization_id: str
    agent_id: str
    action: DesktopAction
    args: Dict[str, Any] = Field(default_factory=dict)
    status: DesktopCommandStatus
    timeout_ms: int
    requested_by_user_id: Optional[str] = None
    approved_by_user_id: Optional[str] = None
    approved_at: Optional[datetime] = None
    issued_at: Optional[datetime] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None
    result: Optional[DesktopCommandResultRead] = None


class DesktopAgentRead(BaseModel):
    id: str
    organization_id: str
    agent_name: str
    environment: Literal["sandbox", "production"] = "production"
    status: Literal["online", "offline"]
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    last_heartbeat_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    disconnected_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class DesktopAgentsResponse(BaseModel):
    items: list[DesktopAgentRead] = Field(default_factory=list)


class WsCommandEnvelope(BaseModel):
    type: Literal["command"] = "command"
    command_id: str
    action: DesktopAction
    args: Dict[str, Any] = Field(default_factory=dict)
    approved: bool = True
    issued_at: str
    timeout_ms: int = 10000


class WsResultEnvelope(BaseModel):
    type: Literal["result"] = "result"
    command_id: str
    status: Literal["success", "failed"]
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class WsHeartbeatEnvelope(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    agent_id: str
    ts: str


class WsAckEnvelope(BaseModel):
    type: Literal["ack"] = "ack"
    command_id: Optional[str] = None
    status: Literal["received", "persisted", "error"]
    detail: Optional[str] = None


class WsRecordingControlEnvelope(BaseModel):
    type: Literal["recording_control"] = "recording_control"
    recording_id: str
    status: Literal["start", "stop", "pause", "resume"]
    mode: Literal["human"] = "human"
    redaction: Dict[str, Any] = Field(default_factory=dict)


class WsHumanEventEnvelope(BaseModel):
    type: Literal["human_event"] = "human_event"
    recording_id: str
    ts: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
