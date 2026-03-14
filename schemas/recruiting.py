from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RecruitingCandidateInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: Optional[str] = Field(default=None, max_length=220)
    phone: Optional[str] = Field(default=None, max_length=80)
    cv_text: str = Field(..., min_length=10, max_length=50000)
    source: Optional[str] = Field(default=None, max_length=80)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RecruitingCandidatesImportRequest(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=140)
    candidates: List[RecruitingCandidateInput] = Field(default_factory=list)


class RecruitingCandidateRead(BaseModel):
    id: str
    job_id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    score: float
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None


class RecruitingImportResponse(BaseModel):
    job_id: str
    imported: int
    candidates: List[RecruitingCandidateRead] = Field(default_factory=list)


class RecruitingScreenRequest(BaseModel):
    must_have_keywords: List[str] = Field(default_factory=list)
    nice_to_have_keywords: List[str] = Field(default_factory=list)
    reject_keywords: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RecruitingScreenResponse(BaseModel):
    candidate_id: str
    score: float
    recommendation: str
    rationale: str
    dimensions: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RecruitingInterviewScheduleRequest(BaseModel):
    candidate_id: str
    scheduled_at: datetime
    channel: str = Field(default="meet", max_length=64)
    interviewer: Optional[str] = Field(default=None, max_length=160)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RecruitingInterviewRead(BaseModel):
    id: str
    candidate_id: str
    scheduled_at: datetime
    channel: str
    interviewer: Optional[str] = None
    status: str
    result: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RecruitingRankingResponse(BaseModel):
    job_id: str
    candidates: List[RecruitingCandidateRead] = Field(default_factory=list)
    generated_at: datetime

