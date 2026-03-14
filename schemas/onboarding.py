from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


class ContactInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class AppointmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_at: Optional[datetime] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None


class IntegrationAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    status: Literal["has_access", "pending", "missing"] = "has_access"
    notes: Optional[str] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_token(value)


class OnboardingIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_id: Optional[str] = None
    client_id: Optional[str] = None
    company_name: Optional[str] = None
    contact: ContactInfo = Field(default_factory=ContactInfo)
    request_text: str = Field(..., min_length=1, max_length=8000)
    target_url: Optional[str] = None
    api_docs_urls: List[str] = Field(default_factory=list)
    business_type: Optional[str] = None
    objective: Optional[str] = None
    channels: List[str] = Field(default_factory=list)
    desired_capabilities: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    requested_appointment: Optional[AppointmentRequest] = None
    integrations: List[IntegrationAccess] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("channels", mode="before")
    @classmethod
    def normalize_channels(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [_normalize_token(str(item)) for item in value if str(item).strip()]

    @field_validator("desired_capabilities", mode="before")
    @classmethod
    def normalize_capabilities(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("api_docs_urls", mode="before")
    @classmethod
    def normalize_api_docs_urls(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [str(item).strip() for item in value if str(item).strip()]


class RequiredIntegration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str
    priority: Literal["high", "medium", "low"] = "medium"


class OnboardingIntakeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["needs_api_access", "ready_for_automation"]
    required_integrations: List[RequiredIntegration] = Field(default_factory=list)
    provided_integrations: List[str] = Field(default_factory=list)
    pending_integrations: List[str] = Field(default_factory=list)
    missing_integrations: List[str] = Field(default_factory=list)
    next_actions: List[str] = Field(default_factory=list)
    operator_message: str
    n8n_dispatched: bool = False
    website_processed: bool = False
    website_url: Optional[str] = None
    detected_api_docs: List[str] = Field(default_factory=list)
    ingestion_notes: List[str] = Field(default_factory=list)
