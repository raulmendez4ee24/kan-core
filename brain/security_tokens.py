import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import Header, HTTPException


def _secret_key() -> bytes:
    secret = os.getenv("INTERNAL_JWT_SECRET") or os.getenv("MASTER_KEY") or "kan-core-dev-secret"
    return secret.encode()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def _sign(data: bytes) -> str:
    sig = hmac.new(_secret_key(), data, hashlib.sha256).digest()
    return _b64url_encode(sig)


def issue_token(
    *,
    user_id: str,
    organization_id: Optional[str],
    role: str,
    permissions: List[str],
    expires_in_minutes: int,
) -> tuple[str, int]:
    now = int(time.time())
    exp = now + max(60, int(expires_in_minutes) * 60)
    default_perms = {
        "SUPER_ADMIN": {
            "admin.audit.read",
            "admin.clients.read",
            "admin.clients.impersonate",
            "org.policies.read",
            "org.policies.write",
            "desktop.commands.create",
            "desktop.commands.approve",
            "tasks.read",
            "tasks.write",
            "tasks.run",
            "evidence.read",
            "workflows.recordings.write",
            "workflows.recordings.read",
            "workflows.playbooks.write",
            "workflows.playbooks.read",
            "workflows.playbooks.run",
            "workflows.playbooks.export",
            "predictive.followups.run",
        },
        "PLATFORM_ADMIN": {
            "admin.audit.read",
            "admin.clients.read",
            "org.policies.read",
        },
        "ORG_ADMIN": {
            "autonomy.objectives.write",
            "autonomy.missions.execute",
            "crm.leads.write",
            "alerts.rules.write",
            "org.policies.read",
            "org.policies.write",
            "desktop.commands.create",
            "desktop.commands.approve",
            "tasks.read",
            "tasks.write",
            "tasks.run",
            "evidence.read",
            "workflows.recordings.write",
            "workflows.recordings.read",
            "workflows.playbooks.write",
            "workflows.playbooks.read",
            "workflows.playbooks.run",
            "workflows.playbooks.export",
            "predictive.followups.run",
        },
        "ORG_USER": {
            "crm.leads.read",
            "autonomy.recommendations.read",
            "tasks.read",
            "workflows.recordings.read",
            "workflows.playbooks.read",
        },
    }
    role_permissions = default_perms.get(role.upper(), set())
    merged_permissions = sorted(set(permissions or []).union(role_permissions))
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "org": organization_id,
        "role": role,
        "permissions": merged_permissions,
        "iat": now,
        "exp": exp,
    }

    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    s = _sign(signing_input)
    return f"{h}.{p}.{s}", exp


def decode_token(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid token format")
    h, p, s = parts
    signing_input = f"{h}.{p}".encode()
    expected = _sign(signing_input)
    if not hmac.compare_digest(expected, s):
        raise HTTPException(status_code=401, detail="Invalid token signature")

    try:
        payload = json.loads(_b64url_decode(p).decode())
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc

    exp = int(payload.get("exp") or 0)
    if exp <= int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")
    return payload


def parse_bearer(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix) :].strip()
    return authorization.strip()


async def require_user_principal(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Dict[str, Any]:
    token = parse_bearer(authorization)
    return decode_token(token)


def require_roles(payload: Dict[str, Any], allowed_roles: List[str]) -> None:
    role = str(payload.get("role") or "")
    if role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Insufficient role")


def require_permissions(payload: Dict[str, Any], required: List[str]) -> None:
    permissions = {str(p) for p in payload.get("permissions") or []}
    missing = [perm for perm in required if perm not in permissions]
    if missing:
        raise HTTPException(status_code=403, detail=f"Missing permissions: {', '.join(missing)}")
