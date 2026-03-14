import json
import re
from typing import Any, Dict, List, Optional, Tuple

from schemas.integration import EndpointSpec

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def parse_openapi_document(raw_document: Any) -> Dict[str, Any]:
    if isinstance(raw_document, dict):
        parsed = raw_document
    elif isinstance(raw_document, str):
        payload = raw_document.strip()
        if not payload:
            raise ValueError("OpenAPI payload is empty")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore[import-not-found]
            except Exception as exc:  # pragma: no cover - depends on optional dependency
                raise ValueError(
                    "OpenAPI YAML is not supported without PyYAML. Send JSON or install PyYAML."
                ) from exc
            parsed = yaml.safe_load(payload)  # type: ignore[union-attr]
    else:
        raise ValueError("OpenAPI payload must be JSON object or string")

    if not isinstance(parsed, dict):
        raise ValueError("OpenAPI payload must deserialize to an object")
    if not isinstance(parsed.get("paths"), dict):
        raise ValueError("OpenAPI payload is missing 'paths'")

    has_openapi = isinstance(parsed.get("openapi"), str)
    has_swagger = isinstance(parsed.get("swagger"), str)
    if not has_openapi and not has_swagger:
        raise ValueError("OpenAPI payload must include 'openapi' or 'swagger' version")
    return parsed


def _safe_operation_name(method: str, path: str, operation: Dict[str, Any]) -> str:
    operation_id = operation.get("operationId")
    if isinstance(operation_id, str) and operation_id.strip():
        return operation_id.strip()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")
    return f"{method.lower()}_{slug}"[:80] or f"{method.lower()}_endpoint"


def _extract_query_params(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw_params = operation.get("parameters")
    if not isinstance(raw_params, list):
        return None
    query_params: Dict[str, Any] = {}
    for item in raw_params:
        if not isinstance(item, dict):
            continue
        if item.get("in") != "query":
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        value = item.get("example")
        if value is None:
            value = item.get("default")
        if value is None:
            value = ""
        query_params[name] = value
    return query_params or None


def _extract_body(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # OpenAPI 3.x
    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        content = request_body.get("content")
        if isinstance(content, dict):
            for media in ("application/json", "application/*+json"):
                media_item = content.get(media)
                if not isinstance(media_item, dict):
                    continue
                example = media_item.get("example")
                if isinstance(example, dict):
                    return example
                examples = media_item.get("examples")
                if isinstance(examples, dict):
                    for candidate in examples.values():
                        if not isinstance(candidate, dict):
                            continue
                        value = candidate.get("value")
                        if isinstance(value, dict):
                            return value
        return {}

    # Swagger 2.0
    raw_params = operation.get("parameters")
    if isinstance(raw_params, list):
        for item in raw_params:
            if not isinstance(item, dict):
                continue
            if item.get("in") != "body":
                continue
            example = item.get("example")
            if isinstance(example, dict):
                return example
            schema = item.get("schema")
            if isinstance(schema, dict):
                props = schema.get("properties")
                if isinstance(props, dict):
                    return {k: "" for k in props.keys()}
            return {}

    return None


def _extract_base_url(document: Dict[str, Any]) -> Optional[str]:
    servers = document.get("servers")
    if isinstance(servers, list):
        for item in servers:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                return url.strip()

    host = document.get("host")
    if isinstance(host, str) and host.strip():
        schemes = document.get("schemes")
        scheme = "https"
        if isinstance(schemes, list) and schemes:
            first = schemes[0]
            if isinstance(first, str) and first.strip():
                scheme = first.strip()
        base_path = document.get("basePath")
        path = ""
        if isinstance(base_path, str):
            path = base_path.strip()
        return f"{scheme}://{host.strip()}{path}"
    return None


def extract_endpoints_from_openapi(
    document: Dict[str, Any],
    *,
    max_endpoints: int = 8,
) -> Tuple[Optional[str], List[EndpointSpec]]:
    if max_endpoints <= 0:
        max_endpoints = 1

    paths = document.get("paths")
    if not isinstance(paths, dict):
        return _extract_base_url(document), []

    endpoints: List[EndpointSpec] = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            endpoints.append(
                EndpointSpec(
                    name=_safe_operation_name(method, path, operation),
                    method=method.upper(),
                    path=path,
                    query=_extract_query_params(operation),
                    body=_extract_body(operation),
                )
            )
            if len(endpoints) >= max_endpoints:
                return _extract_base_url(document), endpoints
    return _extract_base_url(document), endpoints
