from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ScreenElementDetection(BaseModel):
    element: str = Field(..., min_length=1, max_length=200)
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class VisionAnalyzeRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=2000)
    image_base64: Optional[str] = None
    image_mime_type: str = Field(default="image/png", max_length=120)
    agent_id: Optional[str] = None
    auto_click: bool = True
    click_button: Literal["left", "right", "middle"] = "left"
    clicks: int = Field(default=1, ge=1, le=5)
    include_all_detections: bool = False


class VisionAnalyzeResponse(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    element: str = Field(..., min_length=1, max_length=200)
    confidence: float = Field(..., ge=0.0, le=1.0)
    image_width: int = Field(..., ge=1)
    image_height: int = Field(..., ge=1)
    source: Literal["request", "desktop_agent_history"]
    command_created: bool = False
    command_id: Optional[str] = None
    command_status: Optional[str] = None
    detections: List[ScreenElementDetection] = Field(default_factory=list)
