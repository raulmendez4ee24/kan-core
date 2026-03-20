from __future__ import annotations

import inspect
import logging
import os
from uuid import UUID
from collections import defaultdict
from typing import Any, Callable, DefaultDict, Dict, List

from sqlalchemy import select

from database import AsyncSessionLocal
from models import RuntimeHookRegistration

logger = logging.getLogger("kan_core.runtime_hooks")

HookCallable = Callable[[Dict[str, Any]], Any]


class RuntimeHooks:
    def __init__(self) -> None:
        self._hooks: DefaultDict[str, List[HookCallable]] = defaultdict(list)
        self._declarative: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._loaded_orgs: set[str] = set()
        self._persistence_enabled = (
            os.getenv("RUNTIME_HOOKS_PERSISTENCE", "true").lower() in {"1", "true", "yes"}
            and "PYTEST_CURRENT_TEST" not in os.environ
        )

    def register(self, event: str, hook: HookCallable) -> None:
        key = str(event or "").strip()
        if not key:
            raise ValueError("event is required")
        self._hooks[key].append(hook)

    def register_declarative(
        self,
        *,
        event: str,
        mode: str,
        metadata: Dict[str, Any],
        organization_id: str,
        version: int,
    ) -> None:
        key = str(event or "").strip()
        if not key:
            raise ValueError("event is required")
        self._declarative[key].append(
            {
                "organization_id": str(organization_id),
                "mode": str(mode or "annotate"),
                "metadata": dict(metadata or {}),
                "version": int(version),
            }
        )

    async def ensure_loaded(self, organization_id: str) -> None:
        org = str(organization_id or "").strip()
        if not org or not self._persistence_enabled or org in self._loaded_orgs:
            return
        try:
            org_uuid = UUID(org)
        except Exception:
            return
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(RuntimeHookRegistration)
                    .where(RuntimeHookRegistration.organization_id == org_uuid)
                    .order_by(RuntimeHookRegistration.created_at.asc())
                )
                rows = list((await session.execute(stmt)).scalars().all())
            self._replace_org_declarative(
                org,
                [
                    {
                        "event": row.event,
                        "mode": row.mode,
                        "metadata": dict(row.metadata_json or {}),
                        "organization_id": str(row.organization_id),
                        "version": int(row.version or 1),
                    }
                    for row in rows
                ],
            )
            self._loaded_orgs.add(org)
        except Exception:
            return

    def _replace_org_declarative(self, organization_id: str, rows: List[Dict[str, Any]]) -> None:
        org = str(organization_id or "").strip()
        if not org:
            return
        for event in list(self._declarative.keys()):
            defs = self._declarative.get(event) or []
            self._declarative[event] = [
                item for item in defs if str(item.get("organization_id") or "") != org
            ]
        for item in rows:
            event = str(item.get("event") or "").strip()
            if not event:
                continue
            self._declarative[event].append(
                {
                    "organization_id": org,
                    "mode": str(item.get("mode") or "annotate"),
                    "metadata": dict(item.get("metadata") or {}),
                    "version": int(item.get("version") or 1),
                }
            )

    async def reload_org(self, organization_id: str) -> None:
        org = str(organization_id or "").strip()
        if not org:
            return
        self._loaded_orgs.discard(org)
        await self.ensure_loaded(org)

    def list_events(self, organization_id: str | None = None) -> List[str]:
        keys = set(self._hooks.keys()) | set(self._declarative.keys())
        if not organization_id:
            return sorted(keys)
        org = str(organization_id).strip()
        filtered = set()
        for key in keys:
            if key in self._hooks:
                filtered.add(key)
                continue
            defs = self._declarative.get(key) or []
            if any(str(item.get("organization_id") or "") == org for item in defs):
                filtered.add(key)
        return sorted(filtered)

    def list_registrations(self, organization_id: str | None = None) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        org = str(organization_id).strip() if organization_id else None
        for event, defs in self._declarative.items():
            for item in defs:
                if org and str(item.get("organization_id") or "") != org:
                    continue
                rows.append(
                    {
                        "event": event,
                        "mode": str(item.get("mode") or "annotate"),
                        "metadata": dict(item.get("metadata") or {}),
                        "organization_id": str(item.get("organization_id") or ""),
                        "version": int(item.get("version") or 1),
                    }
                )
        rows.sort(key=lambda x: (x["event"], x["version"]))
        return rows

    async def run(self, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        key = str(event or "").strip()
        out = dict(payload or {})
        org_id = str(out.get("client_id") or out.get("organization_id") or "").strip()
        if org_id:
            await self.ensure_loaded(org_id)

        hooks = list(self._hooks.get(key) or [])
        declarative = list(self._declarative.get(key) or [])

        for hook in hooks:
            try:
                value = hook(out)
                if inspect.isawaitable(value):
                    value = await value  # type: ignore[assignment]
                if isinstance(value, dict):
                    out = value
            except Exception:
                logger.exception("runtime_hook_failed event=%s hook=%s", key, getattr(hook, "__name__", str(hook)))

        active_declarative: List[Dict[str, Any]] = []
        global_defs = [x for x in declarative if not str(x.get("organization_id") or "").strip()]
        if global_defs:
            active_declarative.extend(global_defs)
        if org_id:
            org_defs = [x for x in declarative if str(x.get("organization_id") or "").strip() == org_id]
            if org_defs:
                max_version = max(int(x.get("version") or 1) for x in org_defs)
                active_declarative.extend([x for x in org_defs if int(x.get("version") or 1) == max_version])

        for item in active_declarative:
            try:
                hook_org = str(item.get("organization_id") or "")
                if org_id and hook_org and hook_org != org_id:
                    continue
                mode = str(item.get("mode") or "annotate")
                meta = dict(item.get("metadata") or {})
                if mode == "noop":
                    continue
                if mode == "annotate":
                    annotations = out.get("annotations")
                    if not isinstance(annotations, list):
                        annotations = []
                    annotations.append(
                        {
                            "event": key,
                            "version": int(item.get("version") or 1),
                            **meta,
                        }
                    )
                    out["annotations"] = annotations
            except Exception:
                logger.exception("runtime_hook_declarative_failed event=%s", key)
        return out


runtime_hooks = RuntimeHooks()

# Official hook names for plugin authors.
HOOK_BEFORE_PROMPT = "before_prompt"
HOOK_BEFORE_MODEL = "before_model"
HOOK_AFTER_MODEL = "after_model"
HOOK_BEFORE_TOOL = "before_tool"
HOOK_AFTER_TOOL = "after_tool"
HOOK_BEFORE_COMPACTION = "before_compaction"
HOOK_AFTER_RUN = "after_run"
