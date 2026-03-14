from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


ChannelName = Literal["whatsapp", "instagram", "messenger", "email", "webchat", "other"]
Direction = Literal["inbound", "outbound"]


class OmnichannelIngestRequest(BaseModel):
    channel: ChannelName = "webchat"
    customer_id: str = Field(..., min_length=1, max_length=160)
    customer_name: Optional[str] = Field(default=None, max_length=200)
    customer_email: Optional[str] = Field(default=None, max_length=220)
    customer_phone: Optional[str] = Field(default=None, max_length=80)
    external_message_id: Optional[str] = Field(default=None, max_length=200)
    message: str = Field(..., min_length=1, max_length=10000)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OmnichannelReplyRequest(BaseModel):
    thread_id: Optional[str] = None
    customer_id: Optional[str] = None
    channel: ChannelName = "webchat"
    message: str = Field(..., min_length=1, max_length=10000)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OmnichannelMessageRead(BaseModel):
    id: str
    thread_id: str
    customer_id: str
    channel: str
    direction: Direction
    body: str
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class OmnichannelThreadRead(BaseModel):
    id: str
    customer_id: str
    primary_channel: str
    status: str
    last_message_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_message: Optional[OmnichannelMessageRead] = None


class OmnichannelInboxResponse(BaseModel):
    items: List[OmnichannelThreadRead] = Field(default_factory=list)


class CustomerProfileRead(BaseModel):
    id: str
    customer_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    preferred_channel: Optional[str] = None
    tags: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    thread_count: int = 0
    message_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

