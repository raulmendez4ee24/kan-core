import json
import os
from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class AgentConfig:
    backend_ws_url: str
    client_id: str
    client_token: str
    agent_name: str
    environment: str
    heartbeat_interval_seconds: float
    reconnect_backoff_min_seconds: float
    reconnect_backoff_max_seconds: float
    command_timeout_ms: int
    screenshot_max_width: int
    open_program_allowlist: Dict[str, str]
    browser_enabled: bool
    browser_headless: bool
    browser_channel: str
    browser_user_data_dir: str
    browser_domain_allowlist: List[str]
    browser_default_timeout_ms: int
    browser_max_attempts: int
    browser_prewarm: bool
    human_recorder_enabled: bool
    unsafe_allow_all: bool



def _parse_allowlist(raw: str) -> Dict[str, str]:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("DESKTOP_AGENT_ALLOWLIST_JSON must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("DESKTOP_AGENT_ALLOWLIST_JSON must be a JSON object")

    normalized: Dict[str, str] = {}
    for key, item in value.items():
        name = str(key).strip().lower()
        path = str(item).strip()
        if not name or not path:
            continue
        normalized[name] = path
    return normalized


def _parse_browser_domains(raw: str) -> List[str]:
    value = raw.strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON must be valid JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON must be a JSON array")
    domains: List[str] = []
    for item in parsed:
        token = str(item).strip().lower()
        if token and token not in domains:
            domains.append(token)
    return domains



def load_config() -> AgentConfig:
    backend_ws_url = os.getenv("DESKTOP_AGENT_WS_URL", "").strip()
    client_id = os.getenv("DESKTOP_AGENT_CLIENT_ID", "").strip()
    client_token = os.getenv("DESKTOP_AGENT_CLIENT_TOKEN", "").strip()
    agent_name = os.getenv("DESKTOP_AGENT_NAME", "desktop-agent").strip()[:160]
    environment = os.getenv("DESKTOP_AGENT_ENVIRONMENT", "production").strip().lower()

    if not backend_ws_url:
        raise ValueError("DESKTOP_AGENT_WS_URL is required")
    if not client_id:
        raise ValueError("DESKTOP_AGENT_CLIENT_ID is required")
    if not client_token:
        raise ValueError("DESKTOP_AGENT_CLIENT_TOKEN is required")
    if not agent_name:
        raise ValueError("DESKTOP_AGENT_NAME cannot be empty")
    if environment not in {"sandbox", "production"}:
        raise ValueError("DESKTOP_AGENT_ENVIRONMENT must be sandbox or production")

    heartbeat_interval = float(os.getenv("DESKTOP_AGENT_HEARTBEAT_SECONDS", "10"))
    reconnect_min = float(os.getenv("DESKTOP_AGENT_RECONNECT_MIN_SECONDS", "1"))
    reconnect_max = float(os.getenv("DESKTOP_AGENT_RECONNECT_MAX_SECONDS", "20"))
    timeout_ms = int(os.getenv("DESKTOP_AGENT_COMMAND_TIMEOUT_MS", "10000"))
    screenshot_max_width = int(os.getenv("DESKTOP_AGENT_SCREENSHOT_MAX_WIDTH", "1600"))
    browser_enabled = os.getenv("DESKTOP_AGENT_BROWSER_ENABLED", "true").lower() in {"1", "true", "yes"}
    browser_headless = os.getenv("DESKTOP_AGENT_BROWSER_HEADLESS", "false").lower() in {"1", "true", "yes"}
    browser_channel = os.getenv("DESKTOP_AGENT_BROWSER_CHANNEL", "chromium").strip().lower()
    browser_user_data_dir = os.getenv("DESKTOP_AGENT_BROWSER_USER_DATA_DIR", "").strip()
    browser_default_timeout_ms = int(os.getenv("DESKTOP_AGENT_BROWSER_DEFAULT_TIMEOUT_MS", "15000"))
    browser_max_attempts = int(os.getenv("DESKTOP_AGENT_BROWSER_MAX_ATTEMPTS", "2"))
    browser_prewarm = os.getenv("DESKTOP_AGENT_BROWSER_PREWARM", "true").lower() in {"1", "true", "yes"}
    human_recorder_enabled = os.getenv("DESKTOP_AGENT_HUMAN_RECORDER_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    unsafe_allow_all = os.getenv("DESKTOP_AGENT_UNSAFE_ALLOW_ALL", "false").lower() in {"1", "true", "yes"}

    if heartbeat_interval <= 0:
        raise ValueError("DESKTOP_AGENT_HEARTBEAT_SECONDS must be > 0")
    if reconnect_min <= 0 or reconnect_max < reconnect_min:
        raise ValueError("Reconnect backoff values are invalid")
    if timeout_ms < 500:
        raise ValueError("DESKTOP_AGENT_COMMAND_TIMEOUT_MS must be >= 500")
    if screenshot_max_width < 320:
        raise ValueError("DESKTOP_AGENT_SCREENSHOT_MAX_WIDTH must be >= 320")
    if browser_default_timeout_ms < 1000:
        raise ValueError("DESKTOP_AGENT_BROWSER_DEFAULT_TIMEOUT_MS must be >= 1000")
    if browser_max_attempts < 1 or browser_max_attempts > 5:
        raise ValueError("DESKTOP_AGENT_BROWSER_MAX_ATTEMPTS must be in [1,5]")
    if browser_channel and browser_channel not in {"chrome", "chromium"}:
        raise ValueError("DESKTOP_AGENT_BROWSER_CHANNEL must be chrome or chromium")

    allowlist = _parse_allowlist(os.getenv("DESKTOP_AGENT_ALLOWLIST_JSON", "{}"))
    browser_domains = _parse_browser_domains(
        os.getenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", "[]")
    )

    return AgentConfig(
        backend_ws_url=backend_ws_url,
        client_id=client_id,
        client_token=client_token,
        agent_name=agent_name,
        environment=environment,
        heartbeat_interval_seconds=heartbeat_interval,
        reconnect_backoff_min_seconds=reconnect_min,
        reconnect_backoff_max_seconds=reconnect_max,
        command_timeout_ms=timeout_ms,
        screenshot_max_width=screenshot_max_width,
        open_program_allowlist=allowlist,
        browser_enabled=browser_enabled,
        browser_headless=browser_headless,
        browser_channel=(browser_channel or "chromium"),
        browser_user_data_dir=browser_user_data_dir,
        browser_domain_allowlist=browser_domains,
        browser_default_timeout_ms=browser_default_timeout_ms,
        browser_max_attempts=browser_max_attempts,
        browser_prewarm=browser_prewarm,
        human_recorder_enabled=human_recorder_enabled,
        unsafe_allow_all=unsafe_allow_all,
    )
