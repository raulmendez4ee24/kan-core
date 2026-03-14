from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Sequence


@dataclass(frozen=True)
class ContextItem:
    text: str
    timestamp: datetime | None = None
    success_score: float = 0.5


def _tokenize(text: str) -> set[str]:
    return {x for x in (text or "").lower().replace("\n", " ").split(" ") if x}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / max(1, union)


def _recency_score(ts: datetime | None) -> float:
    if not ts:
        return 0.3
    now = datetime.now(timezone.utc)
    age_hours = max(0.0, (now - ts).total_seconds() / 3600.0)
    return 1.0 / (1.0 + math.log1p(age_hours))


def rank_context_items(*, query: str, items: Sequence[ContextItem]) -> List[ContextItem]:
    q_tokens = _tokenize(query)

    scored: List[tuple[float, ContextItem]] = []
    for item in items:
        text = (item.text or "").strip()
        if not text:
            continue
        relevance = _jaccard(q_tokens, _tokenize(text))
        recency = _recency_score(item.timestamp)
        success = max(0.0, min(item.success_score, 1.0))
        score = (0.65 * relevance) + (0.2 * recency) + (0.15 * success)
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored]


def compact_context(
    *,
    query: str,
    semantic_chunks: Iterable[str],
    episodic_chunks: Iterable[str],
    task_chunks: Iterable[str],
    max_chars: int,
) -> List[str]:
    # Build weighted items: task > episodic > semantic by success priors.
    all_items: List[ContextItem] = []
    all_items.extend(ContextItem(text=x, success_score=0.8) for x in task_chunks)
    all_items.extend(ContextItem(text=x, success_score=0.65) for x in episodic_chunks)
    all_items.extend(ContextItem(text=x, success_score=0.55) for x in semantic_chunks)

    ranked = rank_context_items(query=query, items=all_items)
    out: List[str] = []
    used = 0
    for item in ranked:
        text = item.text.strip()
        if not text:
            continue
        next_used = used + len(text) + 1
        if next_used > max_chars:
            continue
        out.append(text)
        used = next_used
    return out
