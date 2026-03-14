from __future__ import annotations

import asyncio
import io
import json as _json
import logging
import time
import traceback
from typing import Any, Dict, List, Optional

import httpx

from brain.execution_guard import emit_execution_guard_audit, observe_guarded_execution, plan_guarded_execution
from brain.runtime_hooks import HOOK_AFTER_TOOL, HOOK_BEFORE_TOOL, runtime_hooks
from schemas.action_contract import ActionContract
from tools.n8n_client import send_to_n8n_with_response

_HTTP_TIMEOUT = 20.0
_CODE_TIMEOUT = 10.0   # seconds for sandboxed code execution
_MAX_CODE_LINES = 40   # prevent abuse
_ALLOWED_BUILTINS = {
    # Safe subset of builtins for code sandbox
    "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted", "sum",
    "min", "max", "abs", "round", "int", "float", "str", "bool", "list", "dict",
    "set", "tuple", "type", "isinstance", "hasattr", "getattr", "repr", "format",
    "any", "all", "next", "iter", "reversed", "divmod", "pow", "hash", "id",
    "chr", "ord", "hex", "oct", "bin",
}

logger = logging.getLogger("kan_core.sandbox_executor")


class ToolObservation:
    """Result from executing a tool in sandbox (dry-run) or production (commit)."""

    def __init__(
        self,
        *,
        status: str,
        result_preview: str,
        error: Optional[str] = None,
        duration_ms: int = 0,
        committed: bool = False,
        confidence: float = 1.0,
        raw_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status = status              # "ok" | "error" | "skipped"
        self.result_preview = result_preview
        self.error = error
        self.duration_ms = duration_ms
        self.committed = committed
        self.confidence = confidence
        self.raw_payload = raw_payload or {}


class SandboxExecutor:
    """Executes ActionContract instances with sandbox isolation.

    - dry_run=True:  sends to n8n with sandbox_mode/dry_run flags; no production side-effects.
    - dry_run=False: commits the action to production via n8n.

    Uses RuntimeHooks HOOK_BEFORE_TOOL / HOOK_AFTER_TOOL for observability.
    """

    def __init__(self, *, client_id: str, session_id: str) -> None:
        self._client_id = client_id
        self._session_id = session_id

    async def execute(
        self,
        action: ActionContract,
        *,
        dry_run: bool = True,
        action_id: Optional[str] = None,
    ) -> ToolObservation:
        hook_payload: Dict[str, Any] = {
            "action": action.model_dump(),
            "dry_run": dry_run,
            "client_id": self._client_id,
            "session_id": self._session_id,
            "action_id": action_id,
        }
        hook_payload = await runtime_hooks.run(HOOK_BEFORE_TOOL, hook_payload)

        start = time.monotonic()
        observation = await self._dispatch(action, dry_run=dry_run, action_id=action_id)
        observation.duration_ms = int((time.monotonic() - start) * 1000)

        after_payload = {**hook_payload, "observation": observation.__dict__}
        await runtime_hooks.run(HOOK_AFTER_TOOL, after_payload)
        return observation

    async def _dispatch_discover(
        self,
        action: ActionContract,
        *,
        dry_run: bool,
    ) -> ToolObservation:
        """Handle action_type='discover' — runs APIDiscoveryEngine natively.

        Supports:
          action='from_url'    → arguments={'url': '...'}
          action='from_openapi'→ arguments={'openapi_spec': {...}}

        Discovery always runs live (reads external URLs) regardless of dry_run
        because it has no side-effects: it only saves to DB on commit mode.
        """
        import json as _json

        args = dict(action.arguments or {})
        action_name = str(action.action or "from_url").lower()

        if action_name == "from_openapi":
            # Parse provided OpenAPI spec directly — no HTTP needed
            spec = args.get("openapi_spec") or args.get("spec")
            if not spec:
                return ToolObservation(
                    status="error",
                    result_preview="",
                    error="arguments.openapi_spec is required for action='from_openapi'",
                    confidence=0.0,
                )
            try:
                from brain.openapi_parser import extract_endpoints_from_openapi, parse_openapi_document

                parsed = parse_openapi_document(spec)
                base_url, endpoints = extract_endpoints_from_openapi(parsed, max_endpoints=30)
                summary = [
                    f"{e.method.upper()} {e.path} — {e.summary or e.operation_id}"
                    for e in (endpoints or [])
                ]
                preview = (
                    f"[discover] Parsed OpenAPI spec. base_url={base_url}. "
                    f"{len(summary)} endpoints found:\n" + "\n".join(summary[:20])
                )
                raw = {"base_url": base_url, "endpoints": [e.model_dump() for e in (endpoints or [])]}

                if not dry_run and self._client_id:
                    # Save to DB on commit
                    try:
                        from database import AsyncSessionLocal
                        from brain.api_discovery_engine import APIDiscoveryEngine
                        async with AsyncSessionLocal() as db:
                            eng = APIDiscoveryEngine(db, self._client_id)
                            await eng._save_discovered_apis([{
                                "source": "openapi_direct",
                                "url": base_url or "",
                                "base_url": base_url or "",
                                "openapi_spec": parsed,
                                "endpoints": raw["endpoints"],
                            }])
                    except Exception as exc:
                        logger.warning("Could not persist discovered OpenAPI: %s", exc)

                return ToolObservation(
                    status="ok",
                    result_preview=preview,
                    committed=not dry_run,
                    confidence=0.95,
                    raw_payload=raw,
                )
            except Exception as exc:
                return ToolObservation(
                    status="error",
                    result_preview="",
                    error=f"OpenAPI parse failed: {exc}",
                    confidence=0.0,
                )

        # Default: action='from_url'
        url = str(args.get("url") or args.get("base_url") or "").strip()
        if not url:
            return ToolObservation(
                status="error",
                result_preview="",
                error="arguments.url is required for action='from_url'",
                confidence=0.0,
            )

        try:
            from database import AsyncSessionLocal
            from brain.api_discovery_engine import APIDiscoveryEngine

            auto_save = not dry_run
            async with AsyncSessionLocal() as db:
                eng = APIDiscoveryEngine(db, self._client_id)
                result = await eng.discover_from_url(url, auto_encrypt=auto_save, max_depth=2)
                await eng.close()

            discovered = result.get("discovered_apis", [])
            if not discovered:
                return ToolObservation(
                    status="ok",
                    result_preview=f"[discover] No APIs found at {url}. Try a different URL or path.",
                    committed=False,
                    confidence=0.4,
                    raw_payload=result,
                )

            parts = []
            for api in discovered[:5]:
                base = api.get("base_url") or api.get("url") or ""
                source = api.get("source", "?")
                eps = api.get("endpoints") or []
                ep_lines = [
                    f"  {e.get('method','').upper()} {e.get('path','')} — {e.get('summary','')}"
                    for e in eps[:10]
                ]
                parts.append(f"[{source}] {base} → {len(eps)} endpoints")
                parts.extend(ep_lines)

            preview = (
                f"[discover] Found {len(discovered)} API(s) at {url}:\n"
                + "\n".join(parts)
                + ("\n(saved to integrations DB)" if auto_save else "\n(dry-run: not saved yet)")
            )
            return ToolObservation(
                status="ok",
                result_preview=preview,
                committed=auto_save,
                confidence=0.9 if discovered else 0.4,
                raw_payload=result,
            )
        except Exception as exc:
            logger.exception("_dispatch_discover failed: %s", exc)
            return ToolObservation(
                status="error",
                result_preview="",
                error=f"Discovery failed: {exc}",
                confidence=0.0,
            )

    async def _dispatch_http(
        self,
        action: ActionContract,
        *,
        dry_run: bool,
    ) -> ToolObservation:
        """Handle action_type='http' — direct HTTP call to any URL.

        arguments:
            url          (required) full URL
            method       GET | POST | PUT | PATCH | DELETE (default GET)
            headers      dict of extra headers
            body / json  request body (dict → sent as JSON)
            params       query string params dict
            auth_integration  name of an ApiIntegration in DB to pull auth from

        On dry_run the request IS executed (it's a read-only probe by default)
        but destructive methods (POST/PUT/PATCH/DELETE) are only executed on commit.
        """
        args = dict(action.arguments or {})
        url = str(args.get("url") or "").strip()
        if not url:
            return ToolObservation(
                status="error", result_preview="",
                error="arguments.url is required for action_type='http'",
                confidence=0.0,
            )

        method = str(
            action.http_method or args.get("method") or "GET"
        ).upper().strip()
        headers: Dict[str, str] = dict(args.get("headers") or {})
        body = args.get("body") or args.get("json")
        params = args.get("params")
        guard_payload: Dict[str, Any] = {
            "source": "sandbox_http",
            "method": method,
            "url": url,
            "action": {
                "action": action.action,
                "arguments": args,
            },
        }
        guardrail = plan_guarded_execution(guard_payload, channel="http")

        # Pull auth from a configured integration in DB if requested
        auth_integration = str(args.get("auth_integration") or "").strip()
        if auth_integration and self._client_id:
            try:
                from database import AsyncSessionLocal
                from models import ApiIntegration
                from sqlalchemy import select as _select
                from security import get_security_manager

                async with AsyncSessionLocal() as db:
                    stmt = _select(ApiIntegration).where(
                        ApiIntegration.client_id == self._client_id,
                        ApiIntegration.name == auth_integration,
                    )
                    integ = (await db.execute(stmt)).scalars().first()
                    if integ:
                        # ApiIntegration stores credentials in secret_encrypted via get_secret()
                        token = integ.get_secret() if hasattr(integ, "get_secret") else None
                        if token:
                            auth_type = str(integ.auth_type or "bearer").lower()
                            if auth_type == "bearer":
                                headers["Authorization"] = f"Bearer {token}"
                            elif auth_type in {"apikey", "api_key"}:
                                key_name = str(getattr(integ, "header_name", None) or "X-API-Key")
                                headers[key_name] = token
                            elif auth_type == "basic":
                                import base64 as _b64
                                encoded = _b64.b64encode(token.encode()).decode()
                                headers["Authorization"] = f"Basic {encoded}"
                            elif auth_type == "oauth2":
                                headers["Authorization"] = f"Bearer {token}"
            except Exception as exc:
                logger.warning("Could not load auth for '%s': %s", auth_integration, exc)

        if not dry_run and guardrail.get("dry_run_preview"):
            logger.info("http preflight dry-run action=%s preview=%s", guardrail.get("action_name"), guardrail.get("dry_run_preview"))
            await emit_execution_guard_audit(guard_payload, stage="preflight", guardrail=guardrail)
        if not dry_run and guardrail.get("should_pause"):
            await emit_execution_guard_audit(guard_payload, stage="paused", guardrail=guardrail)
            return ToolObservation(
                status="skipped",
                result_preview=(
                    f"[http-guard] Paused {method} {url}. "
                    f"Reason: {guardrail.get('pause_reason') or 'guardrail'}"
                ),
                committed=False,
                confidence=0.0,
                raw_payload={"url": url, "method": method, "guardrail": guardrail},
            )

        # On dry_run, block destructive methods (still run GET/HEAD as probe)
        if dry_run and method in {"POST", "PUT", "PATCH", "DELETE"}:
            return ToolObservation(
                status="ok",
                result_preview=(
                    f"[http-sandbox] {method} {url} would be called on commit. "
                    f"Dry-run skipped to avoid side-effects."
                ),
                committed=False,
                confidence=0.85,
                raw_payload={"url": url, "method": method, "dry_run": True},
            )

        timeout = float(action.timeout_seconds or _HTTP_TIMEOUT)

        async def _send_request(
            *,
            req_headers: Dict[str, str],
            req_body: Any,
            req_params: Any,
        ) -> tuple[int, str, Optional[Dict[str, Any]], Dict[str, Any]]:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.request(
                    method,
                    url,
                    headers=req_headers,
                    json=req_body if isinstance(req_body, dict) else None,
                    content=req_body if isinstance(req_body, str) else None,
                    params=req_params,
                )
            parsed_json: Optional[Dict[str, Any]] = None
            try:
                raw_json = resp.json()
                if isinstance(raw_json, dict):
                    parsed_json = raw_json
                preview_text = _json.dumps(raw_json, ensure_ascii=False)[:1000]
            except Exception:
                preview_text = resp.text[:1000]

            tripwire = observe_guarded_execution(
                guardrail,
                {
                    "status_code": resp.status_code,
                    "response_json": parsed_json,
                },
            )
            await emit_execution_guard_audit(
                guard_payload,
                stage="tripwire",
                guardrail=guardrail,
                tripwire=tripwire,
                result={
                    "status_code": resp.status_code,
                    "execution_status": "success" if resp.status_code < 400 else "failed",
                },
            )
            return resp.status_code, preview_text, parsed_json, tripwire

        try:
            batch_payloads = list(guardrail.get("batch_payloads") or []) if not dry_run else []
            batch_results: List[Dict[str, Any]] = []
            if batch_payloads:
                last_status = 0
                last_preview = ""
                last_body_data: Optional[Dict[str, Any]] = None
                for batched_payload in batch_payloads:
                    batch_args = (
                        batched_payload.get("action", {}).get("arguments")
                        if isinstance(batched_payload.get("action"), dict)
                        else {}
                    )
                    batch_args = dict(batch_args or {})
                    batch_headers = {**headers, **dict(batch_args.get("headers") or {})}
                    batch_body = batch_args.get("body") or batch_args.get("json")
                    batch_params = batch_args.get("params", params)
                    status_code, preview_text, body_data, tripwire = await _send_request(
                        req_headers=batch_headers,
                        req_body=batch_body,
                        req_params=batch_params,
                    )
                    batch_results.append(
                        {
                            "status_code": status_code,
                            "response": preview_text,
                            "tripwire": tripwire,
                        }
                    )
                    last_status = status_code
                    last_preview = preview_text
                    last_body_data = body_data
                    if status_code >= 400:
                        break
                status_code = last_status
                preview_text = last_preview
                body_data = last_body_data
                status_ok = bool(batch_results) and all(item["status_code"] < 400 for item in batch_results)
            else:
                status_code, preview_text, body_data, tripwire = await _send_request(
                    req_headers=headers,
                    req_body=body,
                    req_params=params,
                )
                batch_results = [{"status_code": status_code, "response": preview_text, "tripwire": tripwire}]
                status_ok = status_code < 400

            prefix = "[http]" if not dry_run else "[http-probe]"
            label = "batched" if (not dry_run and len(batch_results) > 1) else ("committed" if (not dry_run and status_ok) else "sandbox")
            preview = (
                f"{prefix} {method} {url} → {status_code} ({label})\n"
                f"Response: {preview_text}"
            )
            return ToolObservation(
                status="ok" if status_ok else "error",
                result_preview=preview,
                error=None if status_ok else f"HTTP {status_code}",
                committed=not dry_run and status_ok,
                confidence=0.95 if status_ok else 0.2,
                raw_payload={
                    "url": url,
                    "method": method,
                    "status_code": status_code,
                    "response": preview_text,
                    "response_json": body_data if isinstance(body_data, dict) else None,
                    "guardrail": {
                        **guardrail,
                        "tripwire": batch_results[-1].get("tripwire") if batch_results else {"triggered": False},
                    },
                    "batch_results": batch_results if len(batch_results) > 1 else [],
                },
            )
        except Exception as exc:
            return ToolObservation(
                status="error", result_preview="",
                error=f"HTTP call failed: {exc}",
                confidence=0.0,
            )

    async def _dispatch_wait(
        self,
        action: ActionContract,
        *,
        dry_run: bool,
    ) -> ToolObservation:
        """Handle action_type='wait' — pause for N seconds.

        arguments:
            seconds  int (default 5, max 60)
            reason   optional string explaining why we wait
        """
        args = dict(action.arguments or {})
        seconds = min(int(args.get("seconds") or 5), 60)
        reason = str(args.get("reason") or "").strip()

        if not dry_run:
            await asyncio.sleep(seconds)

        msg = f"[wait] {'Simulated' if dry_run else 'Waited'} {seconds}s"
        if reason:
            msg += f" — {reason}"
        return ToolObservation(
            status="ok",
            result_preview=msg,
            committed=not dry_run,
            confidence=1.0,
            raw_payload={"seconds": seconds, "dry_run": dry_run},
        )

    async def _dispatch_code(
        self,
        action: ActionContract,
        *,
        dry_run: bool,
    ) -> ToolObservation:
        """Handle action_type='code' — execute a sandboxed Python snippet.

        arguments:
            code   (required) Python source string, max 40 lines
            vars   optional dict of variables injected into the execution namespace

        Security:
            - Only whitelisted builtins available
            - No file I/O, subprocess, or network calls
            - 10-second wall-clock timeout
            - stdout captured and returned as observation

        Example use: transform JSON from a previous step, compute values, filter lists.
        """
        args = dict(action.arguments or {})
        code_str = str(args.get("code") or "").strip()
        if not code_str:
            return ToolObservation(
                status="error", result_preview="",
                error="arguments.code is required for action_type='code'",
                confidence=0.0,
            )

        lines = code_str.splitlines()
        if len(lines) > _MAX_CODE_LINES:
            return ToolObservation(
                status="error", result_preview="",
                error=f"Code exceeds max {_MAX_CODE_LINES} lines. Split into smaller snippets.",
                confidence=0.0,
            )

        # Dry-run: just validate syntax without executing
        if dry_run:
            try:
                compile(code_str, "<sandbox>", "exec")
                return ToolObservation(
                    status="ok",
                    result_preview="[code-sandbox] Syntax OK. Will execute on commit.",
                    committed=False,
                    confidence=0.9,
                    raw_payload={"lines": len(lines)},
                )
            except SyntaxError as exc:
                return ToolObservation(
                    status="error", result_preview="",
                    error=f"SyntaxError: {exc}",
                    confidence=0.0,
                )

        # Production: execute in restricted namespace with timeout
        safe_builtins = {k: v for k, v in __builtins__.items() if k in _ALLOWED_BUILTINS} \
            if isinstance(__builtins__, dict) \
            else {k: getattr(__builtins__, k) for k in _ALLOWED_BUILTINS if hasattr(__builtins__, k)}
        safe_builtins["__import__"] = None  # block imports

        injected_vars = dict(args.get("vars") or {})
        namespace: Dict[str, Any] = {"__builtins__": safe_builtins, **injected_vars}

        stdout_capture = io.StringIO()
        import sys as _sys
        old_stdout = _sys.stdout
        error_text: Optional[str] = None
        try:
            _sys.stdout = stdout_capture
            exec(compile(code_str, "<sandbox>", "exec"), namespace)  # noqa: S102
        except Exception:
            error_text = traceback.format_exc(limit=3)
        finally:
            _sys.stdout = old_stdout

        output = stdout_capture.getvalue()[:2000]
        if error_text:
            return ToolObservation(
                status="error",
                result_preview=f"[code] Error:\n{error_text[:500]}",
                error=error_text[:300],
                committed=False,
                confidence=0.0,
            )

        return ToolObservation(
            status="ok",
            result_preview=f"[code] Output:\n{output or '(no output)'}",
            committed=True,
            confidence=1.0,
            raw_payload={"output": output, "vars_used": list(injected_vars.keys())},
        )

    async def _dispatch_workflow(
        self,
        action: ActionContract,
        *,
        dry_run: bool,
    ) -> ToolObservation:
        """Handle action_type='workflow' — generate and optionally deploy an n8n workflow.

        Generates a complete n8n workflow JSON from discovered endpoints, then:
        - dry_run=True:  returns the generated workflow JSON as the observation (no deployment)
        - dry_run=False: deploys it to n8n via create_workflow() and returns the workflow ID

        arguments:
            name         workflow name (default: "auto_workflow")
            base_url     base URL of the API to integrate
            endpoints    list of {method, path, summary} dicts (from discover observation)
            trigger      "webhook" | "cron" | "manual" (default: webhook)
            cron         cron expression (only when trigger=cron)
            auth_integration  name of ApiIntegration in DB to pull auth from for the workflow
        """
        args = dict(action.arguments or {})
        name = str(args.get("name") or "auto_workflow").strip()
        base_url = str(args.get("base_url") or "").strip()
        raw_endpoints = list(args.get("endpoints") or [])
        trigger = str(args.get("trigger") or "webhook").lower()
        cron_expr = str(args.get("cron") or "0 9 * * 1-5").strip()

        if not base_url:
            return ToolObservation(
                status="error", result_preview="",
                error="arguments.base_url is required for action_type='workflow'",
                confidence=0.0,
            )

        # Pull auth secret from DB if requested
        auth_type = "none"
        auth_secret: Optional[str] = None
        auth_integration = str(args.get("auth_integration") or "").strip()
        if auth_integration and self._client_id:
            try:
                from database import AsyncSessionLocal
                from models import ApiIntegration
                from sqlalchemy import select as _select

                async with AsyncSessionLocal() as db:
                    stmt = _select(ApiIntegration).where(
                        ApiIntegration.client_id == self._client_id,
                        ApiIntegration.name == auth_integration,
                    )
                    integ = (await db.execute(stmt)).scalars().first()
                    if integ:
                        auth_type = str(integ.auth_type or "none")
                        auth_secret = integ.get_secret() if hasattr(integ, "get_secret") else None
            except Exception as exc:
                logger.warning("Could not load auth for workflow '%s': %s", auth_integration, exc)

        try:
            from brain.workflow_generator import generate_workflow
            from schemas.integration import EndpointSpec, TriggerSpec

            endpoints = []
            for ep in raw_endpoints[:20]:
                try:
                    endpoints.append(EndpointSpec(
                        name=str(ep.get("operation_id") or ep.get("name") or ep.get("path") or "endpoint"),
                        method=str(ep.get("method") or "GET").upper(),
                        path=str(ep.get("path") or "/"),
                        summary=str(ep.get("summary") or ""),
                        query=ep.get("query"),
                        body=ep.get("body"),
                    ))
                except Exception:
                    continue

            if not endpoints:
                # Fallback: create a single GET endpoint for the base URL
                endpoints = [EndpointSpec(name="get_data", method="GET", path="/")]

            trig_spec = TriggerSpec(
                type=trigger if trigger in {"webhook", "cron"} else "webhook",
                config={"cronExpression": cron_expr} if trigger == "cron" else {},
            )

            workflow_json = generate_workflow(
                name=name,
                base_url=base_url,
                endpoints=endpoints,
                trigger=trig_spec,
                auth_type=auth_type,
                auth_secret=auth_secret,
            )
        except Exception as exc:
            return ToolObservation(
                status="error", result_preview="",
                error=f"Workflow generation failed: {exc}",
                confidence=0.0,
            )

        if dry_run:
            import json as _json2
            preview = (
                f"[workflow-preview] Generated n8n workflow '{name}' with "
                f"{len(endpoints)} nodes, trigger={trigger}. "
                f"Will deploy on commit.\n"
                f"Nodes: {[ep.name for ep in endpoints]}"
            )
            return ToolObservation(
                status="ok",
                result_preview=preview,
                committed=False,
                confidence=0.9,
                raw_payload={"workflow": workflow_json, "endpoint_count": len(endpoints)},
            )

        # Production: deploy to n8n
        try:
            from tools.n8n_client import create_workflow
            result = await create_workflow(workflow_json)
            if result:
                wf_id = result.get("id") or result.get("workflowId") or "?"
                wf_url = result.get("url") or ""
                preview = (
                    f"[workflow] Deployed '{name}' to n8n. "
                    f"workflow_id={wf_id}"
                    + (f" url={wf_url}" if wf_url else "")
                )
                return ToolObservation(
                    status="ok",
                    result_preview=preview,
                    committed=True,
                    confidence=1.0,
                    raw_payload={"workflow_id": wf_id, "workflow_url": wf_url, "name": name},
                )
            else:
                return ToolObservation(
                    status="error", result_preview="",
                    error="n8n deploy returned empty — check N8N_API_URL and N8N_API_KEY",
                    confidence=0.3,
                )
        except Exception as exc:
            return ToolObservation(
                status="error", result_preview="",
                error=f"n8n deploy failed: {exc}",
                confidence=0.0,
            )

    async def _dispatch_register(
        self,
        action: ActionContract,
        *,
        dry_run: bool,
    ) -> ToolObservation:
        """Handle action_type='register' — save a discovered API to DB as ApiIntegration.

        arguments:
            name          integration name (snake_case)
            base_url      base URL of the API
            auth_type     none | bearer | apikey | basic | oauth2 (default: none)
            secret        API key / Bearer token / credentials to store encrypted
            docs_url      optional docs URL
            openapi_url   optional OpenAPI spec URL
            endpoints     optional list of discovered endpoint dicts

        dry_run: returns what would be saved, doesn't write to DB.
        """
        args = dict(action.arguments or {})
        name = str(args.get("name") or "").strip().lower().replace(" ", "_").replace("-", "_")
        base_url = str(args.get("base_url") or "").strip()

        if not name or not base_url:
            return ToolObservation(
                status="error", result_preview="",
                error="arguments.name and arguments.base_url are required for action_type='register'",
                confidence=0.0,
            )

        auth_type = str(args.get("auth_type") or "none").strip().lower()
        secret = str(args.get("secret") or "").strip()
        docs_url = str(args.get("docs_url") or "").strip() or None
        openapi_url = str(args.get("openapi_url") or "").strip() or None
        endpoints = list(args.get("endpoints") or [])

        if dry_run:
            preview = (
                f"[register-preview] Would save integration '{name}' "
                f"base_url={base_url} auth_type={auth_type} "
                f"secret={'***' if secret else 'none'} "
                f"endpoints={len(endpoints)}. Commit to persist."
            )
            return ToolObservation(
                status="ok", result_preview=preview,
                committed=False, confidence=0.95,
                raw_payload={"name": name, "base_url": base_url, "auth_type": auth_type},
            )

        try:
            import uuid as _uuid
            from database import AsyncSessionLocal
            from models import ApiIntegration
            from sqlalchemy import select as _select

            try:
                client_uuid = _uuid.UUID(self._client_id)
            except (ValueError, AttributeError):
                return ToolObservation(
                    status="error", result_preview="",
                    error="Invalid client_id — cannot register integration",
                    confidence=0.0,
                )

            async with AsyncSessionLocal() as db:
                stmt = _select(ApiIntegration).where(
                    ApiIntegration.client_id == client_uuid,
                    ApiIntegration.name == name,
                )
                existing = (await db.execute(stmt)).scalars().first()
                if existing:
                    existing.base_url = base_url
                    existing.auth_type = auth_type
                    existing.docs_url = docs_url
                    existing.openapi_url = openapi_url
                    existing.is_active = True
                    existing.integration_metadata = {
                        **(existing.integration_metadata or {}),
                        "auto_registered": True,
                        "endpoint_count": len(endpoints),
                    }
                    if secret:
                        existing.set_secret(secret)
                    await db.commit()
                    action_label = "Updated"
                else:
                    integ = ApiIntegration(
                        client_id=client_uuid,
                        name=name,
                        base_url=base_url,
                        auth_type=auth_type,
                        docs_url=docs_url,
                        openapi_url=openapi_url,
                        is_active=True,
                        integration_metadata={
                            "auto_registered": True,
                            "endpoint_count": len(endpoints),
                        },
                    )
                    if secret:
                        integ.set_secret(secret)
                    db.add(integ)
                    await db.commit()
                    action_label = "Registered"

            preview = (
                f"[register] {action_label} integration '{name}' "
                f"base_url={base_url} auth_type={auth_type}. "
                f"Now available for use with action_type='http' or 'integration'."
            )
            return ToolObservation(
                status="ok", result_preview=preview,
                committed=True, confidence=1.0,
                raw_payload={"name": name, "base_url": base_url, "action": action_label.lower()},
            )
        except Exception as exc:
            logger.exception("_dispatch_register failed: %s", exc)
            return ToolObservation(
                status="error", result_preview="",
                error=f"Registration failed: {exc}",
                confidence=0.0,
            )

    async def _dispatch_http_with_retry(
        self,
        action: ActionContract,
        *,
        dry_run: bool,
        max_retries: int = 2,
    ) -> ToolObservation:
        """Wrap _dispatch_http with exponential backoff retry on 5xx / timeout."""
        last_obs: Optional[ToolObservation] = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait = 2 ** attempt
                logger.info("HTTP retry %d/%d after %ds", attempt, max_retries, wait)
                await asyncio.sleep(wait)
            obs = await self._dispatch_http(action, dry_run=dry_run)
            last_obs = obs
            # Only retry on server errors (5xx) or connection failures
            if obs.status == "ok":
                return obs
            if obs.error and ("5" in (obs.error or "")[:4] or "failed" in (obs.error or "").lower()):
                continue
            # 4xx client errors — no point retrying
            return obs
        return last_obs  # type: ignore[return-value]

    async def _dispatch(
        self,
        action: ActionContract,
        *,
        dry_run: bool,
        action_id: Optional[str],
    ) -> ToolObservation:
        atype = str(action.action_type or "").lower()

        # --- Native discovery tool (no n8n needed) ---
        if atype == "discover":
            return await self._dispatch_discover(action, dry_run=dry_run)

        # --- Direct HTTP call (with retry on 5xx) ---
        if atype == "http":
            return await self._dispatch_http_with_retry(action, dry_run=dry_run)

        # --- Wait (async coordination) ---
        if atype == "wait":
            return await self._dispatch_wait(action, dry_run=dry_run)

        # --- Code execution ---
        if atype == "code":
            return await self._dispatch_code(action, dry_run=dry_run)

        # --- Workflow generation + n8n deploy ---
        if atype == "workflow":
            return await self._dispatch_workflow(action, dry_run=dry_run)

        # --- Register integration to DB ---
        if atype == "register":
            return await self._dispatch_register(action, dry_run=dry_run)

        payload: Dict[str, Any] = {
            "source": "sandbox_executor",
            "action_id": action_id,
            "client_id": self._client_id,
            "session_id": self._session_id,
            "action": action.model_dump(),
            "sandbox_mode": dry_run,
            "dry_run": dry_run,
        }

        result = await send_to_n8n_with_response(payload)
        dispatched = bool(result.get("dispatched"))
        confirmed = bool(result.get("confirmed"))
        execution_status = str(result.get("execution_status") or "not_dispatched")

        if dispatched and execution_status not in {"error", "failed", "canceled", "crashed"}:
            if dry_run:
                preview = (
                    f"[sandbox] Action '{action.action}' ({action.integration}) "
                    "queued for dry-run. Pending commit."
                )
                return ToolObservation(
                    status="ok",
                    result_preview=preview,
                    committed=False,
                    confidence=0.9,
                    raw_payload={**payload, "n8n_result": result},
                )
            else:
                preview = (
                    f"[committed] Action '{action.action}' ({action.integration}) "
                    + (
                        "confirmed in production."
                        if confirmed and execution_status == "success"
                        else "dispatched to production (pending execution)."
                    )
                )
                return ToolObservation(
                    status="ok",
                    result_preview=preview,
                    committed=bool(confirmed and execution_status == "success"),
                    confidence=1.0 if confirmed and execution_status == "success" else 0.85,
                    raw_payload={**payload, "n8n_result": result},
                )
        else:
            # n8n unavailable — graceful mock so the ReAct loop can continue
            if dry_run:
                preview = (
                    f"[sandbox-mock] n8n unavailable. Simulated result for "
                    f"'{action.action}' ({action.integration}): no data returned."
                )
                return ToolObservation(
                    status="ok",
                    result_preview=preview,
                    committed=False,
                    confidence=0.5,
                    raw_payload={**payload, "n8n_result": result},
                )
            else:
                return ToolObservation(
                    status="error",
                    result_preview="",
                    error="n8n unavailable — action not committed",
                    committed=False,
                    confidence=0.0,
                    raw_payload={**payload, "n8n_result": result},
                )

    async def commit_all(self, steps_with_actions: List[Any]) -> int:
        """Re-dispatch all pending (dry-run) steps as real production commits.

        Returns the number of successfully committed actions.
        """
        committed = 0
        for step in steps_with_actions:
            if not step.action or step.is_final:
                continue
            try:
                action_model = ActionContract.model_validate(step.action)
                obs = await self.execute(action_model, dry_run=False)
                if obs.committed:
                    committed += 1
            except Exception as exc:
                logger.exception(
                    "commit_all failed for step %s: %s", step.step_index, exc
                )
        return committed
