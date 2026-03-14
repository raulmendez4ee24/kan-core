from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.context_engine import compact_context
from models import ConversationLog


def _compact_lines(lines: List[str], max_lines: int = 12) -> str:
    if not lines:
        return ""
    trimmed = [x.strip() for x in lines if x and x.strip()]
    if not trimmed:
        return ""
    keep = trimmed[-max_lines:]
    return "\n".join(f"- {item[:240]}" for item in keep)


def _token_budget_to_chars(token_budget: int) -> int:
    # Conservative 1 token ~= 4 chars for mixed English/Spanish payloads.
    return max(400, int(token_budget) * 4)


def _split_memory_layers(turns: List[ConversationLog]) -> tuple[List[str], List[str], List[str]]:
    semantic: List[str] = []
    episodic: List[str] = []
    task: List[str] = []
    for row in turns:
        role = str(row.role or "")
        text = str(row.content or "").strip()
        if not text:
            continue
        blob = f"{role}: {text}"[:340]
        low = text.lower()
        if role == "system_compaction" or role == "system":
            semantic.append(blob)
        elif any(token in low for token in ("action", "step", "execute", "browser_", "desktop_")):
            task.append(blob)
        else:
            episodic.append(blob)
    return semantic, episodic, task


async def load_latest_compaction(
    session: AsyncSession,
    *,
    client_id: UUID,
    session_id: str,
) -> Optional[str]:
    stmt = (
        select(ConversationLog.content)
        .where(
            ConversationLog.client_id == client_id,
            ConversationLog.session_id == session_id,
            ConversationLog.role == "system_compaction",
        )
        .order_by(ConversationLog.timestamp.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if not isinstance(row, str):
        return None
    value = row.strip()
    return value or None


async def maybe_persist_compaction(
    session: AsyncSession,
    *,
    client_id: UUID,
    session_id: str,
    recent_history: List[ConversationLog],
    every_n_messages: int = 10,
) -> Optional[str]:
    # Count non-compaction turns and compact every N turns.
    turns = [x for x in recent_history if str(x.role) in {"user", "assistant"}]
    if len(turns) < max(2, every_n_messages):
        return None
    if len(turns) % max(1, every_n_messages) != 0:
        return None

    token_budget = max(100, int(os.getenv("COMPACTION_TOKEN_BUDGET", "700")))
    max_chars = _token_budget_to_chars(token_budget)
    latest_user = ""
    for row in reversed(turns):
        if str(row.role) == "user":
            latest_user = str(row.content or "").strip()
            if latest_user:
                break
    query = latest_user or f"session {session_id}"

    semantic_chunks, episodic_chunks, task_chunks = _split_memory_layers(recent_history[-80:])
    compacted = compact_context(
        query=query,
        semantic_chunks=semantic_chunks,
        episodic_chunks=episodic_chunks,
        task_chunks=task_chunks,
        max_chars=max_chars,
    )
    summary = _compact_lines(compacted, max_lines=min(20, every_n_messages * 2))
    if not summary:
        return None

    retry_hint = ""
    fail_markers = [x for x in turns[-every_n_messages:] if "fail" in str(x.content or "").lower()]
    if fail_markers:
        retry_hint = "\nRetry hints:\n- Prioriza rutas que antes terminaron en success.\n- Evita repetir secuencias que ya fallaron."

    row = ConversationLog(
        client_id=client_id,
        session_id=session_id,
        role="system_compaction",
        content=(
            "Session compaction snapshot (persisted, budget-aware):\n"
            f"{summary}"
            f"{retry_hint}"
        ),
        timestamp=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    return row.content
