from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ContentPlanGenerateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=240)
    channel: str = Field(default="instagram", max_length=64)
    cadence: str = Field(default="weekly", max_length=64)
    brief: str = Field(..., min_length=10, max_length=10000)
    weeks: int = Field(default=1, ge=1, le=12)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContentPlanRead(BaseModel):
    id: str
    title: str
    channel: str
    cadence: str
    brief: str
    plan: Dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class ContentPublishRequest(BaseModel):
    plan_id: Optional[str] = None
    channel: str = Field(default="instagram", max_length=64)
    asset_type: str = Field(default="post", max_length=64)
    content: str = Field(..., min_length=5, max_length=20000)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContentAssetRead(BaseModel):
    id: str
    plan_id: Optional[str] = None
    asset_type: str
    channel: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: str
    published_at: Optional[datetime] = None
    created_at: datetime


class ContentPublishResponse(BaseModel):
    asset: ContentAssetRead
    dispatched: bool

