from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AnalyticsUploadRequest(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=260)
    mime_type: str = Field(default="text/csv", max_length=120)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AnalyticsUploadRead(BaseModel):
    id: str
    file_name: str
    mime_type: str
    rows_count: int
    status: str
    created_at: datetime


class AnalyticsRunRequest(BaseModel):
    upload_id: Optional[str] = None
    objective: Optional[str] = Field(default=None, max_length=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AnalyticsInsightRead(BaseModel):
    id: str
    upload_id: Optional[str] = None
    status: str
    summary: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    recommendations: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None

