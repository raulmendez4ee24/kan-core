"""SSE streaming chat endpoint.

POST /chat/stream — streams LLM tokens in real-time using Server-Sent Events.

The client receives a stream of:
    data: {"token": "<chunk>"}\n\n
    ...
    data: [DONE]\n\n

Usage (curl):
    curl -N -X POST /chat/stream \\
         -H "Authorization: Bearer <token>" \\
         -H "Content-Type: application/json" \\
         -d '{"message": "hola", "session_id": "abc"}'

Required env vars: same as /chat/message (LLM_PROVIDER, OPENAI_API_KEY, etc.)
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import AsyncGenerator
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from brain.core import CognitiveEngine
from observability import capture_exception
from security import require_client_token

router = APIRouter(prefix="/chat", tags=["chat"])

_rate_limit_store: dict = defaultdict(list)
_RATE_LIMIT_RPM = int(os.getenv("CHAT_RATE_LIMIT_RPM", "60"))


def _check_rate_limit(client_id: str) -> bool:
    now = time.monotonic()
    window = 60.0
    calls = _rate_limit_store[client_id]
    _rate_limit_store[client_id] = [t for t in calls if now - t < window]
    if len(_rate_limit_store[client_id]) >= _RATE_LIMIT_RPM:
        return False
    _rate_limit_store[client_id].append(now)
    return True


class StreamRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    session_id: str = Field(default="default", min_length=1, max_length=128)


@router.post("/stream")
async def chat_stream(
    req: StreamRequest,
    client_id: str = Depends(require_client_token),
):
    """Stream LLM tokens over Server-Sent Events (SSE).

    Clients should consume with `EventSource` or `ReadableStream` and
    look for `data: [DONE]` as the terminal event.
    """
    if not _check_rate_limit(client_id):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_RATE_LIMIT_RPM} requests/minute per client.",
            headers={"Retry-After": "60"},
        )

    try:
        engine = CognitiveEngine.get_instance()
    except Exception as exc:
        capture_exception(exc, {"source": "chat_stream", "client_id": client_id})
        raise HTTPException(status_code=502, detail="AI engine unavailable") from exc

    async def _generate() -> AsyncGenerator[str, None]:
        try:
            async for chunk in engine.stream_response(
                client_id=client_id,
                session_id=req.session_id,
                message=req.message,
            ):
                yield chunk
        except Exception as exc:
            capture_exception(exc, {"source": "chat_stream", "client_id": client_id})
            yield 'data: {"error": "stream failed"}\n\n'
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
