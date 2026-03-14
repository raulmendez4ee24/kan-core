#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"


PROFILE_VALUES = {
    # Core autonomy profile
    "AUTONOMY_DEFAULT_EXECUTION_MODE": "full_auto",
    "AUTONOMY_DEFAULT_MAX_AUTO_RISK": "high",
    "POLICY_AUTO_APPLY_PRESET": "openclaw_allow_all",
    # Feature flags for legal high-autonomy operations
    "ENABLE_AUTONOMOUS_RUNNER": "true",
    "ENABLE_DESKTOP_AUTONOMY_LOOP": "true",
    "ENABLE_DESKTOP_AGENT": "true",
    "ENABLE_BROWSER_AGENT": "true",
    "ENABLE_BUSINESS_CONTEXT_REASONING": "true",
    "ENABLE_ENTERPRISE_POLICIES": "true",
    "ENABLE_EXECUTION_QUEUE": "true",
    "ENABLE_TASK_STUDIO": "true",
    "ENABLE_REINFORCEMENT_OPTIMIZER": "true",
    "ENABLE_ENTERPRISE_CONSOLE": "true",
    # Keep legal/safety controls active
    "DESKTOP_AGENT_UNSAFE_ALLOW_ALL": "false",
    "DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON": "[]",
    "AUTONOMY_ENABLE_SELF_HEALING_DEFAULT": "true",
    "AUTONOMY_ENFORCE_QUALITY_GATE_DEFAULT": "true",
    "AUTONOMY_ENABLE_DYNAMIC_RISK_CONTROLS_DEFAULT": "true",
}


def _parse_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    return key.strip(), value


def _needs_quotes(value: str) -> bool:
    return any(ch.isspace() for ch in value) or "#" in value


def _format_value(value: str) -> str:
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        return value
    return f'"{value}"' if _needs_quotes(value) else value


def apply_profile() -> None:
    original_lines: list[str] = []
    if ENV_PATH.exists():
        original_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    existing: dict[str, str] = {}
    for line in original_lines:
        parsed = _parse_line(line)
        if parsed:
            existing[parsed[0]] = parsed[1]

    merged = dict(existing)
    merged.update(PROFILE_VALUES)

    rendered: list[str] = []
    seen: set[str] = set()
    for line in original_lines:
        parsed = _parse_line(line)
        if not parsed:
            rendered.append(line)
            continue
        key, _ = parsed
        if key in PROFILE_VALUES:
            rendered.append(f"{key}={_format_value(merged[key])}")
            seen.add(key)
        else:
            rendered.append(line)

    for key, value in PROFILE_VALUES.items():
        if key not in seen and key not in existing:
            rendered.append(f"{key}={_format_value(value)}")

    backup_path = ENV_PATH.parent / ".env.bak"
    if ENV_PATH.exists():
        backup_path.write_text("\n".join(original_lines) + "\n", encoding="utf-8")

    ENV_PATH.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
    print(f"Applied max-legal profile to: {ENV_PATH}")
    if backup_path.exists():
        print(f"Backup created: {backup_path}")
    for key in PROFILE_VALUES:
        print(f"{key}={PROFILE_VALUES[key]}")


if __name__ == "__main__":
    apply_profile()
