from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


class AgentOrchestrationRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=2000)
    context: Dict[str, Any] = Field(default_factory=dict)
    agents: List[Literal["sales", "support", "operations", "marketing", "finance"]] = Field(
        default_factory=lambda: ["sales", "support", "operations"]
    )
    dry_run: bool = False


class AgentAction(BaseModel):
    agent: str
    action: str
    rationale: str
    tool: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class AgentOrchestrationResponse(BaseModel):
    goal: str
    strategy: str
    actions: List[AgentAction] = Field(default_factory=list)
    executed: bool
    execution_result: Dict[str, Any] = Field(default_factory=dict)
