import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from brain.internal_assistant import ask_internal_knowledge, upload_document
from database import get_session
from schemas.internal_assistant import (
    InternalAskRequest,
    InternalAskResponse,
    InternalDocRead,
    InternalDocUploadRequest,
)
from security import require_client_token

router = APIRouter(prefix="/internal-assistant", tags=["internal-assistant"])


def _enabled() -> bool:
    return os.getenv("ENABLE_INTERNAL_ASSISTANT", "true").lower() in {"1", "true", "yes"}


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from exc


@router.post("/docs/upload", response_model=InternalDocRead)
async def internal_upload(
    req: InternalDocUploadRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_INTERNAL_ASSISTANT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    row = await upload_document(
        session,
        organization_id=org_id,
        title=req.title,
        source=req.source,
        text=req.text,
        metadata=req.metadata,
    )
    return InternalDocRead(
        id=str(row.id),
        title=row.title,
        source=row.source,
        metadata=row.doc_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/ask", response_model=InternalAskResponse)
async def internal_ask(
    req: InternalAskRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    if not _enabled():
        raise HTTPException(status_code=404, detail="Feature ENABLE_INTERNAL_ASSISTANT disabled")
    org_id = _parse_uuid(client_id, field="client_id")
    answer, matched_docs, query = await ask_internal_knowledge(
        session,
        organization_id=org_id,
        question=req.question,
        max_context_chunks=req.max_context_chunks,
        metadata=req.metadata,
    )
    return InternalAskResponse(
        answer=answer,
        matched_docs=matched_docs,
        query_id=str(query.id),
        created_at=query.created_at,
    )

