"""Pydantic models for Instagram analytics responses."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PostMetrics(BaseModel):
    media_id: str
    caption: Optional[str] = None
    media_type: str = "IMAGE"
    timestamp: Optional[str] = None
    like_count: int = 0
    comments_count: int = 0


class PostInsights(BaseModel):
    media_id: str
    reach: int = 0
    saved: int = 0
    shares: int = 0
    total_interactions: int = 0
    engagement_rate: float = 0.0


class PostWithInsights(BaseModel):
    post: PostMetrics
    insights: PostInsights


class AccountInfo(BaseModel):
    ig_id: str
    username: str = ""
    name: str = ""
    biography: str = ""
    followers_count: int = 0
    follows_count: int = 0
    media_count: int = 0


class AccountReach(BaseModel):
    total_reach: int = 0
    avg_reach_per_day: float = 0.0
    best_day_reach: int = 0
    days_measured: int = 0


class InstagramIssue(BaseModel):
    code: str
    title: str
    severity: str  # HIGH, MEDIUM, LOW
    detail: str


class InstagramReport(BaseModel):
    account: AccountInfo
    reach: AccountReach
    posts: List[PostWithInsights] = Field(default_factory=list)
    issues: List[InstagramIssue] = Field(default_factory=list)
    aggregate: Dict[str, Any] = Field(default_factory=dict)
