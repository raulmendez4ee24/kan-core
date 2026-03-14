from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class FailureDiagnosis:
    error_code: str
    cause: str
    recoverable: bool
    confidence: float
    recommendations: List[str]


@dataclass
class CausalDiagnosis:
    primary_cause: str
    causal_path: List[str]
    recoverable: bool
    confidence: float
    recommended_action: str


_CAUSAL_GRAPH = {
    "endpoint_changed": ["provider_release", "route_mismatch", "version_drift"],
    "token_expired_or_invalid": ["credential_rotation", "scope_mismatch", "expired_secret"],
    "provider_transient_error": ["provider_outage", "network_instability", "timeout_spike"],
    "invalid_fields": ["schema_change", "payload_mapping_drift", "contract_violation"],
    "rate_limited": ["traffic_spike", "quota_exhausted"],
}


def diagnose_failure(
    *,
    phase: str,
    message: str,
    status_code: Optional[int] = None,
) -> FailureDiagnosis:
    lowered = (message or "").lower()

    if status_code == 404 or "404" in lowered or "not found" in lowered:
        return FailureDiagnosis(
            error_code="endpoint_changed",
            cause="El endpoint ya no existe o cambio de ruta/version.",
            recoverable=True,
            confidence=0.9,
            recommendations=[
                "Probar rutas alternativas de healthcheck.",
                "Usar openapi_url actualizado del proveedor.",
            ],
        )
    if status_code in {401, 403} or "unauthorized" in lowered or "token" in lowered:
        return FailureDiagnosis(
            error_code="token_expired_or_invalid",
            cause="La credencial esta expirada o sin permisos.",
            recoverable=True,
            confidence=0.9,
            recommendations=[
                "Rotar o refrescar API key/token.",
                "Validar scopes/permisos del token.",
            ],
        )
    if status_code == 429 or "rate limit" in lowered or "too many requests" in lowered:
        return FailureDiagnosis(
            error_code="rate_limited",
            cause="El proveedor rechazo por limite de peticiones.",
            recoverable=True,
            confidence=0.88,
            recommendations=[
                "Aplicar backoff exponencial y jitter.",
                "Cambiar proveedor si el limite persiste.",
            ],
        )
    if status_code is not None and 500 <= status_code <= 599:
        return FailureDiagnosis(
            error_code="provider_transient_error",
            cause="Error temporal del proveedor.",
            recoverable=True,
            confidence=0.85,
            recommendations=[
                "Reintentar con backoff.",
                "Aplicar fallback a otro proveedor.",
            ],
        )
    if "validation" in lowered or "invalid field" in lowered or "bad request" in lowered:
        return FailureDiagnosis(
            error_code="invalid_fields",
            cause="Campos incompatibles con el endpoint actual.",
            recoverable=True,
            confidence=0.82,
            recommendations=[
                "Aplicar auto-mapping de payload.",
                "Revisar schema esperado por el proveedor.",
            ],
        )
    return FailureDiagnosis(
        error_code=f"{phase}_error",
        cause="Error no clasificado.",
        recoverable=False,
        confidence=0.5,
        recommendations=["Revisar logs tecnicos y ejecutar fallback de proveedor."],
    )


def suggest_repairs(
    *,
    candidate: Dict[str, Any],
    diagnosis: FailureDiagnosis,
) -> List[Dict[str, Any]]:
    repairs: List[Dict[str, Any]] = []
    if diagnosis.error_code == "endpoint_changed":
        base_health = str(candidate.get("healthcheck_path") or "").strip()
        alternatives = [
            "/health",
            "/status",
            "/v1/health",
            "/v1/status",
        ]
        if base_health and "?" in base_health:
            alternatives.insert(0, base_health.split("?")[0])
        for path in alternatives:
            repairs.append(
                {
                    "type": "healthcheck_path_update",
                    "description": f"Usar healthcheck alternativo: {path}",
                    "patch": {"healthcheck_path": path},
                }
            )
    elif diagnosis.error_code == "invalid_fields":
        repairs.append(
            {
                "type": "payload_mapping",
                "description": "Aplicar auto-mapping semantico de campos.",
                "patch": {},
            }
        )
    elif diagnosis.error_code == "provider_transient_error":
        repairs.append(
            {
                "type": "retry_backoff",
                "description": "Reintentar con backoff por falla transitoria.",
                "patch": {},
            }
        )
    elif diagnosis.error_code == "token_expired_or_invalid":
        repairs.append(
            {
                "type": "refresh_token",
                "description": "Intentar token alterno desde entorno o rotacion.",
                "patch": {},
            }
        )
    return repairs


def apply_repair_patch(candidate: Dict[str, Any], repair: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    updated = dict(candidate)
    patch = repair.get("patch") or {}
    if isinstance(patch, dict):
        updated.update(patch)
    return updated, str(repair.get("description") or "repair_applied")


def diagnose_causal_path(diagnosis: FailureDiagnosis) -> CausalDiagnosis:
    path = list(_CAUSAL_GRAPH.get(diagnosis.error_code, ["unknown"]))
    if diagnosis.error_code == "endpoint_changed":
        action = "Actualizar OpenAPI, reprobar endpoints y versionar base_url."
    elif diagnosis.error_code == "token_expired_or_invalid":
        action = "Rotar credenciales y validar scopes antes de reintentar."
    elif diagnosis.error_code == "provider_transient_error":
        action = "Activar circuit breaker y fallback de proveedor."
    elif diagnosis.error_code == "rate_limited":
        action = "Reducir QPS, aplicar retry con jitter y fallback."
    elif diagnosis.error_code == "invalid_fields":
        action = "Re-mappear payload con auto-mapping y revalidar schema."
    else:
        action = "Escalar a revision manual con logs causales."
    return CausalDiagnosis(
        primary_cause=diagnosis.error_code,
        causal_path=path,
        recoverable=diagnosis.recoverable,
        confidence=diagnosis.confidence,
        recommended_action=action,
    )
