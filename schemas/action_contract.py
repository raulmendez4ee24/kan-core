from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ACTION_CONTRACT_VERSION = "1.0"

# All valid action_type values understood by SandboxExecutor:
#   integration  — dispatch to n8n for a pre-configured integration
#   desktop      — dispatch to desktop agent (clicks, screenshots)
#   discover     — run APIDiscoveryEngine natively (no n8n needed)
#   http         — direct HTTP call to any URL (auth from DB or inline args)
#   wait         — pause N seconds (async coordination, polling)
#   code         — execute a sandboxed Python snippet, capture stdout as observation
ACTION_TYPES = Literal[
    "integration",  # dispatch to n8n for a pre-configured integration
    "desktop",      # dispatch to desktop agent (clicks, screenshots)
    "discover",     # run APIDiscoveryEngine natively (crawl + OpenAPI)
    "http",         # direct HTTP call to any URL (auth from DB or inline)
    "wait",         # pause N seconds (async coordination, polling)
    "code",         # execute sandboxed Python snippet, capture stdout
    "workflow",     # generate + deploy n8n workflow from endpoints
    "register",     # save discovered API as ApiIntegration in DB
]


class ActionContract(BaseModel):
    """Versioned contract between LLM action outputs and executors."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"]
    action_type: ACTION_TYPES = "integration"
    # Relaxed pattern: allows paths, URLs, methods (e.g. "get_/orders", "POST /v1/send")
    action: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z][a-zA-Z0-9_./:@ -]*$")
    integration: str = Field(
        default="none",
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_./:@-][a-zA-Z0-9_./:@ -]*$",
    )
    arguments: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(..., min_length=1, max_length=2000)
    # Optional — used by http action (GET/POST/PUT/PATCH/DELETE)
    http_method: Optional[str] = Field(default=None, max_length=10)
    # Optional — override default timeout per action
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)
