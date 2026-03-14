from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LeadPredictionRead(BaseModel):
    lead_id: str
    conversion_probability: float
    confidence: float
    model_name: str
    features: Dict[str, Any] = Field(default_factory=dict)
    valid_until: Optional[datetime] = None
    created_at: datetime


class LeadsPredictionsResponse(BaseModel):
    predictions: List[LeadPredictionRead] = Field(default_factory=list)


class LeadChurnPredictionRead(BaseModel):
    lead_id: str
    churn_risk: float
    confidence: float
    model_name: str
    features: Dict[str, Any] = Field(default_factory=dict)
    valid_until: Optional[datetime] = None
    created_at: datetime


class LeadsChurnPredictionsResponse(BaseModel):
    predictions: List[LeadChurnPredictionRead] = Field(default_factory=list)


class LeadBestContactTimeRead(BaseModel):
    lead_id: str
    best_contact_hour: int
    confidence: float
    model_name: str
    features: Dict[str, Any] = Field(default_factory=dict)
    valid_until: Optional[datetime] = None
    created_at: datetime


class LeadsBestContactTimeResponse(BaseModel):
    predictions: List[LeadBestContactTimeRead] = Field(default_factory=list)
