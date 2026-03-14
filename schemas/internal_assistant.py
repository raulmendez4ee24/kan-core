from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class InternalDocUploadRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    source: Optional[str] = Field(default=None, max_length=120)
    text: str = Field(..., min_length=20, max_length=200000)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InternalDocRead(BaseModel):
    id: str
    title: str
    source: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class InternalAskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=5000)
    max_context_chunks: int = Field(default=5, ge=1, le=20)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InternalAskResponse(BaseModel):
    answer: str
    matched_docs: List[Dict[str, Any]] = Field(default_factory=list)
    query_id: str
    created_at: datetime

