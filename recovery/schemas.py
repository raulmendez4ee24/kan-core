from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


RecoveryErrorType = Literal[
    "none",
    "element_not_found",
    "incorrect_coordinates",
    "wrong_screen",
    "visible_error_message",
    "no_visual_change",
    "unknown",
]

RecoveryStrategy = Literal[
    "adjust_coordinates",
    "scroll_and_retry",
    "search_again",
    "wait_and_retry",
    "fallback_click_center",
    "retry_with_alternate_selector",
]


class ErrorAnalysisResult(BaseModel):
    success: bool
    error_type: RecoveryErrorType = "unknown"
    reason: str = Field(default="Unknown failure", max_length=3000)
    suggested_fix: RecoveryStrategy = "search_again"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw: Dict[str, Any] = Field(default_factory=dict)


class RecoveryLog(BaseModel):
    attempt: int = Field(..., ge=1)
    strategy: RecoveryStrategy
    command_id: Optional[str] = None
    status: Literal["success", "failed", "skipped"] = "failed"
    reason: Optional[str] = None
    error_type: Optional[RecoveryErrorType] = None
    command_result_status: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetryResult(BaseModel):
    success: bool
    command_id: Optional[str] = None
    attempts_used: int = Field(default=0, ge=0)
    final_strategy: Optional[RecoveryStrategy] = None
    final_reason: Optional[str] = None
    logs: List[RecoveryLog] = Field(default_factory=list)
