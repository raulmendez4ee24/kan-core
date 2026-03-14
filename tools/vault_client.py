import os
from typing import Optional

import httpx


def vault_enabled() -> bool:
    return bool(os.getenv("VAULT_ADDR") and os.getenv("VAULT_TOKEN"))


def _vault_mount() -> str:
    return os.getenv("VAULT_MOUNT", "secret").strip("/") or "secret"


def _build_url(path: str) -> str:
    base = (os.getenv("VAULT_ADDR") or "").rstrip("/")
    mount = _vault_mount()
    return f"{base}/v1/{mount}/data/{path.lstrip('/')}"


def _headers() -> dict:
    return {
        "X-Vault-Token": os.getenv("VAULT_TOKEN", ""),
        "Content-Type": "application/json",
    }


async def write_secret(path: str, secret: str) -> bool:
    if not vault_enabled():
        return False
    payload = {"data": {"secret": secret}}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(_build_url(path), headers=_headers(), json=payload)
        response.raise_for_status()
        return True
    except Exception:
        return False


async def read_secret(path: str) -> Optional[str]:
    if not vault_enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(_build_url(path), headers=_headers())
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        inner = data.get("data") if isinstance(data, dict) else None
        secret = inner.get("secret") if isinstance(inner, dict) else None
        if isinstance(secret, str) and secret.strip():
            return secret
    except Exception:
        return None
    return None
