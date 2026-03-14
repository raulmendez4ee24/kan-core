from __future__ import annotations

import os
import re
from pathlib import Path
import sys

_ENV_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_ENV_PLACEHOLDERS = {"__IN_ENV_SECRETS__", "REPLACE_ME", "CHANGE_ME"}


def is_placeholder(value: str) -> bool:
    token = str(value or "").strip()
    if not token:
        return True
    upper = token.upper()
    return token in _ENV_PLACEHOLDERS or "IN_ENV_SECRETS" in upper


def _unwrap_quotes(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _sanitize_env_value(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    value = re.sub(r"\s+#.*$", "", value).strip()
    value = _unwrap_quotes(value)
    while value.endswith("}") and value.count("}") > value.count("{"):
        value = value[:-1].rstrip()
    while value.endswith("]") and value.count("]") > value.count("["):
        value = value[:-1].rstrip()
    return _unwrap_quotes(value)


def _load_env_file_tolerant(path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return loaded
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        matched = _ENV_ASSIGN_RE.match(line)
        if not matched:
            continue
        key = str(matched.group(1) or "").strip()
        value = _sanitize_env_value(matched.group(2) or "")
        if key:
            loaded[key] = value
    return loaded


def _normalize_runtime_aliases() -> None:
    telegram_token = str(os.getenv("TELEGRAM_TOKEN") or "").strip()
    telegram_bot_token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if telegram_bot_token and not telegram_token:
        os.environ["TELEGRAM_TOKEN"] = telegram_bot_token
    if telegram_token and not telegram_bot_token:
        os.environ["TELEGRAM_BOT_TOKEN"] = telegram_token

    jarvis_client_id = str(os.getenv("JARVIS_CLIENT_ID") or "").strip()
    if not jarvis_client_id:
        fallback = (
            str(os.getenv("KAN_CLIENT_ID") or "").strip()
            or str(os.getenv("DESKTOP_AGENT_CLIENT_ID") or "").strip()
        )
        if fallback:
            os.environ["JARVIS_CLIENT_ID"] = fallback


def _validate_required_env(
    *,
    required_all: tuple[str, ...],
    required_any: tuple[tuple[str, ...], ...],
    context: str,
) -> None:
    missing_all = [key for key in required_all if is_placeholder(str(os.getenv(key) or ""))]
    if missing_all:
        joined = ", ".join(missing_all)
        raise RuntimeError(f"[ENV] Missing required vars for {context}: {joined}")

    for group in required_any:
        candidates = tuple(str(x or "").strip() for x in group if str(x or "").strip())
        if not candidates:
            continue
        if any(not is_placeholder(str(os.getenv(candidate) or "")) for candidate in candidates):
            continue
        joined = " | ".join(candidates)
        raise RuntimeError(f"[ENV] Missing required vars for {context}: one of ({joined})")


def load_environment(
    *,
    strict: bool = False,
    required_all: tuple[str, ...] = (),
    required_any: tuple[tuple[str, ...], ...] = (),
    context: str = "runtime",
) -> dict[str, str]:
    if os.getenv("KAN_LOAD_DOTENV", "true").strip().lower() not in {"1", "true", "yes"}:
        return {}
    in_pytest = bool(os.getenv("PYTEST_CURRENT_TEST") or any("pytest" in arg for arg in sys.argv))
    if in_pytest and not strict:
        return {}
    root = Path(__file__).resolve().parents[1]
    merged: dict[str, str] = {}
    for env_name in (".env", ".env.secrets"):
        env_path = root / env_name
        if env_path.exists():
            merged.update(_load_env_file_tolerant(env_path))

    for key, value in merged.items():
        if is_placeholder(value):
            continue
        current = str(os.getenv(key) or "").strip()
        if current and not is_placeholder(current):
            continue
        os.environ[key] = value

    _normalize_runtime_aliases()

    if strict:
        _validate_required_env(
            required_all=required_all,
            required_any=required_any,
            context=context,
        )

    return merged
