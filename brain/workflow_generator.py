import base64
from typing import Any, Dict, List, Optional

from schemas.integration import EndpointSpec, TriggerSpec


def _webhook_node(name: str, path: str) -> Dict[str, Any]:
    return {
        "parameters": {
            "path": path.strip("/") or "trigger",
            "httpMethod": "POST",
            "responseMode": "onReceived",
            "responseData": {"send": True, "response": 200, "responseBody": "{\"status\":\"ok\"}"},
        },
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 1,
        "position": [240, 300],
        "name": name,
    }


def _cron_node(name: str, cron_expression: str) -> Dict[str, Any]:
    return {
        "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": cron_expression}]}},
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1,
        "position": [240, 300],
        "name": name,
    }


def _build_auth_headers(
    *,
    auth_type: str,
    auth_header_name: Optional[str],
    auth_secret: Optional[str],
) -> Dict[str, str]:
    if not auth_secret:
        return {}
    normalized_auth = (auth_type or "none").strip().lower()
    if normalized_auth == "bearer":
        return {"Authorization": f"Bearer {auth_secret}"}
    if normalized_auth == "apikey":
        return {auth_header_name or "X-API-Key": auth_secret}
    if normalized_auth == "basic":
        if auth_secret.lower().startswith("basic "):
            return {"Authorization": auth_secret}
        encoded = base64.b64encode(auth_secret.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    return {}


def _http_request_node(
    name: str,
    base_url: str,
    ep: EndpointSpec,
    *,
    auth_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{ep.path.lstrip('/')}"
    method = (ep.method or "GET").upper()
    node: Dict[str, Any] = {
        "parameters": {
            "url": url,
            "method": method,
            "options": {},
        },
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4,
        "position": [640, 300],
        "name": name,
    }
    if ep.query:
        node["parameters"]["sendQuery"] = True
        node["parameters"]["queryParametersUi"] = {
            "parameter": [{"name": k, "value": str(v)} for k, v in (ep.query or {}).items()]
        }
    if method != "GET":
        node["parameters"]["sendBody"] = True
        node["parameters"]["jsonBody"] = ep.body or {}
    if auth_headers:
        node["parameters"]["sendHeaders"] = True
        node["parameters"]["headerParametersUi"] = {
            "parameter": [{"name": k, "value": v} for k, v in auth_headers.items()]
        }
    return node


def generate_workflow(
    *,
    name: str,
    base_url: str,
    endpoints: List[EndpointSpec],
    trigger: Optional[TriggerSpec] = None,
    auth_type: str = "none",
    auth_header_name: Optional[str] = None,
    auth_secret: Optional[str] = None,
) -> Dict[str, Any]:
    trigger = trigger or TriggerSpec(type="webhook", config={})
    auth_headers = _build_auth_headers(
        auth_type=auth_type,
        auth_header_name=auth_header_name,
        auth_secret=auth_secret,
    )

    nodes: List[Dict[str, Any]] = []
    connections: Dict[str, Any] = {}

    # Trigger node
    last_name = "Trigger"
    if trigger.type == "webhook":
        webhook_path = trigger.config.get("path", name.lower().replace(" ", "-")[:32])
        tnode = _webhook_node("Webhook", webhook_path)
    else:
        cron_expression = str(trigger.config.get("cron", "0 */2 * * *"))
        tnode = _cron_node("Schedule Trigger", cron_expression)
    nodes.append(tnode)
    last_name = tnode["name"]

    # HTTP Request nodes chained
    x, y = 640, 300
    for i, ep in enumerate(endpoints):
        hnode = _http_request_node(
            ep.name,
            base_url,
            ep,
            auth_headers=auth_headers,
        )
        hnode["position"] = [x + i * 320, y]
        nodes.append(hnode)

        # Connect previous to current
        connections.setdefault(last_name, {"main": [[]]})
        connections[last_name]["main"][0].append({"node": hnode["name"], "type": "main", "index": 0})
        last_name = hnode["name"]

    workflow = {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": {},
    }
    return workflow


def generate_complex_workflow(
    *,
    name: str,
    steps: List[Dict[str, Any]],
    trigger: Optional[TriggerSpec] = None,
) -> Dict[str, Any]:
    trigger = trigger or TriggerSpec(type="webhook", config={})

    nodes: List[Dict[str, Any]] = []
    connections: Dict[str, Any] = {}
    if trigger.type == "webhook":
        webhook_path = trigger.config.get("path", name.lower().replace(" ", "-")[:32])
        tnode = _webhook_node("Webhook", webhook_path)
    else:
        cron_expression = str(trigger.config.get("cron", "0 */2 * * *"))
        tnode = _cron_node("Schedule Trigger", cron_expression)
    nodes.append(tnode)
    last_name = tnode["name"]

    x, y = 640, 300
    for i, step in enumerate(steps):
        endpoint = EndpointSpec(
            name=str(step.get("name") or f"Step_{i+1}"),
            method=str(step.get("method") or "GET"),
            path=str(step.get("path") or "/"),
            query=step.get("query") if isinstance(step.get("query"), dict) else None,
            body=step.get("body") if isinstance(step.get("body"), dict) else None,
        )
        headers = _build_auth_headers(
            auth_type=str(step.get("auth_type") or "none"),
            auth_header_name=(
                str(step.get("auth_header_name"))
                if step.get("auth_header_name") is not None
                else None
            ),
            auth_secret=(
                str(step.get("auth_secret"))
                if step.get("auth_secret") is not None
                else None
            ),
        )
        node = _http_request_node(
            endpoint.name,
            str(step.get("base_url") or ""),
            endpoint,
            auth_headers=headers,
        )
        node["position"] = [x + i * 320, y]
        nodes.append(node)

        connections.setdefault(last_name, {"main": [[]]})
        connections[last_name]["main"][0].append(
            {"node": node["name"], "type": "main", "index": 0}
        )
        last_name = node["name"]

    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": {},
    }
