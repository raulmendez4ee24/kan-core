from typing import List, Optional

from pydantic import BaseModel, Field


class FunnelResponse(BaseModel):
    source_key: str
    leads: int = 0
    conversations: int = 0
    replies: int = 0
    bookings: int = 0
    closes: int = 0
    revenue: float = 0.0
    spend: float = 0.0
    conversation_rate: Optional[float] = None
    reply_rate: Optional[float] = None
    booking_rate: Optional[float] = None
    close_rate: Optional[float] = None
    roas: Optional[float] = None
    roi: Optional[float] = None


class RoiResponse(BaseModel):
    key: str
    leads: int = 0
    conversations: int = 0
    replies: int = 0
    bookings: int = 0
    closes: int = 0
    revenue: float = 0.0
    spend: float = 0.0
    booking_rate: Optional[float] = None
    close_rate: Optional[float] = None
    roas: Optional[float] = None
    roi: Optional[float] = None


class SourceSummary(BaseModel):
    source_key: str
    channel: str
    lead_count: int
    total_spend: float


class SourcesListResponse(BaseModel):
    sources: List[SourceSummary] = Field(default_factory=list)
