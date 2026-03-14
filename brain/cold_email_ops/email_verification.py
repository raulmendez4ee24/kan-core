"""Fase 3: Email verification — ZeroBounce/NeverBounce if API key; else format + MX check (dnspython)."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple


def _normalize_email(s: str | None) -> str | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip().lower()
    if not s or "@" not in s:
        return None
    return s


def _check_format(email: str) -> bool:
    if not email or "@" not in email:
        return False
    local, domain = email.rsplit("@", 1)
    if not local or not domain or ".." in email:
        return False
    if not re.match(r"^[a-zA-Z0-9._%+-]+$", local):
        return False
    if not re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", domain):
        return False
    return True


def _check_mx(domain: str) -> bool:
    try:
        import dns.resolver  # type: ignore
        dns.resolver.resolve(domain, "MX")
        return True
    except Exception:
        return False


def _verify_with_zerobounce(email: str) -> str:
    api_key = os.getenv("ZEROBOUNCE_API_KEY", "").strip()
    if not api_key:
        return "unknown"
    try:
        import httpx
        r = httpx.get(
            "https://api.zerobounce.net/v2/validate",
            params={"api_key": api_key, "email": email},
            timeout=10.0,
        )
        if r.status_code != 200:
            return "unknown"
        j = r.json()
        status = (j.get("status") or "").lower()
        if status == "valid":
            return "valid"
        if status in ("invalid", "spamtrap", "abuse", "do_not_mail"):
            return "invalid"
        return "risky"
    except Exception:
        return "unknown"


def _verify_with_neverbounce(email: str) -> str:
    api_key = os.getenv("NEVERBOUNCE_API_KEY", "").strip()
    if not api_key:
        return "unknown"
    try:
        import httpx
        r = httpx.get(
            "https://api.neverbounce.com/v4/single/check",
            params={"key": api_key, "email": email},
            timeout=10.0,
        )
        if r.status_code != 200:
            return "unknown"
        j = r.json()
        result = (j.get("result") or "").lower()
        if result == "valid":
            return "valid"
        if result in ("invalid", "disposable", "catchall"):
            return "invalid"
        return "risky"
    except Exception:
        return "unknown"


def run(
    data: Dict[str, List[Dict[str, Any]]],
    log: List[str],
    resumen: List[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str], List[str]]:
    """Set email_validation_status per lead. Prefer ZeroBounce, else NeverBounce, else format+MX."""
    leads = data.get("leads", [])
    if not leads:
        return data, log, resumen

    use_zerobounce = bool(os.getenv("ZEROBOUNCE_API_KEY", "").strip())
    use_neverbounce = bool(os.getenv("NEVERBOUNCE_API_KEY", "").strip())

    updated = []
    for row in leads:
        d = dict(row)
        email = _normalize_email(d.get("email"))
        if not email:
            d["email_validation_status"] = "invalid"
            updated.append(d)
            continue
        status = "unknown"
        if use_zerobounce:
            status = _verify_with_zerobounce(email)
        elif use_neverbounce:
            status = _verify_with_neverbounce(email)
        else:
            if not _check_format(email):
                status = "invalid"
            else:
                domain = email.rsplit("@", 1)[1]
                status = "valid" if _check_mx(domain) else "risky"
        d["email_validation_status"] = status
        updated.append(d)

    data = {**data, "leads": updated}
    valid = sum(1 for r in updated if r.get("email_validation_status") == "valid")
    invalid = sum(1 for r in updated if r.get("email_validation_status") == "invalid")
    log.append(f"Email verification: {valid} valid, {invalid} invalid, rest risky/unknown")
    resumen.append(f"Leads valid email: {valid}")
    return data, log, resumen
