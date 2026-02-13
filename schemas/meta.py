from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MetaMessage(BaseModel):
    mid: Optional[str] = None
    text: Optional[str] = None
    type: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    audio: Optional[Dict[str, Any]] = None
    location: Optional[Dict[str, Any]] = None

    class Config:
        extra = "allow"


class MetaMessaging(BaseModel):
    sender: Optional[Dict[str, Any]] = None
    recipient: Optional[Dict[str, Any]] = None
    timestamp: Optional[int] = None
    message: Optional[MetaMessage] = None

    class Config:
        extra = "allow"


class MetaChange(BaseModel):
    field: Optional[str] = None
    value: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class MetaEntry(BaseModel):
    id: Optional[str] = None
    time: Optional[int] = None
    changes: Optional[List[MetaChange]] = None
    messaging: Optional[List[MetaMessaging]] = None

    class Config:
        extra = "allow"


class MetaWebhook(BaseModel):
    object: Optional[str] = None
    entry: List[MetaEntry] = Field(default_factory=list)

    class Config:
        extra = "allow"
