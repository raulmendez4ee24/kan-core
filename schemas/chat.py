from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=8000)
    channel: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    auto_dispatch_actions: bool = True
    human_approval_granted: bool = False
    approver_id: Optional[str] = None


class ChatMessageResponse(BaseModel):
    type: Literal["message", "action"]
    content: str
    action: Optional[Dict[str, Any]] = None
    action_dispatched: bool = False
    action_id: Optional[str] = None
    approval_required: bool = False
    approval_status: Literal["not_required", "pending", "approved"] = "not_required"
