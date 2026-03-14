from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ConsoleLlmConnectRequest(BaseModel):
    provider: str = Field(default="openai")
    api_key: str = Field(..., min_length=8, max_length=4000)
    model_name: Optional[str] = Field(default=None, max_length=120)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConsoleLlmStatusResponse(BaseModel):
    provider: str
    configured: bool
    integration_name: Optional[str] = None
    model_name: Optional[str] = None
    secret_backend: Optional[str] = None

