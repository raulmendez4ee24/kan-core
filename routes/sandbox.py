from __future__ import annotations

import os
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from brain.core import CognitiveEngine
from brain.llm_sandbox import LLMSandbox, _MAX_ITERATIONS
from brain.sandbox_executor import SandboxExecutor
from schemas.sandbox import SandboxResult
from security import require_client_token

router = APIRouter(prefix="/sandbox", tags=["sandbox"])

# In-memory store for pending sandbox runs awaiting manual commit.
# run_id → SandboxResult. Expires on process restart (stateless restart is OK
# since commits are idempotent and the client can re-run if needed).
_pending_runs: Dict[str, SandboxResult] = {}


class SandboxRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(default="sandbox-default", min_length=1, max_length=128)
    max_iterations: int = Field(default=_MAX_ITERATIONS, ge=1, le=20)
    auto_commit: bool = Field(default=True)
    dry_run_only: bool = Field(default=False)


@router.post("/run", response_model=SandboxResult)
async def sandbox_run(
    req: SandboxRunRequest,
    client_id: str = Depends(require_client_token),
):
    """Execute a goal in the ReAct sandbox loop.

    The LLM iterates Think → Act (dry-run) → Observe up to ``max_iterations``.
    If ``auto_commit=true`` and all action confidences meet the adaptive threshold,
    actions are committed automatically. Otherwise call ``POST /sandbox/commit/{run_id}``.
    """
    if os.getenv("ENABLE_LLM_SANDBOX", "false").lower() not in {"1", "true", "yes"}:
        # Sandbox can also be called directly even without the env flag — allow it.
        pass

    engine = CognitiveEngine.get_instance()
    sandbox = LLMSandbox(engine=engine)

    try:
        result = await sandbox.run(
            goal=req.goal,
            client_id=client_id,
            session_id=req.session_id,
            max_iterations=req.max_iterations,
            auto_commit=req.auto_commit,
            dry_run_only=req.dry_run_only,
        )
    except Exception as exc:
        from observability import capture_exception

        capture_exception(exc, {"source": "sandbox_run", "client_id": client_id})
        raise HTTPException(status_code=502, detail="Sandbox execution failed") from exc

    if result.pending_commit and not result.committed:
        _pending_runs[result.run_id] = result

    return result


@router.post("/commit/{run_id}", response_model=SandboxResult)
async def sandbox_commit(
    run_id: str,
    client_id: str = Depends(require_client_token),
):
    """Commit pending sandbox actions from a previous ``/sandbox/run`` response.

    After a dry-run loop, call this endpoint to execute all queued actions
    in production.  The run_id is found in the ``run_id`` field of
    ``SandboxResult``.
    """
    result = _pending_runs.get(run_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=(
                f"run_id '{run_id}' not found or already committed. "
                "Pending runs are held in memory and expire on server restart."
            ),
        )
    if result.client_id != client_id:
        raise HTTPException(
            status_code=403, detail="run_id belongs to a different client"
        )

    executor = SandboxExecutor(
        client_id=client_id,
        session_id=result.session_id or "sandbox-default",
    )
    action_steps = [s for s in result.steps if s.action and not s.is_final]

    try:
        committed_count = await executor.commit_all(action_steps)
    except Exception as exc:
        from observability import capture_exception

        capture_exception(
            exc,
            {"source": "sandbox_commit", "client_id": client_id, "run_id": run_id},
        )
        raise HTTPException(status_code=502, detail="Commit failed") from exc

    data = result.model_dump()
    data["committed"] = committed_count > 0
    data["pending_commit"] = committed_count == 0
    updated = SandboxResult(**data)

    _pending_runs.pop(run_id, None)
    return updated
