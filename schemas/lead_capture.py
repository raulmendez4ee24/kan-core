from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class LeadCaptureRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    offer_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LeadCaptureResponse(BaseModel):
    ok: bool
    lead_id: Optional[str] = None
    message: str = "Lead captured"
