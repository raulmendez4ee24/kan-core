from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import InternalDoc, InternalDocChunk, InternalQuery


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chunk_text(text: str, *, max_chars: int = 1200, overlap: int = 120) -> List[str]:
    token = str(text or "").strip()
    if not token:
        return []
    out: List[str] = []
    i = 0
    n = len(token)
    while i < n:
        j = min(i + max_chars, n)
        out.append(token[i:j])
        if j >= n:
            break
        i = max(0, j - overlap)
    return out


def _score_chunk(question: str, chunk: str) -> float:
    q_tokens = {t for t in question.lower().split() if len(t) >= 3}
    if not q_tokens:
        return 0.0
    text = chunk.lower()
    hits = sum(1 for t in q_tokens if t in text)
    return hits / max(len(q_tokens), 1)


async def upload_document(
    session: AsyncSession,
    *,
    organization_id: UUID,
    title: str,
    source: str | None,
    text: str,
    metadata: Dict[str, Any],
) -> InternalDoc:
    doc = InternalDoc(
        organization_id=organization_id,
        title=str(title).strip(),
        source=(str(source).strip() if source else None),
        raw_text=str(text),
        doc_metadata=dict(metadata or {}),
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    chunks = _chunk_text(text)
    for idx, chunk in enumerate(chunks):
        row = InternalDocChunk(
            organization_id=organization_id,
            doc_id=doc.id,
            chunk_index=idx,
            content=chunk,
            chunk_metadata={},
        )
        session.add(row)
    await session.commit()
    return doc


async def ask_internal_knowledge(
    session: AsyncSession,
    *,
    organization_id: UUID,
    question: str,
    max_context_chunks: int,
    metadata: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], InternalQuery]:
    chunks = list(
        (
            await session.execute(
                select(InternalDocChunk, InternalDoc.title)
                .join(InternalDoc, InternalDoc.id == InternalDocChunk.doc_id)
                .where(InternalDocChunk.organization_id == organization_id)
                .order_by(desc(InternalDocChunk.created_at))
            )
        ).all()
    )
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for chunk, title in chunks:
        score = _score_chunk(question, chunk.content or "")
        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    "doc_id": str(chunk.doc_id),
                    "doc_title": str(title or ""),
                    "chunk_index": int(chunk.chunk_index or 0),
                    "score": round(score, 4),
                    "content": str(chunk.content or ""),
                },
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [item for _, item in scored[: max(1, min(max_context_chunks, 20))]]

    if not top:
        answer = (
            "No encontré suficiente contexto en documentos internos. "
            "Sube SOPs, políticas o manuales para responder mejor."
        )
    else:
        snippets = [f"- {item['doc_title']} (chunk {item['chunk_index']}): {item['content'][:220]}" for item in top]
        answer = (
            "Respuesta basada en conocimiento interno:\n"
            + "\n".join(snippets)
            + "\n\nSiguiente paso sugerido: valida con tu política interna antes de ejecutar cambios críticos."
        )

    q = InternalQuery(
        organization_id=organization_id,
        question=question,
        answer=answer,
        context={"matches": top, **(metadata or {})},
    )
    session.add(q)
    await session.commit()
    await session.refresh(q)
    return answer, top, q

