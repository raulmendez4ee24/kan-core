from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.memory import VectorMemory


class LocalPersistentMemory(VectorMemory):
    """Lightweight persistent fallback memory when external vector DB is unavailable."""

    _instance: Optional["LocalPersistentMemory"] = None

    def __init__(self, store_path: str) -> None:
        self._path = Path(store_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: List[Dict[str, Any]] = []
        self._load()

    @classmethod
    def get_instance(cls) -> "LocalPersistentMemory":
        if cls._instance is None:
            path = os.getenv("LOCAL_MEMORY_STORE", ".run/local_memory_store.json")
            cls._instance = cls(path)
        return cls._instance

    def _load(self) -> None:
        if not self._path.exists():
            self._rows = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._rows = list(data) if isinstance(data, list) else []
        except Exception:
            self._rows = []

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._rows, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {x for x in str(text or "").lower().replace("\n", " ").split(" ") if x}

    @classmethod
    def _score(cls, query: str, content: str) -> float:
        q = cls._tokens(query)
        c = cls._tokens(content)
        if not q or not c:
            return 0.0
        inter = len(q & c)
        union = len(q | c)
        return inter / max(1, union)

    async def search_context(
        self, query: str, *, top_k: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        f = dict(filters or {})
        scored: List[tuple[float, Dict[str, Any]]] = []
        for row in self._rows:
            if not isinstance(row, dict):
                continue
            meta = row.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
            # Filter by exact metadata match when provided.
            ok = True
            for key, val in f.items():
                if str(meta.get(key)) != str(val):
                    ok = False
                    break
            if not ok:
                continue
            content = str(row.get("content") or "")
            score = self._score(query, content)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [str(item.get("content") or "") for _, item in scored[: max(1, int(top_k))]]

    async def store_interaction(
        self,
        *,
        client_id: str,
        session_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        item = {
            "client_id": str(client_id),
            "session_id": str(session_id),
            "content": str(content or ""),
            "metadata": dict(metadata or {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # cap store size to avoid unbounded growth.
        self._rows.append(item)
        max_rows = max(500, int(os.getenv("LOCAL_MEMORY_MAX_ROWS", "5000")))
        if len(self._rows) > max_rows:
            self._rows = self._rows[-max_rows:]
        self._save()
