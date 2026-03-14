import os
from typing import Optional

import httpx


def _enabled() -> bool:
    return bool(os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")))


def _base_url() -> str:
    return (os.getenv("SUPABASE_URL") or "").rstrip("/")


def _service_key() -> str:
    return (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or "").strip()


def _headers(content_type: Optional[str] = None, *, upsert: bool = True) -> dict:
    headers = {
        "Authorization": f"Bearer {_service_key()}",
        "apikey": _service_key(),
    }
    if content_type:
        headers["Content-Type"] = content_type
    if upsert:
        headers["x-upsert"] = "true"
    return headers


async def upload_bytes(
    *,
    bucket: str,
    path: str,
    data: bytes,
    content_type: str,
    upsert: bool = True,
) -> bool:
    """Upload a binary object to Supabase Storage.

    Uses Storage REST API to avoid SDK version drift.
    """
    if not _enabled():
        return False
    base = _base_url()
    if not base:
        return False
    bucket = str(bucket or "").strip()
    path = str(path or "").lstrip("/")
    if not bucket or not path:
        return False

    url = f"{base}/storage/v1/object/{bucket}/{path}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                headers=_headers(content_type, upsert=upsert),
                content=data,
            )
        response.raise_for_status()
        return True
    except Exception:
        return False


async def create_signed_url(
    *,
    bucket: str,
    path: str,
    expires_in: int,
) -> Optional[str]:
    if not _enabled():
        return None
    base = _base_url()
    if not base:
        return None
    bucket = str(bucket or "").strip()
    path = str(path or "").lstrip("/")
    if not bucket or not path:
        return None

    expires_in = max(30, min(int(expires_in or 0), 60 * 60 * 24))
    url = f"{base}/storage/v1/object/sign/{bucket}/{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers=_headers(None, upsert=False),
                json={"expiresIn": expires_in},
            )
        response.raise_for_status()
        payload = response.json()
        signed = payload.get("signedURL") if isinstance(payload, dict) else None
        if not isinstance(signed, str) or not signed.strip():
            return None
        return f"{base}{signed}"
    except Exception:
        return None

