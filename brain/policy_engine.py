import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.policy_presets import build_default_autonomy_profile
from models import OrgPolicy


def _enabled() -> bool:
    # Policies are now enabled by default with a safe profile.
    return os.getenv("ENABLE_ENTERPRISE_POLICIES", "true").lower() in {"1", "true", "yes"}


def _auto_preset() -> str:
    return str(os.getenv("POLICY_AUTO_APPLY_PRESET", "")).strip().lower()


def _default_require_domain_allowlist() -> bool:
    return _auto_preset() != "openclaw_allow_all"


def _default_require_open_program_allowlist() -> bool:
    return _auto_preset() != "openclaw_allow_all"


def _normalize_domains(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for item in items:
        token = str(item or "").strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def _normalize_program_names(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for item in items:
        # Program names are matched against the desktop agent allowlist keys, which
        # are normalized to lowercase in the agent config.
        token = str(item or "").strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def _parse_default_business_hours() -> Tuple[int, int]:
    raw = os.getenv("POLICY_DEFAULT_BUSINESS_HOURS_JSON", "[9,19]")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [9, 19]
    if not isinstance(data, list) or len(data) != 2:
        return 9, 19
    try:
        start = int(data[0])
        end = int(data[1])
    except Exception:
        return 9, 19
    start = max(0, min(start, 23))
    end = max(1, min(end, 24))
    if end <= start:
        end = min(start + 1, 24)
    return start, end


@dataclass(frozen=True)
class EffectivePolicy:
    organization_id: UUID
    policy: Dict[str, Any]
    updated_at: Optional[datetime]
    enforced: bool

    def browser_domain_allowlist(self) -> List[str]:
        browser = self.policy.get("browser") if isinstance(self.policy.get("browser"), dict) else {}
        direct = self.policy.get("browser_domain_allowlist")
        candidates = browser.get("domain_allowlist") if isinstance(browser, dict) else None
        return _normalize_domains(candidates if candidates is not None else direct)

    def open_program_allowlist(self) -> List[str]:
        desktop = self.policy.get("desktop") if isinstance(self.policy.get("desktop"), dict) else {}
        direct = self.policy.get("desktop_open_program_allowlist")
        candidates = desktop.get("allow_open_programs") if isinstance(desktop, dict) else None
        return _normalize_program_names(candidates if candidates is not None else direct)

    def business_hours(self) -> Tuple[int, int]:
        value = self.policy.get("business_hours")
        if isinstance(value, list) and len(value) == 2:
            try:
                start = int(value[0])
                end = int(value[1])
                start = max(0, min(start, 23))
                end = max(1, min(end, 24))
                if end <= start:
                    end = min(start + 1, 24)
                return start, end
            except Exception:
                pass
        return _parse_default_business_hours()

    def allow_execute_campaigns(self) -> bool:
        autonomy = self.policy.get("autonomy") if isinstance(self.policy.get("autonomy"), dict) else {}
        direct = self.policy.get("allow_execute_campaigns")
        value = autonomy.get("allow_execute_campaigns") if isinstance(autonomy, dict) else None
        if isinstance(value, bool):
            return value
        if isinstance(direct, bool):
            return direct
        return self.autonomy_execution_mode() in {"semi_auto", "full_auto"}

    def allow_workflow_auto_recovery(self) -> bool:
        workflow = self.policy.get("workflow") if isinstance(self.policy.get("workflow"), dict) else {}
        value = workflow.get("allow_auto_recovery")
        if isinstance(value, bool):
            return value
        return True

    def autonomy_execution_mode(self) -> str:
        """safe|semi_auto|full_auto."""
        if not self.enforced:
            fallback = os.getenv("AUTONOMY_DEFAULT_EXECUTION_MODE", "full_auto").strip().lower()
            return fallback if fallback in {"safe", "semi_auto", "full_auto"} else "full_auto"
        autonomy = self.policy.get("autonomy") if isinstance(self.policy.get("autonomy"), dict) else {}
        direct = self.policy.get("autonomy_execution_mode")
        value = autonomy.get("execution_mode") if isinstance(autonomy, dict) else None
        token = value if isinstance(value, str) else (direct if isinstance(direct, str) else None)
        token = (token or "").strip().lower()
        if token in {"safe", "semi_auto", "full_auto"}:
            return token
        fallback = os.getenv("AUTONOMY_DEFAULT_EXECUTION_MODE", "full_auto").strip().lower()
        return fallback if fallback in {"safe", "semi_auto", "full_auto"} else "full_auto"

    def max_auto_risk(self) -> str:
        """low|medium|high."""
        if not self.enforced:
            fallback = os.getenv("AUTONOMY_DEFAULT_MAX_AUTO_RISK", "high").strip().lower()
            return fallback if fallback in {"low", "medium", "high"} else "high"
        autonomy = self.policy.get("autonomy") if isinstance(self.policy.get("autonomy"), dict) else {}
        direct = self.policy.get("autonomy_max_auto_risk")
        value = autonomy.get("max_auto_risk") if isinstance(autonomy, dict) else None
        token = value if isinstance(value, str) else (direct if isinstance(direct, str) else None)
        token = (token or "").strip().lower()
        if token in {"low", "medium", "high"}:
            return token
        fallback = os.getenv("AUTONOMY_DEFAULT_MAX_AUTO_RISK", "high").strip().lower()
        return fallback if fallback in {"low", "medium", "high"} else "high"

    def auto_approve_enabled_for(self, action: str) -> bool:
        if not self.enforced:
            return self.autonomy_execution_mode() in {"semi_auto", "full_auto"}
        token = str(action or "").strip().lower()
        is_browser = token.startswith("browser_")
        section = "browser" if is_browser else "desktop"
        scoped = self.policy.get(section) if isinstance(self.policy.get(section), dict) else {}
        value = scoped.get("auto_approve_enabled") if isinstance(scoped, dict) else None
        if isinstance(value, bool):
            return value
        # Default: enable auto-approval when autonomy is not "safe".
        return self.autonomy_execution_mode() in {"semi_auto", "full_auto"}

    def guardrail_require_domain_allowlist_for_browser(self) -> bool:
        if not self.enforced:
            return False
        guardrails = self.policy.get("guardrails") if isinstance(self.policy.get("guardrails"), dict) else {}
        value = guardrails.get("require_domain_allowlist_for_browser") if isinstance(guardrails, dict) else None
        if isinstance(value, bool):
            return value
        return _default_require_domain_allowlist()

    def guardrail_require_open_program_allowlist(self) -> bool:
        if not self.enforced:
            return False
        guardrails = self.policy.get("guardrails") if isinstance(self.policy.get("guardrails"), dict) else {}
        value = guardrails.get("require_open_program_allowlist") if isinstance(guardrails, dict) else None
        if isinstance(value, bool):
            return value
        return _default_require_open_program_allowlist()

    def circuit_breaker_config(self) -> Dict[str, Any]:
        if not self.enforced:
            return {"enabled": False}
        guardrails = self.policy.get("guardrails") if isinstance(self.policy.get("guardrails"), dict) else {}
        cb = guardrails.get("circuit_breaker") if isinstance(guardrails.get("circuit_breaker"), dict) else {}
        enabled = cb.get("enabled")
        threshold = cb.get("error_rate_threshold")
        window_minutes = cb.get("window_minutes")
        open_minutes = cb.get("open_minutes")
        return {
            "enabled": bool(enabled) if isinstance(enabled, bool) else False,
            "error_rate_threshold": float(threshold) if isinstance(threshold, (int, float)) else 0.45,
            "window_minutes": int(window_minutes) if isinstance(window_minutes, int) else 15,
            "open_minutes": int(open_minutes) if isinstance(open_minutes, int) else 10,
        }

    def learning_exploration_epsilon(self) -> float:
        if not self.enforced:
            return 0.0
        learning = self.policy.get("learning") if isinstance(self.policy.get("learning"), dict) else {}
        value = learning.get("exploration_epsilon") if isinstance(learning, dict) else None
        if isinstance(value, (int, float)):
            return max(0.0, min(float(value), 1.0))
        raw = os.getenv("LEARNING_EXPLORATION_EPSILON", "0.10")
        try:
            env_value = float(raw)
        except ValueError:
            env_value = 0.10
        return max(0.0, min(env_value, 1.0))

    def tool_weights(self) -> Dict[str, float]:
        if not self.enforced:
            return {}
        learning = self.policy.get("learning") if isinstance(self.policy.get("learning"), dict) else {}
        weights = learning.get("tool_weights") if isinstance(learning.get("tool_weights"), dict) else {}
        out: Dict[str, float] = {}
        for key, value in weights.items():
            if not isinstance(key, str):
                continue
            if not isinstance(value, (int, float)):
                continue
            out[key] = float(value)
        return out


async def load_effective_policy(
    session: AsyncSession,
    *,
    organization_id: UUID,
) -> EffectivePolicy:
    enforced = _enabled()
    if not enforced:
        return EffectivePolicy(organization_id=organization_id, policy={}, updated_at=None, enforced=False)

    stmt = select(OrgPolicy).where(OrgPolicy.organization_id == organization_id).limit(1)
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        profile = os.getenv("AUTONOMY_DEFAULT_PROFILE", "safe").strip().lower()
        return EffectivePolicy(
            organization_id=organization_id,
            policy=build_default_autonomy_profile(profile),
            updated_at=None,
            enforced=True,
        )
    return EffectivePolicy(
        organization_id=organization_id,
        policy=row.policy if isinstance(row.policy, dict) else {},
        updated_at=row.updated_at,
        enforced=True,
    )


def _host_allowed(host: str, allowlist: List[str]) -> bool:
    host = (host or "").strip().lower()
    if not host:
        return False
    for domain in allowlist:
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False


def validate_browser_url(
    url: str,
    *,
    allowlist: List[str],
    require_allowlist: bool = False,
) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL"
    scheme = (parsed.scheme or "").strip().lower()
    host = (parsed.hostname or "").strip().lower()
    if scheme not in {"http", "https"} or not host:
        return "URL must be http(s) with a hostname"
    if require_allowlist and not allowlist:
        return "Browser domain allowlist is required by policy"
    if allowlist and not _host_allowed(host, allowlist):
        return "URL domain is not allowed by policy"
    return None


def validate_desktop_action(
    *,
    action: str,
    args: Dict[str, Any],
    policy: EffectivePolicy,
) -> Optional[str]:
    action_name = str(action or "").strip().lower()

    # Generic policy rules (MVP): deny_actions.
    rule_error = policy_denies_action(action=action_name, args=args, policy=policy)
    if rule_error:
        return rule_error

    if action_name == "open_program":
        allowlist = policy.open_program_allowlist()
        if policy.guardrail_require_open_program_allowlist() and not allowlist:
            return "open_program blocked: allowlist is required by policy"
        if allowlist:
            name = str(args.get("name") or "").strip().lower()
            if not name:
                return "open_program requires name"
            if name not in allowlist:
                return "open_program blocked by policy allowlist"

    if action_name == "browser_goto":
        url = args.get("url")
        if isinstance(url, str) and url.strip():
            err = validate_browser_url(
                url,
                allowlist=policy.browser_domain_allowlist(),
                require_allowlist=policy.guardrail_require_domain_allowlist_for_browser(),
            )
            if err:
                return err

    if action_name == "browser_launch":
        url = args.get("start_url")
        if isinstance(url, str) and url.strip():
            err = validate_browser_url(
                url,
                allowlist=policy.browser_domain_allowlist(),
                require_allowlist=policy.guardrail_require_domain_allowlist_for_browser(),
            )
            if err:
                return err

    return None


def _get_arg_value(args: Dict[str, Any], path: str) -> Any:
    """Support shallow or dotted key access into args dict."""
    token = str(path or "").strip()
    if not token:
        return None
    if "." not in token:
        return args.get(token)
    current: Any = args
    for part in token.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _compare(op: str, left: Any, right: Any) -> bool:
    op = str(op or "").strip()
    if op in {"==", "eq"}:
        return left == right
    if op in {"!=", "ne"}:
        return left != right
    if op in {">", ">=", "<", "<="}:
        try:
            lf = float(left)
            rf = float(right)
        except Exception:
            return False
        if op == ">":
            return lf > rf
        if op == ">=":
            return lf >= rf
        if op == "<":
            return lf < rf
        if op == "<=":
            return lf <= rf
    if op in {"contains", "in"}:
        if isinstance(left, (list, tuple, set)):
            return right in left
        if isinstance(left, str):
            return str(right) in left
        return False
    return False


def _rules(policy: EffectivePolicy) -> List[Dict[str, Any]]:
    payload = policy.policy if isinstance(policy.policy, dict) else {}
    rules = payload.get("rules")
    if isinstance(rules, list):
        return [x for x in rules if isinstance(x, dict)]
    return []


def policy_denies_action(*, action: str, args: Dict[str, Any], policy: EffectivePolicy) -> Optional[str]:
    """Return a human-readable error when the policy denies this action."""
    action_name = str(action or "").strip().lower()
    for rule in _rules(policy):
        denies = rule.get("deny_actions")
        if isinstance(denies, list):
            deny_set = {str(x or "").strip().lower() for x in denies}
            if action_name in deny_set:
                return f"Action '{action_name}' is denied by policy"
    return None


def policy_requires_human_approval_for_action(*, action: str, args: Dict[str, Any], policy: EffectivePolicy) -> bool:
    """Return True when policy rules require human approval for this action."""
    action_name = str(action or "").strip().lower()
    for rule in _rules(policy):
        cond = rule.get("require_approval_when")
        candidates: List[Dict[str, Any]] = []
        if isinstance(cond, dict):
            candidates = [cond]
        elif isinstance(cond, list):
            candidates = [x for x in cond if isinstance(x, dict)]
        for item in candidates:
            if str(item.get("action") or "").strip().lower() != action_name:
                continue
            arg_path = str(item.get("arg") or "").strip()
            op = str(item.get("op") or "").strip()
            value = item.get("value")
            left = _get_arg_value(args, arg_path)
            if _compare(op, left, value):
                return True
    return False
