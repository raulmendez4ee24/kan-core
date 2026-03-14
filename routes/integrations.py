import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.data_mapper import auto_map_fields, normalize_industry
from brain.global_learning import load_learning_bias, refresh_global_learnings
from brain.integration_autopilot import infer_required_functions, rank_provider_candidates
from brain.integration_resilience import (
    apply_repair_patch,
    diagnose_causal_path,
    diagnose_failure,
    suggest_repairs,
)
from brain.openapi_parser import extract_endpoints_from_openapi, parse_openapi_document
from brain.workflow_generator import generate_complex_workflow, generate_workflow
from database import get_session
from models import ApiIntegration, BusinessEvent, IntegrationMemory, IntegrationRunLog
from schemas.integration import (
    DashboardMemoryItem,
    DashboardRunLog,
    DashboardSummary,
    EndpointSpec,
    GenerateComplexWorkflowRequest,
    GenerateComplexWorkflowResponse,
    GenerateWorkflowRequest,
    IntegrationAutomationInput,
    IntegrationAutomationRequest,
    IntegrationAutomationResponse,
    IntegrationAutomationResult,
    IntegrationAutopilotRequest,
    IntegrationAutopilotResponse,
    IntegrationAutopilotResult,
    IntegrationCreate,
    IntegrationDashboardResponse,
    IntegrationFunctionPlan,
    IntegrationRead,
    ProviderCandidate,
    RotateSecretRequest,
    RotateSecretResponse,
)
from security import require_client_token
from tools.n8n_client import build_workflow_url, create_workflow
from tools.secret_store import load_integration_secret, store_integration_secret

router = APIRouter(prefix="/integrations", tags=["integrations"])

_OPENAPI_FETCH_TIMEOUT = 10.0
_ENDPOINT_TEST_TIMEOUT_FALLBACK = 6.0
_DEFAULT_RUN_COST_USD = {
    "crm": 0.002,
    "calendar": 0.001,
    "payments": 0.01,
    "helpdesk": 0.002,
    "shopify": 0.003,
    "inventory": 0.002,
    "meta": 0.003,
    "email": 0.004,
    "messaging": 0.006,
}


def _allow_scaffold_without_keys() -> bool:
    return os.getenv("AUTOPILOT_ALLOW_SCAFFOLD_WITHOUT_KEYS", "true").lower() in {
        "1",
        "true",
        "yes",
    }


def _parse_client_uuid(client_id: str) -> UUID:
    try:
        return UUID(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid client id") from exc


def _normalize_lookup_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _to_integration_read(integ: ApiIntegration) -> IntegrationRead:
    return IntegrationRead(
        id=str(integ.id),
        name=integ.name,
        base_url=integ.base_url,
        auth_type=integ.auth_type,
        header_name=integ.header_name,
        docs_url=integ.docs_url,
        openapi_url=integ.openapi_url,
        openapi_spec=integ.openapi_spec,
        metadata=integ.integration_metadata or {},
        is_active=integ.is_active,
        created_at=integ.created_at,
        updated_at=integ.updated_at,
    )


def _build_auth_headers(
    *,
    auth_type: str,
    header_name: Optional[str],
    secret: Optional[str],
) -> Dict[str, str]:
    if not secret:
        return {}
    normalized_auth = (auth_type or "none").strip().lower()
    if normalized_auth == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if normalized_auth == "apikey":
        return {header_name or "X-API-Key": secret}
    if normalized_auth == "basic":
        return {"Authorization": secret}
    return {}


def _to_provider_candidate(candidate: Dict[str, object]) -> ProviderCandidate:
    return ProviderCandidate(
        provider_id=str(candidate.get("provider_id") or ""),
        provider_name=str(candidate.get("provider_name") or ""),
        integration_name=str(candidate.get("integration_name") or ""),
        base_url=str(candidate.get("base_url") or ""),
        docs_url=str(candidate.get("docs_url") or ""),
        openapi_url=(
            str(candidate.get("openapi_url"))
            if candidate.get("openapi_url") is not None
            else None
        ),
        auth_type=str(candidate.get("auth_type") or "none"),
        header_name=(
            str(candidate.get("header_name"))
            if candidate.get("header_name") is not None
            else None
        ),
        registration_mode=(
            "api"
            if str(candidate.get("registration_mode") or "").strip().lower() == "api"
            else "manual"
        ),
        signup_url=(
            str(candidate.get("signup_url"))
            if candidate.get("signup_url") is not None
            else None
        ),
        score=float(candidate.get("score") or 0.0),
        comparison_notes=[
            str(item)
            for item in (candidate.get("comparison_notes") or [])
            if str(item).strip()
        ],
    )


async def _fetch_openapi_from_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="openapi_url must use http or https")
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_OPENAPI_FETCH_TIMEOUT,
        ) as client:
            response = await client.get(
                parsed.geturl(),
                headers={"User-Agent": "kan-core-openapi-fetcher/1.0"},
            )
        response.raise_for_status()
        return response.text
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to fetch OpenAPI URL: {exc!s}",
        ) from exc


async def _find_client_integration(
    session: AsyncSession,
    *,
    client_uuid: UUID,
    name: str,
) -> Optional[ApiIntegration]:
    stmt = select(ApiIntegration).where(
        and_(ApiIntegration.client_id == client_uuid, ApiIntegration.name == name)
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def _load_active_integrations_map(
    session: AsyncSession,
    *,
    client_uuid: UUID,
) -> Dict[str, str]:
    stmt = (
        select(ApiIntegration)
        .where(
            ApiIntegration.client_id == client_uuid,
            ApiIntegration.is_active.is_(True),
        )
        .order_by(ApiIntegration.name.asc())
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    mapping: Dict[str, str] = {}
    for integ in rows:
        key = _normalize_lookup_key(integ.name)
        if key and key not in mapping:
            mapping[key] = integ.name
    return mapping


async def _upsert_integration(
    session: AsyncSession,
    *,
    client_uuid: UUID,
    client_id: str,
    data: IntegrationCreate | IntegrationAutomationInput,
) -> ApiIntegration:
    existing = await _find_client_integration(session, client_uuid=client_uuid, name=data.name)

    if existing:
        existing.base_url = data.base_url
        existing.auth_type = data.auth_type
        existing.header_name = data.header_name
        existing.docs_url = data.docs_url
        existing.openapi_url = data.openapi_url
        existing.openapi_spec = data.openapi_spec
        existing.integration_metadata = data.metadata or {}
        if data.secret is not None:
            await store_integration_secret(
                existing,
                client_id=client_id,
                integration_name=data.name,
                secret=data.secret,
            )
        await session.commit()
        await session.refresh(existing)
        return existing

    integ = ApiIntegration(
        client_id=client_uuid,
        name=data.name,
        base_url=data.base_url,
        auth_type=data.auth_type,
        header_name=data.header_name,
        docs_url=data.docs_url,
        openapi_url=data.openapi_url,
        openapi_spec=data.openapi_spec,
        integration_metadata=data.metadata or {},
        is_active=True,
    )
    if data.secret is not None:
        await store_integration_secret(
            integ,
            client_id=client_id,
            integration_name=data.name,
            secret=data.secret,
        )

    session.add(integ)
    await session.commit()
    await session.refresh(integ)
    return integ


async def _resolve_endpoints_from_request(
    *,
    request: GenerateWorkflowRequest | IntegrationAutomationInput,
    integration: ApiIntegration,
) -> Tuple[str, Sequence[EndpointSpec], str]:
    if request.endpoints:
        return integration.base_url, request.endpoints, "manual_endpoints"

    metadata = integration.integration_metadata or {}
    openapi_payload: Any = request.openapi
    openapi_url = request.openapi_url

    if openapi_payload is None:
        openapi_payload = request.openapi_spec or integration.openapi_spec or metadata.get("openapi")
    if not openapi_url:
        meta_openapi_url = integration.openapi_url or metadata.get("openapi_url")
        if isinstance(meta_openapi_url, str) and meta_openapi_url.strip():
            openapi_url = meta_openapi_url.strip()

    source = "openapi"
    if openapi_payload is None and openapi_url:
        openapi_payload = await _fetch_openapi_from_url(openapi_url)
        source = "openapi_url"

    if openapi_payload is None:
        # Keep strict behavior for explicit generate/preview endpoints:
        # fallback scaffolding is only for automation/autopilot flows.
        if isinstance(request, IntegrationAutomationInput) and _allow_scaffold_without_keys():
            fallback_path = str(metadata.get("healthcheck_path") or "").strip()
            if not fallback_path:
                fallback_path = "/"
            return (
                integration.base_url,
                [EndpointSpec(name="Auto Endpoint", method="GET", path=fallback_path)],
                "fallback_endpoint",
            )
        raise HTTPException(
            status_code=400,
            detail="Provide endpoints or openapi/openapi_url (or configure them in integration metadata).",
        )

    try:
        document = parse_openapi_document(openapi_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid OpenAPI: {exc!s}") from exc

    parsed_base_url, endpoints = extract_endpoints_from_openapi(
        document,
        max_endpoints=request.max_endpoints,
    )
    if not endpoints:
        raise HTTPException(status_code=400, detail="OpenAPI has no usable HTTP endpoints")

    resolved_base_url = integration.base_url.strip() if integration.base_url else ""
    if not resolved_base_url:
        resolved_base_url = parsed_base_url.strip() if parsed_base_url else ""
    if not resolved_base_url:
        raise HTTPException(
            status_code=400,
            detail="Base URL missing. Set integration.base_url or define servers in OpenAPI.",
        )
    return resolved_base_url, endpoints, source


async def _build_workflow_payload(
    *,
    name: str,
    client_id: str,
    integration: ApiIntegration,
    base_url: str,
    endpoints: Sequence[EndpointSpec],
    trigger: Any,
) -> dict:
    workflow_name = f"{name} (client {client_id[:8]})"
    auth_secret = await load_integration_secret(integration, client_id=client_id)
    return generate_workflow(
        name=workflow_name,
        base_url=base_url,
        endpoints=list(endpoints),
        trigger=trigger,
        auth_type=integration.auth_type,
        auth_header_name=integration.header_name,
        auth_secret=auth_secret,
    )


def _resolve_secret_for_candidate(
    *,
    candidate: Dict[str, object],
    supplied_api_keys: Dict[str, str],
) -> tuple[Optional[str], str]:
    normalized_supplied = {
        _normalize_lookup_key(k): v
        for k, v in supplied_api_keys.items()
        if _normalize_lookup_key(k) and isinstance(v, str) and v.strip()
    }
    provider_id = _normalize_lookup_key(str(candidate.get("provider_id") or ""))
    integration_name = _normalize_lookup_key(str(candidate.get("integration_name") or ""))

    for key in (provider_id, integration_name):
        if key and key in normalized_supplied:
            return normalized_supplied[key].strip(), "supplied"

    env_candidates: list[str] = []
    env_key_name = candidate.get("env_key_name")
    if isinstance(env_key_name, str) and env_key_name.strip():
        env_candidates.append(env_key_name.strip())
    if provider_id:
        env_candidates.append(f"AUTO_API_KEY_{provider_id.upper()}")
        env_candidates.append(f"{provider_id.upper()}_API_KEY")
    if integration_name:
        env_candidates.append(f"{integration_name.upper()}_API_KEY")

    for env_name in env_candidates:
        value = os.getenv(env_name)
        if isinstance(value, str) and value.strip():
            return value.strip(), f"env:{env_name}"
    return None, ""


async def _try_auto_registration(
    *,
    function_name: str,
    candidate: Dict[str, object],
    registration_profile: Dict[str, Any],
) -> tuple[Optional[str], str, list[str], Dict[str, Any]]:
    registration_mode = str(candidate.get("registration_mode") or "manual").strip().lower()
    registration_url = candidate.get("registration_url")
    diagnostics: Dict[str, Any] = {"phase": "registration"}
    mapped_profile, mapped_trace = auto_map_fields(registration_profile or {})
    diagnostics["mapped_fields"] = mapped_trace

    if registration_mode != "api" or not isinstance(registration_url, str) or not registration_url.strip():
        signup_url = str(candidate.get("signup_url") or candidate.get("docs_url") or "")
        action = (
            f"Crear cuenta y API key en: {signup_url}"
            if signup_url
            else "Crear cuenta y generar API key manualmente."
        )
        return None, "manual_required", [action], diagnostics

    method = str(candidate.get("registration_method") or "POST").strip().upper()
    if method not in {"POST", "PUT", "PATCH"}:
        method = "POST"

    payload = dict(mapped_profile)
    payload.setdefault("requested_function", function_name)
    payload.setdefault("provider_id", str(candidate.get("provider_id") or ""))

    try:
        async with httpx.AsyncClient(timeout=_ENDPOINT_TEST_TIMEOUT_FALLBACK) as client:
            response = await client.request(
                method,
                registration_url.strip(),
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        diagnostics["status_code"] = response.status_code
        response.raise_for_status()
        data = response.json() if response.content else {}
    except Exception as exc:
        diagnostics["error"] = str(exc)
        return None, "failed", [f"Fallo registro automatico: {exc!s}"], diagnostics

    if not isinstance(data, dict):
        return None, "failed", ["Registro automatico sin respuesta JSON valida."], diagnostics

    key_field = str(candidate.get("registration_key_field") or "api_key")
    secret = data.get(key_field) or data.get("api_key") or data.get("token") or data.get("access_token")
    if isinstance(secret, str) and secret.strip():
        return secret.strip(), "auto_registered", [], diagnostics
    return None, "failed", ["Registro automatico completo, pero no devolvio API key."], diagnostics


async def _test_provider_endpoint(
    *,
    candidate: Dict[str, object],
    secret: Optional[str],
    timeout_seconds: float,
) -> tuple[str, str, Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {"phase": "endpoint_test"}
    health_path = candidate.get("healthcheck_path")
    if not isinstance(health_path, str) or not health_path.strip():
        return "not_run", "No healthcheck configurado para este proveedor.", diagnostics

    base_url = str(candidate.get("base_url") or "").strip()
    if not base_url:
        return "failed", "Base URL vacia.", diagnostics
    if "{shop}" in base_url:
        return "not_run", "Base URL requiere variable {shop}; prueba automatica omitida.", diagnostics

    auth_type = str(candidate.get("auth_type") or "none")
    header_name = candidate.get("header_name")
    header_value = str(header_name) if isinstance(header_name, str) else None
    if auth_type.strip().lower() != "none" and not secret:
        return "not_run", "Sin API key para probar endpoint.", diagnostics

    url = f"{base_url.rstrip('/')}/{health_path.lstrip('/')}"
    headers = _build_auth_headers(auth_type=auth_type, header_name=header_value, secret=secret)
    diagnostics["url"] = url
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, headers=headers)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        diagnostics["duration_ms"] = elapsed_ms
        diagnostics["status_code"] = response.status_code
    except Exception as exc:
        diagnostics["error"] = str(exc)
        return "failed", f"Error de red en prueba de endpoint: {exc!s}", diagnostics

    if response.status_code < 400:
        return "passed", f"Healthcheck OK ({response.status_code}).", diagnostics
    if response.status_code in {401, 403}:
        return "failed", f"Credenciales invalidas ({response.status_code}).", diagnostics
    return "failed", f"Healthcheck fallo ({response.status_code}).", diagnostics


def _normalize_endpoint_test_result(
    payload: tuple,
) -> tuple[str, str, Dict[str, Any]]:
    if len(payload) == 3 and isinstance(payload[2], dict):
        return payload[0], payload[1], payload[2]
    if len(payload) >= 2:
        return payload[0], payload[1], {}
    return "failed", "Resultado de prueba invalido.", {}


def _build_autopilot_input(
    *,
    function_name: str,
    candidate: Dict[str, object],
    secret: Optional[str],
    trigger: Any,
    memory_note: Optional[str] = None,
) -> IntegrationAutomationInput:
    metadata = {
        "provider_id": str(candidate.get("provider_id") or ""),
        "provider_name": str(candidate.get("provider_name") or ""),
        "score": float(candidate.get("score") or 0.0),
        "comparison_notes": list(candidate.get("comparison_notes") or []),
        "selected_by": "autopilot",
        "healthcheck_path": str(candidate.get("healthcheck_path") or ""),
    }
    if memory_note:
        metadata["memory_note"] = memory_note
    return IntegrationAutomationInput(
        name=function_name,
        base_url=str(candidate.get("base_url") or ""),
        auth_type=str(candidate.get("auth_type") or "none"),
        header_name=(
            str(candidate.get("header_name"))
            if candidate.get("header_name") is not None
            else None
        ),
        secret=secret,
        docs_url=(
            str(candidate.get("docs_url"))
            if candidate.get("docs_url") is not None
            else None
        ),
        openapi_url=(
            str(candidate.get("openapi_url"))
            if candidate.get("openapi_url") is not None
            else None
        ),
        metadata=metadata,
        trigger=trigger,
    )


def _selection_explanation(
    *,
    function_name: str,
    chosen: Dict[str, object],
    candidates: Sequence[Dict[str, object]],
    memory_note: Optional[str],
) -> str:
    top = sorted(candidates, key=lambda item: -float(item.get("score") or 0.0))[:4]
    names = ", ".join(str(item.get("provider_name") or item.get("provider_id") or "") for item in top)
    chosen_name = str(chosen.get("provider_name") or chosen.get("provider_id") or "")
    score = float(chosen.get("score") or 0.0)
    reason = (
        f"Encontré {len(top)} opciones para {function_name}: {names}. "
        f"Elegí {chosen_name} por score {score:.2f} y mejor compatibilidad."
    )
    if memory_note:
        reason = f"{reason} {memory_note}"
    return reason


def _estimate_run_cost(function_name: str, attempts: int) -> float:
    base = _DEFAULT_RUN_COST_USD.get(function_name, 0.002)
    return round(base * max(attempts, 1), 6)


def _endpoint_priority_score(endpoint: EndpointSpec) -> tuple:
    method = str(endpoint.method or "GET").strip().upper()
    method_weight = {
        "POST": 5,
        "PUT": 4,
        "PATCH": 4,
        "DELETE": 3,
        "GET": 2,
    }.get(method, 1)
    path = str(endpoint.path or "/")
    depth = len([x for x in path.split("/") if x.strip()])
    has_body = 1 if isinstance(endpoint.body, dict) and endpoint.body else 0
    has_query = 1 if isinstance(endpoint.query, dict) and endpoint.query else 0
    # Higher score first; shorter names as tie-breaker.
    return (method_weight, has_body, has_query, depth, -len(str(endpoint.name or "")))


def _rank_endpoints(
    endpoints: Sequence[EndpointSpec],
    *,
    strategy: str,
) -> List[EndpointSpec]:
    token = str(strategy or "priority").strip().lower()
    if token == "as_is":
        return list(endpoints)
    if token == "alphabetical":
        return sorted(list(endpoints), key=lambda ep: str(ep.name or "").lower())
    return sorted(list(endpoints), key=_endpoint_priority_score, reverse=True)


def _chunk_endpoints(
    endpoints: Sequence[EndpointSpec],
    *,
    max_per_workflow: int,
) -> List[List[EndpointSpec]]:
    chunk = max(1, int(max_per_workflow))
    rows = list(endpoints)
    return [rows[i : i + chunk] for i in range(0, len(rows), chunk)]


async def _create_workflows_with_sharding(
    *,
    integration_name: str,
    client_id: str,
    integration: ApiIntegration,
    base_url: str,
    endpoints: Sequence[EndpointSpec],
    trigger: Any,
    shard_enabled: bool,
    max_nodes_per_workflow: int,
    endpoint_selection_strategy: str,
) -> List[Dict[str, Any]]:
    ranked = _rank_endpoints(endpoints, strategy=endpoint_selection_strategy)
    max_endpoints_per_workflow = max(1, int(max_nodes_per_workflow) - 1)
    chunks = (
        _chunk_endpoints(ranked, max_per_workflow=max_endpoints_per_workflow)
        if shard_enabled
        else [ranked]
    )
    if not chunks:
        chunks = [[]]

    created_items: List[Dict[str, Any]] = []
    total = len(chunks)
    for idx, shard in enumerate(chunks, start=1):
        suffix = f" [shard {idx}/{total}]" if total > 1 else ""
        workflow = await _build_workflow_payload(
            name=f"{integration_name}{suffix}",
            client_id=client_id,
            integration=integration,
            base_url=base_url,
            endpoints=shard,
            trigger=trigger,
        )
        created = await create_workflow(workflow)
        if not created:
            raise RuntimeError("Failed to create workflow in n8n")
        workflow_id = created.get("id")
        created_items.append(
            {
                "index": idx,
                "workflowId": workflow_id,
                "url": build_workflow_url(workflow_id),
                "name": str(created.get("name") or f"{integration_name}{suffix}"),
                "endpoint_count": len(shard),
                "max_endpoints_per_workflow": int(max_endpoints_per_workflow),
            }
        )
    return created_items


async def _load_memory_bias(
    session: AsyncSession,
    *,
    client_uuid: UUID,
    function_name: str,
    industry: str,
) -> Dict[str, Dict[str, Any]]:
    try:
        stmt = select(IntegrationMemory).where(
            IntegrationMemory.client_id == client_uuid,
            IntegrationMemory.function_name == function_name,
            IntegrationMemory.industry == industry,
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    except Exception:
        return {}

    bias: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        total = max(0, int((row.success_count or 0) + (row.failure_count or 0)))
        success_rate = (row.success_count / total) if total else 0.0
        # Keep historical bias conservative until enough samples accumulate.
        confidence = min(total, 20) / 20.0
        score_delta = (success_rate - 0.5) * 6.0 * confidence
        bias[_normalize_lookup_key(row.provider_id)] = {
            "score_delta": score_delta,
            "note": f"historical_success_rate={success_rate:.2f}; samples={total}",
        }
    return bias


async def _update_integration_memory(
    session: AsyncSession,
    *,
    client_uuid: UUID,
    function_name: str,
    provider_id: str,
    industry: str,
    success: bool,
    duration_ms: Optional[int],
    cost_usd: Optional[float],
    status: str,
    error_code: Optional[str],
) -> None:
    provider_key = _normalize_lookup_key(provider_id)
    try:
        stmt = select(IntegrationMemory).where(
            IntegrationMemory.client_id == client_uuid,
            IntegrationMemory.function_name == function_name,
            IntegrationMemory.provider_id == provider_key,
            IntegrationMemory.industry == industry,
        )
        result = await session.execute(stmt)
        item = result.scalars().first()
    except Exception:
        return

    now = datetime.now(timezone.utc)
    if not item:
        item = IntegrationMemory(
            client_id=client_uuid,
            function_name=function_name,
            provider_id=provider_key,
            industry=industry,
            success_count=0,
            failure_count=0,
            avg_duration_ms=None,
            avg_cost_usd=None,
            last_status=status,
            last_error_code=error_code,
            memory_metadata={},
            last_used_at=now,
        )
        session.add(item)

    if success:
        item.success_count = int(item.success_count or 0) + 1
    else:
        item.failure_count = int(item.failure_count or 0) + 1
    total_runs = item.success_count + item.failure_count

    if duration_ms is not None:
        prev = item.avg_duration_ms or 0.0
        item.avg_duration_ms = ((prev * (total_runs - 1)) + float(duration_ms)) / max(total_runs, 1)
    if cost_usd is not None:
        prev_cost = item.avg_cost_usd or 0.0
        item.avg_cost_usd = ((prev_cost * (total_runs - 1)) + float(cost_usd)) / max(total_runs, 1)

    item.last_status = status
    item.last_error_code = error_code
    item.last_used_at = now
    await session.commit()


async def _record_run_log(
    session: AsyncSession,
    *,
    client_uuid: UUID,
    function_name: str,
    provider_id: Optional[str],
    integration_name: Optional[str],
    industry: str,
    status: str,
    attempt_number: int,
    auto_repair_used: bool,
    fallback_used: bool,
    duration_ms: Optional[int],
    cost_usd: Optional[float],
    error_code: Optional[str],
    error_message: Optional[str],
    diagnostics: Dict[str, Any],
) -> None:
    row = IntegrationRunLog(
        client_id=client_uuid,
        function_name=function_name,
        provider_id=provider_id,
        integration_name=integration_name,
        industry=industry,
        status=status,
        attempt_number=attempt_number,
        auto_repair_used=auto_repair_used,
        fallback_used=fallback_used,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        error_code=error_code,
        error_message=error_message,
        diagnostics=diagnostics,
    )
    session.add(row)
    event_type = "integration_run_success" if status in {"integrated", "saved_without_workflow", "already_covered"} else "integration_run_failed"
    event = BusinessEvent(
        organization_id=client_uuid,
        event_type=event_type,
        source="integrations.autopilot",
        channel="api",
        entity_id=function_name,
        payload={
            "function_name": function_name,
            "provider_id": provider_id,
            "integration_name": integration_name,
            "status": status,
            "attempt_number": attempt_number,
            "auto_repair_used": auto_repair_used,
            "fallback_used": fallback_used,
            "duration_ms": duration_ms,
            "cost_usd": cost_usd,
            "error_code": error_code,
            "error_message": error_message,
            "diagnostics": diagnostics,
        },
    )
    session.add(event)
    await session.commit()


@router.post("", response_model=IntegrationRead)
async def create_or_update_integration(
    data: IntegrationCreate,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    integ = await _upsert_integration(
        session,
        client_uuid=client_uuid,
        client_id=client_id,
        data=data,
    )
    return _to_integration_read(integ)


@router.post("/{name}/rotate-secret", response_model=RotateSecretResponse)
async def rotate_integration_secret(
    name: str,
    req: RotateSecretRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    integ = await _find_client_integration(session, client_uuid=client_uuid, name=name)
    if not integ or not integ.is_active:
        raise HTTPException(status_code=404, detail="Integration not found or inactive")

    secret = req.secret
    if not secret and req.use_env_var:
        env_name = req.use_env_var.strip()
        secret = os.getenv(env_name)
    if not secret or not secret.strip():
        raise HTTPException(status_code=400, detail="Provide secret or use_env_var with value")

    backend = await store_integration_secret(
        integ,
        client_id=client_id,
        integration_name=integ.name,
        secret=secret.strip(),
    )
    await session.commit()
    return RotateSecretResponse(
        integration_name=integ.name,
        backend=backend,  # type: ignore[arg-type]
        rotated=True,
        detail="Secret rotated successfully.",
    )


@router.post("/auto-generate", response_model=IntegrationAutomationResponse)
async def auto_generate_workflows(
    req: IntegrationAutomationRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    results: list[IntegrationAutomationResult] = []

    for item in req.integrations:
        try:
            integ = await _upsert_integration(
                session,
                client_uuid=client_uuid,
                client_id=client_id,
                data=item,
            )
            base_url, endpoints, source = await _resolve_endpoints_from_request(
                request=item,
                integration=integ,
            )
            created_items = await _create_workflows_with_sharding(
                integration_name=item.name,
                client_id=client_id,
                integration=integ,
                base_url=base_url,
                endpoints=endpoints,
                trigger=item.trigger,
                shard_enabled=bool(item.shard_enabled),
                max_nodes_per_workflow=int(item.max_nodes_per_workflow),
                endpoint_selection_strategy=str(item.endpoint_selection_strategy),
            )
            primary = created_items[0]
            workflow_id = primary.get("workflowId")
            workflow_url = str(primary.get("url") or "") or None
            integ.last_workflow_id = str(workflow_id) if workflow_id is not None else None
            integ.last_workflow_url = workflow_url
            integ.integration_metadata = {
                **(integ.integration_metadata or {}),
                "workflow_shards": created_items,
                "workflow_shard_count": len(created_items),
            }
            await session.commit()
            results.append(
                IntegrationAutomationResult(
                    name=item.name,
                    status="created",
                    endpoint_count=len(endpoints),
                    source=source,
                    workflowId=workflow_id,
                    url=workflow_url,
                    shard_count=len(created_items),
                    shard_workflows=created_items,
                )
            )
        except Exception as exc:
            error_msg = exc.detail if isinstance(exc, HTTPException) else str(exc)
            results.append(
                IntegrationAutomationResult(
                    name=item.name,
                    status="failed",
                    error=str(error_msg),
                )
            )
            if not req.continue_on_error:
                break

    return IntegrationAutomationResponse(results=results)


@router.post("/autopilot", response_model=IntegrationAutopilotResponse)
async def run_integration_autopilot(
    req: IntegrationAutopilotRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    industry = normalize_industry(req.industry or req.registration_profile.get("industry"))
    existing_integrations = await _load_active_integrations_map(session, client_uuid=client_uuid)

    detected_functions = infer_required_functions(req.request_text, req.desired_functions)
    if not detected_functions:
        raise HTTPException(
            status_code=400,
            detail="No se detectaron funciones requeridas. Envia desired_functions explicitamente.",
        )

    plans: list[IntegrationFunctionPlan] = []
    results: list[IntegrationAutopilotResult] = []

    for function_name in detected_functions:
        function_key = _normalize_lookup_key(function_name)
        existing_name = existing_integrations.get(function_key)
        if existing_name:
            workflow_id = None
            workflow_url = None
            status = "already_covered"
            reason = f"La funcion ya esta cubierta por la integracion '{existing_name}'."

            existing_integration = await _find_client_integration(
                session,
                client_uuid=client_uuid,
                name=existing_name,
            )
            if req.auto_generate_workflow and existing_integration:
                existing_secret = await load_integration_secret(
                    existing_integration,
                    client_id=client_id,
                )
                normalized_auth = str(existing_integration.auth_type or "none").strip().lower()
                auth_required = normalized_auth != "none"
                can_generate = bool(existing_secret) or (not auth_required) or _allow_scaffold_without_keys()
                if can_generate:
                    try:
                        request_seed = IntegrationAutomationInput(
                            name=existing_integration.name,
                            base_url=existing_integration.base_url,
                            auth_type=existing_integration.auth_type,
                            header_name=existing_integration.header_name,
                            secret=None,
                            docs_url=existing_integration.docs_url,
                            openapi_url=existing_integration.openapi_url,
                            openapi_spec=existing_integration.openapi_spec,
                            metadata=existing_integration.integration_metadata or {},
                            endpoints=[],
                            trigger=req.trigger,
                        )
                        base_url, endpoints, _source = await _resolve_endpoints_from_request(
                            request=request_seed,
                            integration=existing_integration,
                        )
                        workflow = await _build_workflow_payload(
                            name=function_key,
                            client_id=client_id,
                            integration=existing_integration,
                            base_url=base_url,
                            endpoints=endpoints,
                            trigger=req.trigger,
                        )
                        created = await create_workflow(workflow)
                        if created:
                            workflow_id = created.get("id")
                            workflow_url = build_workflow_url(workflow_id)
                            existing_integration.last_workflow_id = (
                                str(workflow_id) if workflow_id is not None else None
                            )
                            existing_integration.last_workflow_url = workflow_url
                            await session.commit()
                            status = "integrated"
                            reason = (
                                f"La funcion ya estaba cubierta por '{existing_name}' y se creo workflow en n8n."
                            )
                    except Exception as exc:
                        reason = (
                            f"La funcion ya estaba cubierta por '{existing_name}', "
                            f"pero fallo crear workflow: {exc!s}"
                        )

            plans.append(
                IntegrationFunctionPlan(
                    function_name=function_key,
                    needs_new_function=False,
                    existing_integration=existing_name,
                    candidates=[],
                )
            )
            results.append(
                IntegrationAutopilotResult(
                    function_name=function_key,
                    integration_name=existing_name,
                    status=status,
                    reason=reason,
                    workflowId=workflow_id,
                    url=workflow_url,
                    diagnostics={"industry": industry},
                )
            )
            continue

        ranked_candidates = rank_provider_candidates(
            function_name=function_key,
            request_text=req.request_text,
            allowlist=req.provider_allowlist,
            blocklist=req.provider_blocklist,
            limit=req.max_candidates_per_function,
        )

        memory_bias = await _load_memory_bias(
            session,
            client_uuid=client_uuid,
            function_name=function_key,
            industry=industry,
        )
        global_bias: Dict[str, float] = {}
        if os.getenv("ENABLE_GLOBAL_LEARNING", "false").lower() in {"1", "true", "yes"}:
            try:
                global_bias = await load_learning_bias(
                    session,
                    industry=industry,
                    function_name=function_key,
                )
            except Exception:
                global_bias = {}
        for candidate in ranked_candidates:
            provider_key = _normalize_lookup_key(str(candidate.get("provider_id") or ""))
            memory_info = memory_bias.get(provider_key)
            if memory_info:
                candidate["score"] = float(candidate.get("score") or 0.0) + float(
                    memory_info.get("score_delta") or 0.0
                )
                notes = list(candidate.get("comparison_notes") or [])
                notes.append(str(memory_info.get("note") or "memory_bias"))
                candidate["comparison_notes"] = notes
            if provider_key in global_bias:
                candidate["score"] = float(candidate.get("score") or 0.0) + float(global_bias[provider_key])
                notes = list(candidate.get("comparison_notes") or [])
                notes.append(f"global_learning_delta={global_bias[provider_key]:.2f}")
                candidate["comparison_notes"] = notes
        ranked_candidates.sort(key=lambda item: -float(item.get("score") or 0.0))

        plans.append(
            IntegrationFunctionPlan(
                function_name=function_key,
                needs_new_function=True,
                candidates=[_to_provider_candidate(item) for item in ranked_candidates],
            )
        )

        if not ranked_candidates:
            results.append(
                IntegrationAutopilotResult(
                    function_name=function_key,
                    status="manual_action_required",
                    registration_status="manual_required",
                    key_status="missing",
                    reason="No se encontraron APIs candidatas en el catalogo actual.",
                    next_actions=[
                        "Agregar un proveedor en el catalogo de autopilot o usar provider_allowlist.",
                    ],
                    diagnostics={"industry": industry},
                )
            )
            if not req.continue_on_error:
                break
            continue

        if req.dry_run:
            selected = ranked_candidates[0]
            results.append(
                IntegrationAutopilotResult(
                    function_name=function_key,
                    provider_id=str(selected.get("provider_id") or ""),
                    integration_name=str(selected.get("integration_name") or function_key),
                    status="planned",
                    key_status="not_tested",
                    reason="Plan generado en dry_run; no se ejecutaron cambios.",
                    attempted_providers=[
                        str(item.get("provider_id") or "")
                        for item in ranked_candidates
                    ],
                    selection_explanation=_selection_explanation(
                        function_name=function_key,
                        chosen=selected,
                        candidates=ranked_candidates,
                        memory_note=None,
                    ),
                    diagnostics={"industry": industry},
                )
            )
            continue

        attempted_providers: list[str] = []
        auto_repair_log: list[str] = []
        selected_result: Optional[IntegrationAutopilotResult] = None
        selected_error_code: Optional[str] = None
        selected_duration: Optional[int] = None
        total_attempts = 0

        for provider_index, base_candidate in enumerate(ranked_candidates):
            candidate = dict(base_candidate)
            provider_id = str(candidate.get("provider_id") or "")
            attempted_providers.append(provider_id)

            auth_type = str(candidate.get("auth_type") or "none")
            auth_required = auth_type.strip().lower() != "none"
            registration_status = "not_needed"
            key_status = "not_needed"
            next_actions: list[str] = []

            secret, secret_source = _resolve_secret_for_candidate(
                candidate=candidate,
                supplied_api_keys=req.supplied_api_keys,
            )
            registration_diagnostics: Dict[str, Any] = {}

            if auth_required:
                if secret:
                    key_status = "obtained"
                else:
                    key_status = "missing"
                    if req.allow_auto_registration:
                        auto_secret, auto_status, registration_actions, reg_diag = await _try_auto_registration(
                            function_name=function_key,
                            candidate=candidate,
                            registration_profile=req.registration_profile,
                        )
                        registration_status = auto_status
                        registration_diagnostics = reg_diag
                        next_actions.extend(registration_actions)
                        if auto_secret:
                            secret = auto_secret
                            key_status = "obtained"
                    else:
                        registration_status = "manual_required"
                        signup_url = str(candidate.get("signup_url") or candidate.get("docs_url") or "")
                        if signup_url:
                            next_actions.append(f"Generar API key manualmente en: {signup_url}")
                        else:
                            next_actions.append("Generar API key manualmente y reenviar la solicitud.")

            endpoint_status = "not_run"
            endpoint_msg = "No endpoint test."
            endpoint_diag: Dict[str, Any] = {}
            candidate_attempt_success = False

            for attempt in range(1, req.auto_repair_max_attempts + 1):
                total_attempts += 1
                raw_result = await _test_provider_endpoint(
                    candidate=candidate,
                    secret=secret,
                    timeout_seconds=req.test_timeout_seconds,
                )
                endpoint_status, endpoint_msg, endpoint_diag = _normalize_endpoint_test_result(raw_result)
                selected_duration = endpoint_diag.get("duration_ms")
                if endpoint_status in {"passed", "not_run"}:
                    candidate_attempt_success = True
                    break

                diagnosis = diagnose_failure(
                    phase="endpoint_test",
                    message=endpoint_msg,
                    status_code=endpoint_diag.get("status_code"),
                )
                causal = diagnose_causal_path(diagnosis)
                selected_error_code = diagnosis.error_code
                auto_repair_log.append(
                    f"{provider_id}: intento {attempt} -> {diagnosis.error_code} ({diagnosis.cause})"
                )
                auto_repair_log.append(
                    f"{provider_id}: causal_path={' > '.join(causal.causal_path)} action={causal.recommended_action}"
                )
                if attempt >= req.auto_repair_max_attempts or not diagnosis.recoverable:
                    break

                repairs = suggest_repairs(candidate=candidate, diagnosis=diagnosis)
                if not repairs:
                    break
                repaired = False
                for repair in repairs:
                    if repair.get("type") == "refresh_token":
                        new_secret, new_source = _resolve_secret_for_candidate(
                            candidate=candidate,
                            supplied_api_keys=req.supplied_api_keys,
                        )
                        if new_secret and new_secret != secret:
                            secret = new_secret
                            secret_source = new_source
                            auto_repair_log.append(f"{provider_id}: token refresh from {new_source}")
                            repaired = True
                            break
                        continue
                    candidate, repair_desc = apply_repair_patch(candidate, repair)
                    auto_repair_log.append(f"{provider_id}: repair aplicado -> {repair_desc}")
                    repaired = True
                    break
                if not repaired:
                    break

            if auth_required and not secret:
                endpoint_status = "not_run"
                if not next_actions:
                    next_actions.append("Falta API key para continuar.")
                if req.enable_provider_fallback and provider_index < len(ranked_candidates) - 1:
                    continue

            if not candidate_attempt_success and endpoint_status == "failed":
                if req.enable_provider_fallback and provider_index < len(ranked_candidates) - 1:
                    continue

            memory_note = None
            provider_key = _normalize_lookup_key(provider_id)
            if provider_key in memory_bias:
                memory_note = str(memory_bias[provider_key].get("note") or "")

            input_data = _build_autopilot_input(
                function_name=function_key,
                candidate=candidate,
                secret=secret,
                trigger=req.trigger,
                memory_note=memory_note,
            )
            integ = await _upsert_integration(
                session,
                client_uuid=client_uuid,
                client_id=client_id,
                data=input_data,
            )

            workflow_id = None
            workflow_url = None
            shard_workflows: list[dict[str, Any]] = []
            status = "saved_without_workflow"
            reason = "Integracion guardada."
            integration_name = str(candidate.get("integration_name") or function_key)
            can_generate_workflow = req.auto_generate_workflow and (
                not auth_required or bool(secret) or _allow_scaffold_without_keys()
            )

            if req.auto_generate_workflow and not can_generate_workflow:
                status = "manual_action_required"
                reason = "Integracion guardada, pero falta API key para generar workflow funcional."
            elif can_generate_workflow:
                try:
                    base_url, endpoints, _source = await _resolve_endpoints_from_request(
                        request=input_data,
                        integration=integ,
                    )
                    created_items = await _create_workflows_with_sharding(
                        integration_name=function_key,
                        client_id=client_id,
                        integration=integ,
                        base_url=base_url,
                        endpoints=endpoints,
                        trigger=req.trigger,
                        shard_enabled=bool(input_data.shard_enabled),
                        max_nodes_per_workflow=int(input_data.max_nodes_per_workflow),
                        endpoint_selection_strategy=str(input_data.endpoint_selection_strategy),
                    )
                    primary = created_items[0]
                    workflow_id = primary.get("workflowId")
                    workflow_url = str(primary.get("url") or "") or None
                    shard_workflows = created_items
                    integ.last_workflow_id = str(workflow_id) if workflow_id is not None else None
                    integ.last_workflow_url = workflow_url
                    integ.integration_metadata = {
                        **(integ.integration_metadata or {}),
                        "workflow_shards": shard_workflows,
                        "workflow_shard_count": len(shard_workflows),
                    }
                    await session.commit()
                    status = "integrated"
                    reason = "Integracion guardada y workflow creado en n8n."
                except Exception as exc:
                    status = "saved_without_workflow"
                    reason = "Integracion guardada, pero fallo la generacion automatica del workflow."
                    next_actions.append(f"Error de workflow: {exc!s}")

            if auth_required and not secret:
                status = "manual_action_required"
                reason = "Integracion guardada, pero falta credencial API."

            selected_result = IntegrationAutopilotResult(
                function_name=function_key,
                provider_id=provider_id,
                integration_name=integration_name,
                status=status,  # type: ignore[arg-type]
                registration_status=registration_status,  # type: ignore[arg-type]
                key_status=key_status,  # type: ignore[arg-type]
                endpoint_test_status=endpoint_status,  # type: ignore[arg-type]
                attempted_providers=attempted_providers,
                auto_repair_log=auto_repair_log,
                selection_explanation=_selection_explanation(
                    function_name=function_key,
                    chosen=candidate,
                    candidates=ranked_candidates,
                    memory_note=memory_note,
                ),
                diagnostics={
                    "industry": industry,
                    "secret_source": secret_source,
                    "registration": registration_diagnostics,
                    "endpoint_test": endpoint_diag,
                },
                workflowId=workflow_id,
                url=workflow_url,
                shard_count=max(1, len(shard_workflows)),
                shard_workflows=shard_workflows,
                saved_integration_id=str(integ.id),
                reason=reason,
                next_actions=next_actions,
            )
            break

        if not selected_result:
            selected_result = IntegrationAutopilotResult(
                function_name=function_key,
                status="failed",
                attempted_providers=attempted_providers,
                auto_repair_log=auto_repair_log,
                reason="No fue posible completar la integracion con los proveedores intentados.",
                next_actions=["Revisar logs y agregar credenciales/proveedor alternativo."],
                diagnostics={"industry": industry},
            )

        results.append(selected_result)

        success_statuses = {"integrated", "saved_without_workflow", "already_covered"}
        is_success = selected_result.status in success_statuses
        estimated_cost = _estimate_run_cost(function_key, total_attempts)
        await _record_run_log(
            session,
            client_uuid=client_uuid,
            function_name=function_key,
            provider_id=selected_result.provider_id,
            integration_name=selected_result.integration_name,
            industry=industry,
            status=selected_result.status,
            attempt_number=max(total_attempts, 1),
            auto_repair_used=bool(selected_result.auto_repair_log),
            fallback_used=len(selected_result.attempted_providers) > 1,
            duration_ms=selected_duration,
            cost_usd=estimated_cost,
            error_code=selected_error_code,
            error_message=(
                None if is_success else selected_result.reason
            ),
            diagnostics=selected_result.diagnostics,
        )
        if selected_result.provider_id:
                await _update_integration_memory(
                    session,
                    client_uuid=client_uuid,
                    function_name=function_key,
                    provider_id=selected_result.provider_id,
                    industry=industry,
                success=is_success,
                duration_ms=selected_duration,
                cost_usd=estimated_cost,
                status=selected_result.status,
                error_code=selected_error_code,
            )

        if selected_result.status in {"failed", "manual_action_required"} and not req.continue_on_error:
            break

    if os.getenv("ENABLE_GLOBAL_LEARNING", "false").lower() in {"1", "true", "yes"}:
        try:
            await refresh_global_learnings(session, min_samples=3)
        except Exception:
            # Learning refresh is best-effort and should not break the response path.
            pass

    return IntegrationAutopilotResponse(
        detected_functions=detected_functions,
        plans=plans,
        results=results,
    )


@router.get("/dashboard", response_model=IntegrationDashboardResponse)
async def integrations_dashboard(
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)

    active_stmt = select(ApiIntegration).where(
        ApiIntegration.client_id == client_uuid,
        ApiIntegration.is_active.is_(True),
    )
    active_result = await session.execute(active_stmt)
    active_integrations = len(list(active_result.scalars().all()))

    logs_stmt = (
        select(IntegrationRunLog)
        .where(IntegrationRunLog.client_id == client_uuid)
        .order_by(desc(IntegrationRunLog.created_at))
        .limit(100)
    )
    logs_result = await session.execute(logs_stmt)
    logs = list(logs_result.scalars().all())

    total_runs = len(logs)
    success_runs = sum(1 for row in logs if row.status in {"integrated", "saved_without_workflow"})
    failed_runs = sum(1 for row in logs if row.status in {"failed", "manual_action_required"})
    success_rate = round((success_runs / total_runs) * 100.0, 2) if total_runs else 0.0
    avg_duration_ms = round(
        sum((row.duration_ms or 0) for row in logs) / total_runs,
        2,
    ) if total_runs else 0.0
    total_cost_usd = round(sum(float(row.cost_usd or 0.0) for row in logs), 6)

    memory_stmt = (
        select(IntegrationMemory)
        .where(IntegrationMemory.client_id == client_uuid)
        .order_by(desc(IntegrationMemory.updated_at))
        .limit(50)
    )
    memory_result = await session.execute(memory_stmt)
    memories = list(memory_result.scalars().all())

    return IntegrationDashboardResponse(
        summary=DashboardSummary(
            active_integrations=active_integrations,
            total_runs=total_runs,
            success_runs=success_runs,
            failed_runs=failed_runs,
            success_rate=success_rate,
            avg_duration_ms=avg_duration_ms,
            total_cost_usd=total_cost_usd,
        ),
        runs=[
            DashboardRunLog(
                function_name=row.function_name,
                provider_id=row.provider_id,
                integration_name=row.integration_name,
                status=row.status,
                attempt_number=row.attempt_number,
                auto_repair_used=row.auto_repair_used,
                fallback_used=row.fallback_used,
                duration_ms=row.duration_ms,
                cost_usd=row.cost_usd,
                error_code=row.error_code,
                error_message=row.error_message,
                diagnostics=row.diagnostics or {},
                created_at=row.created_at,
            )
            for row in logs
        ],
        memory=[
            DashboardMemoryItem(
                function_name=item.function_name,
                provider_id=item.provider_id,
                industry=item.industry,
                success_count=item.success_count,
                failure_count=item.failure_count,
                avg_duration_ms=item.avg_duration_ms,
                avg_cost_usd=item.avg_cost_usd,
                last_status=item.last_status,
                last_error_code=item.last_error_code,
                metadata=item.memory_metadata or {},
            )
            for item in memories
        ],
    )


@router.post("/generate-complex-workflow", response_model=GenerateComplexWorkflowResponse)
async def generate_complex_business_workflow(
    req: GenerateComplexWorkflowRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    if not req.steps:
        raise HTTPException(status_code=400, detail="Provide at least one step")

    enriched_steps: list[Dict[str, Any]] = []
    for step in req.steps:
        integ = await _find_client_integration(
            session,
            client_uuid=client_uuid,
            name=step.integration_name,
        )
        if not integ or not integ.is_active:
            raise HTTPException(
                status_code=404,
                detail=f"Integration '{step.integration_name}' not found or inactive",
            )
        auth_secret = await load_integration_secret(integ, client_id=client_id)
        enriched_steps.append(
            {
                "name": step.name,
                "base_url": integ.base_url,
                "method": step.method,
                "path": step.path,
                "query": step.query,
                "body": step.body,
                "auth_type": integ.auth_type,
                "auth_header_name": integ.header_name,
                "auth_secret": auth_secret,
            }
        )

    workflow_payload = generate_complex_workflow(
        name=req.name,
        steps=enriched_steps,
        trigger=req.trigger,
    )
    created = await create_workflow(workflow_payload)
    if not created:
        raise HTTPException(status_code=502, detail="Failed to create workflow in n8n")
    workflow_id = created.get("id")
    url = build_workflow_url(workflow_id)
    return GenerateComplexWorkflowResponse(
        workflowId=workflow_id,
        url=url,
        name=str(created.get("name") or req.name),
        step_count=len(req.steps),
    )


@router.post("/{name}/generate-workflow")
async def generate_integration_workflow(
    name: str,
    req: GenerateWorkflowRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    integ = await _find_client_integration(session, client_uuid=client_uuid, name=name)
    if not integ or not integ.is_active:
        raise HTTPException(status_code=404, detail="Integration not found or inactive")

    base_url, endpoints, _source = await _resolve_endpoints_from_request(
        request=req,
        integration=integ,
    )
    created_items = await _create_workflows_with_sharding(
        integration_name=name,
        client_id=client_id,
        integration=integ,
        base_url=base_url,
        endpoints=endpoints,
        trigger=req.trigger,
        shard_enabled=bool(req.shard_enabled),
        max_nodes_per_workflow=int(req.max_nodes_per_workflow),
        endpoint_selection_strategy=str(req.endpoint_selection_strategy),
    )
    primary = created_items[0]
    workflow_id = primary.get("workflowId")
    url = str(primary.get("url") or "") or None
    integ.last_workflow_id = str(workflow_id) if workflow_id is not None else None
    integ.last_workflow_url = url
    integ.integration_metadata = {
        **(integ.integration_metadata or {}),
        "workflow_shards": created_items,
        "workflow_shard_count": len(created_items),
    }
    await session.commit()
    if len(created_items) == 1:
        return {
            "workflowId": workflow_id,
            "url": url,
            "name": primary.get("name"),
        }
    return {
        "workflowId": workflow_id,
        "url": url,
        "name": primary.get("name"),
        "endpointCount": len(endpoints),
        "shardCount": len(created_items),
        "shards": created_items,
    }


@router.post("/{name}/preview-workflow")
async def preview_integration_workflow(
    name: str,
    req: GenerateWorkflowRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    client_uuid = _parse_client_uuid(client_id)
    integ = await _find_client_integration(session, client_uuid=client_uuid, name=name)
    if not integ or not integ.is_active:
        raise HTTPException(status_code=404, detail="Integration not found or inactive")

    base_url, endpoints, source = await _resolve_endpoints_from_request(
        request=req,
        integration=integ,
    )
    auth_secret = await load_integration_secret(integ, client_id=client_id)
    ranked = _rank_endpoints(endpoints, strategy=str(req.endpoint_selection_strategy))
    max_endpoints_per_workflow = max(1, int(req.max_nodes_per_workflow) - 1)
    chunks = (
        _chunk_endpoints(ranked, max_per_workflow=max_endpoints_per_workflow)
        if bool(req.shard_enabled)
        else [ranked]
    )
    preview_workflows: List[Dict[str, Any]] = []
    for idx, shard in enumerate(chunks, start=1):
        suffix = f" [shard {idx}/{len(chunks)}]" if len(chunks) > 1 else ""
        workflow_name = f"{name}{suffix} (preview for {client_id[:8]})"
        workflow = generate_workflow(
            name=workflow_name,
            base_url=base_url,
            endpoints=list(shard),
            trigger=req.trigger,
            auth_type=integ.auth_type,
            auth_header_name=integ.header_name,
            auth_secret=auth_secret,
        )
        preview_workflows.append(
            {
                "index": idx,
                "name": workflow.get("name"),
                "endpointCount": len(shard),
                "workflow": workflow,
            }
        )
    primary_preview = preview_workflows[0]
    return {
        "name": primary_preview.get("name"),
        "source": source,
        "endpointCount": len(endpoints),
        "shardCount": len(preview_workflows),
        "workflows": preview_workflows,
        "workflow": primary_preview.get("workflow"),
    }
