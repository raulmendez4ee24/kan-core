from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Tuple


class SpecialistMemory:
    def __init__(self, *, max_items_per_specialist: int = 20) -> None:
        self._max_items = max(3, int(max_items_per_specialist))
        self._episodic: Dict[Tuple[str, str], Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self._max_items)
        )
        self._semantic: Dict[Tuple[str, str], Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self._max_items)
        )
        self._task: Dict[Tuple[str, str], Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self._max_items)
        )
        in_pytest = "PYTEST_CURRENT_TEST" in os.environ
        self._persistence_enabled = (not in_pytest) or ("SPECIALIST_MEMORY_STORE" in os.environ)
        default_store = ".run/specialist_memory.json"
        self._store_path = Path(os.getenv("SPECIALIST_MEMORY_STORE", default_store))
        self._dirty = False
        if self._persistence_enabled:
            self._load()

    def _load(self) -> None:
        if not self._store_path.exists():
            return
        try:
            payload = json.loads(self._store_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for key in ("episodic", "semantic", "task"):
            rows = payload.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                client_id = str(row.get("client_id") or "").strip()
                specialist = str(row.get("specialist") or "").strip()
                if not client_id or not specialist:
                    continue
                event = dict(row.get("event") or {})
                if key == "episodic":
                    self._episodic[(client_id, specialist)].append(event)
                elif key == "semantic":
                    self._semantic[(client_id, specialist)].append(event)
                else:
                    self._task[(client_id, specialist)].append(event)

    def _flush(self) -> None:
        if (not self._persistence_enabled) or (not self._dirty):
            return
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"episodic": [], "semantic": [], "task": []}
        for (client_id, specialist), events in self._episodic.items():
            for event in list(events):
                payload["episodic"].append(
                    {"client_id": client_id, "specialist": specialist, "event": event}
                )
        for (client_id, specialist), events in self._semantic.items():
            for event in list(events):
                payload["semantic"].append(
                    {"client_id": client_id, "specialist": specialist, "event": event}
                )
        for (client_id, specialist), events in self._task.items():
            for event in list(events):
                payload["task"].append(
                    {"client_id": client_id, "specialist": specialist, "event": event}
                )
        self._store_path.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        self._dirty = False

    def _semantic_hint(self, goal: str, metadata: Dict[str, Any]) -> str:
        tags = metadata.get("tags")
        if isinstance(tags, list) and tags:
            normalized = [str(x).strip().lower() for x in tags if str(x).strip()]
            if normalized:
                return ",".join(sorted(set(normalized))[:8])
        tokens = [x.strip().lower() for x in str(goal or "").replace("\n", " ").split(" ") if x.strip()]
        return " ".join(tokens[:12])

    def _task_signature(self, goal: str, metadata: Dict[str, Any]) -> str:
        action = str(metadata.get("action") or metadata.get("task_action") or "").strip()
        if action:
            return action
        goal_tokens = [x for x in str(goal or "").lower().split(" ") if x]
        return " ".join(goal_tokens[:6])

    def record(
        self,
        *,
        client_id: str,
        specialist: str,
        goal: str,
        status: str,
        confidence: float,
        metadata: Dict[str, Any],
    ) -> None:
        key = (str(client_id), str(specialist))
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "goal": str(goal or "")[:240],
            "status": str(status or "unknown"),
            "confidence": max(0.0, min(float(confidence or 0.0), 1.0)),
            "metadata": dict(metadata or {}),
            "semantic_hint": self._semantic_hint(goal, dict(metadata or {})),
            "task_signature": self._task_signature(goal, dict(metadata or {})),
        }
        self._episodic[key].append(event)
        self._semantic[key].append(
            {
                "ts": event["ts"],
                "semantic_hint": event["semantic_hint"],
                "status": event["status"],
                "confidence": event["confidence"],
            }
        )
        self._task[key].append(
            {
                "ts": event["ts"],
                "task_signature": event["task_signature"],
                "status": event["status"],
                "confidence": event["confidence"],
                "goal": event["goal"],
            }
        )
        self._dirty = True
        self._flush()

    def recall(
        self,
        *,
        client_id: str,
        specialist: str,
        goal: str,
        max_items: int = 6,
    ) -> Dict[str, List[Dict[str, Any]]]:
        key = (str(client_id), str(specialist))
        max_items = max(1, int(max_items))
        goal_lower = str(goal or "").lower()

        episodic_rows = list(self._episodic.get(key) or [])
        semantic_rows = list(self._semantic.get(key) or [])
        task_rows = list(self._task.get(key) or [])

        relevant_episodic = []
        for row in reversed(episodic_rows):
            g = str(row.get("goal") or "").lower()
            if goal_lower and goal_lower[:32] not in g and g[:32] not in goal_lower:
                continue
            relevant_episodic.append(row)
            if len(relevant_episodic) >= max_items:
                break

        relevant_semantic = []
        for row in reversed(semantic_rows):
            hint = str(row.get("semantic_hint") or "").lower()
            if goal_lower and hint and not any(token in hint for token in goal_lower.split(" ")[:4]):
                continue
            relevant_semantic.append(row)
            if len(relevant_semantic) >= max_items:
                break

        relevant_task = []
        for row in reversed(task_rows):
            sig = str(row.get("task_signature") or "").lower()
            if goal_lower and sig and sig not in goal_lower:
                continue
            relevant_task.append(row)
            if len(relevant_task) >= max_items:
                break

        if not relevant_episodic:
            relevant_episodic = episodic_rows[-max_items:]
        if not relevant_semantic:
            relevant_semantic = semantic_rows[-max_items:]
        if not relevant_task:
            relevant_task = task_rows[-max_items:]

        return {
            "episodic": list(relevant_episodic),
            "semantic": list(relevant_semantic),
            "task": list(relevant_task),
        }

    def summary(self, *, client_id: str, specialist: str) -> Dict[str, Any]:
        key = (str(client_id), str(specialist))
        rows = list(self._episodic.get(key) or [])
        if not rows:
            return {
                "count": 0,
                "success_rate": 0.0,
                "avg_confidence": 0.0,
                "recent_goals": [],
                "recent_failures": [],
                "memory_layers": {"episodic": 0, "semantic": 0, "task": 0},
            }
        success = [r for r in rows if str(r.get("status")) in {"ok", "completed", "success"}]
        failures = [r for r in rows if str(r.get("status")) in {"failed", "blocked"}]
        avg_conf = sum(float(r.get("confidence") or 0.0) for r in rows) / max(1, len(rows))
        return {
            "count": len(rows),
            "success_rate": round(len(success) / max(1, len(rows)), 4),
            "avg_confidence": round(avg_conf, 4),
            "recent_goals": [str(r.get("goal") or "") for r in rows[-5:]],
            "recent_failures": [str(r.get("goal") or "") for r in failures[-5:]],
            "memory_layers": {
                "episodic": len(self._episodic.get(key) or []),
                "semantic": len(self._semantic.get(key) or []),
                "task": len(self._task.get(key) or []),
            },
        }


specialist_memory = SpecialistMemory()
