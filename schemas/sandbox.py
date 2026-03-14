from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SandboxStep(BaseModel):
    """One iteration of the ReAct loop: Think → Act (dry-run) → Observe."""

    step_index: int
    thought: str
    action: Optional[Dict[str, Any]] = None  # ActionContract dict, None on FINAL_ANSWER
    observation: Optional[str] = None        # result of executing the tool
    is_final: bool = False                   # True when LLM emitted FINAL_ANSWER
    dry_run_result: Optional[Dict[str, Any]] = None  # raw payload sent to n8n
    observation_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    error: Optional[str] = None


class SandboxResult(BaseModel):
    """Full result of a LLM-in-sandbox ReAct run."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal: str
    steps: List[SandboxStep] = Field(default_factory=list)
    final_answer: Optional[str] = None
    committed: bool = False
    pending_commit: bool = False   # True if actions await manual confirmation
    iterations: int = 0
    total_tokens: int = 0
    stopped_reason: str = "final_answer"   # "final_answer" | "max_iterations" | "error"
    client_id: Optional[str] = None
    session_id: Optional[str] = None
