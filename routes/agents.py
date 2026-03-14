import os

from fastapi import APIRouter, Depends, HTTPException

from brain.agent_orchestrator import orchestrate_agents
from schemas.agents import AgentAction, AgentOrchestrationRequest, AgentOrchestrationResponse
from security import require_client_token

router = APIRouter(prefix="/agents", tags=["agents"])


def _is_enabled(flag: str, default: str = "true") -> bool:
    return os.getenv(flag, default).lower() in {"1", "true", "yes"}


@router.post("/orchestrate", response_model=AgentOrchestrationResponse)
async def orchestrate(
    req: AgentOrchestrationRequest,
    client_id: str = Depends(require_client_token),
):
    if not _is_enabled("ENABLE_MULTI_AGENT"):
        raise HTTPException(status_code=404, detail="Feature ENABLE_MULTI_AGENT disabled")

    result = await orchestrate_agents(
        organization_id=client_id,
        goal=req.goal,
        context=req.context,
        agents=[str(x) for x in req.agents],
        dry_run=req.dry_run,
    )
    return AgentOrchestrationResponse(
        goal=result["goal"],
        strategy=result["strategy"],
        actions=[AgentAction(**item) for item in result.get("actions", [])],
        executed=bool(result.get("executed", False)),
        execution_result=result.get("execution_result", {}),
    )
