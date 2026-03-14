import json
import os
from copy import deepcopy
from typing import Any, Dict, List


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)  # type: ignore[arg-type]
        else:
            out[key] = deepcopy(value)
    return out


def _ensure_dict(policy: Dict[str, Any], key: str) -> Dict[str, Any]:
    current = policy.get(key)
    if isinstance(current, dict):
        return current
    section: Dict[str, Any] = {}
    policy[key] = section
    return section


def _load_env_json_list(var_name: str) -> List[str]:
    raw = os.getenv(var_name, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: List[str] = []
    for item in parsed:
        token = str(item or "").strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def _load_env_json_object_keys(var_name: str) -> List[str]:
    raw = os.getenv(var_name, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    out: List[str] = []
    for key in parsed.keys():
        token = str(key or "").strip().lower()
        if token and token not in out:
            out.append(token)
    return sorted(out)


def apply_openclaw_preset(existing_policy: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a "full auto" preset with strict guardrails.

    This is intentionally opinionated and safe:
    - Enables full automation when enterprise policies are enforced.
    - Keeps allowlists required; will not invent domains/programs.
    - Uses circuit breaker defaults to auto-degrade behavior elsewhere.
    """

    policy: Dict[str, Any] = deepcopy(existing_policy if isinstance(existing_policy, dict) else {})

    autonomy = _ensure_dict(policy, "autonomy")
    autonomy["execution_mode"] = "full_auto"
    autonomy["max_auto_risk"] = "high"
    autonomy["allow_execute_campaigns"] = True

    desktop = _ensure_dict(policy, "desktop")
    desktop["auto_approve_enabled"] = True

    browser = _ensure_dict(policy, "browser")
    browser["auto_approve_enabled"] = True

    workflow = _ensure_dict(policy, "workflow")
    workflow["allow_auto_recovery"] = True

    guardrails = _ensure_dict(policy, "guardrails")
    guardrails["require_domain_allowlist_for_browser"] = True
    guardrails["require_open_program_allowlist"] = True
    guardrails["circuit_breaker"] = _deep_merge(
        guardrails.get("circuit_breaker") if isinstance(guardrails.get("circuit_breaker"), dict) else {},
        {
            "enabled": True,
            "error_rate_threshold": 0.45,
            "window_minutes": 15,
            "open_minutes": 10,
        },
    )

    learning = _ensure_dict(policy, "learning")
    learning.setdefault("exploration_epsilon", 0.10)

    # Fill allowlists from env only when missing (so a preset click doesn't wipe custom policy).
    if not isinstance(browser.get("domain_allowlist"), list) or not browser.get("domain_allowlist"):
        env_domains = _load_env_json_list("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON")
        if env_domains:
            browser["domain_allowlist"] = env_domains

    if not isinstance(desktop.get("allow_open_programs"), list) or not desktop.get("allow_open_programs"):
        env_programs = _load_env_json_object_keys("DESKTOP_AGENT_ALLOWLIST_JSON")
        if env_programs:
            desktop["allow_open_programs"] = env_programs

    return policy


def apply_openclaw_allow_all_preset(existing_policy: Dict[str, Any]) -> Dict[str, Any]:
    """Apply an intentionally unsafe preset: allow any domain + any program.

    This is meant for internal demos/dev only. Production should keep allowlists enabled.
    """
    policy = apply_openclaw_preset(existing_policy)

    guardrails = _ensure_dict(policy, "guardrails")
    guardrails["require_domain_allowlist_for_browser"] = False
    guardrails["require_open_program_allowlist"] = False

    # Empty allowlists mean "allow all" when guardrails are disabled.
    browser = _ensure_dict(policy, "browser")
    browser["domain_allowlist"] = []
    policy["browser_domain_allowlist"] = []

    desktop = _ensure_dict(policy, "desktop")
    desktop["allow_open_programs"] = []
    policy["desktop_open_program_allowlist"] = []

    return policy


def build_default_autonomy_profile(profile: str) -> Dict[str, Any]:
    token = str(profile or "safe").strip().lower()
    if token not in {"safe", "balanced", "autonomous"}:
        token = "safe"

    policy: Dict[str, Any] = {
        "autonomy": {
            "execution_mode": (
                "safe" if token == "safe" else ("semi_auto" if token == "balanced" else "full_auto")
            ),
            "max_auto_risk": ("low" if token == "safe" else ("medium" if token == "balanced" else "high")),
            "allow_execute_campaigns": token != "safe",
        },
        "browser": {"auto_approve_enabled": token != "safe", "domain_allowlist": []},
        "desktop": {"auto_approve_enabled": token == "autonomous", "allow_open_programs": []},
        "workflow": {"allow_auto_recovery": True},
        "guardrails": {
            "require_domain_allowlist_for_browser": True,
            "require_open_program_allowlist": True,
            "circuit_breaker": {
                "enabled": True,
                "error_rate_threshold": 0.45,
                "window_minutes": 15,
                "open_minutes": 10,
            },
        },
        "learning": {"exploration_epsilon": 0.08 if token == "safe" else 0.12},
    }
    return apply_openclaw_preset(policy) if token == "autonomous" else policy
