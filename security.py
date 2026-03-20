import base64
import hashlib
import hmac
import json
import os
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_security_manager, get_session
from models import Client


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load_env_tokens() -> dict:
    raw = os.getenv("CLIENT_TOKENS_JSON", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _token_matches(expected: str, provided: str) -> bool:
    return expected == provided or expected == _hash_token(provided)


async def require_client_token(
    x_client_id: str = Header(..., alias="X-Client-Id"),
    x_client_token: str = Header(..., alias="X-Client-Token"),
    session: AsyncSession = Depends(get_session),
) -> str:
    return await validate_client_credentials(
        session,
        x_client_id=x_client_id,
        x_client_token=x_client_token,
    )


async def validate_client_credentials(
    session: AsyncSession,
    *,
    x_client_id: str,
    x_client_token: str,
) -> str:
    env_tokens = _load_env_tokens()
    if x_client_id in env_tokens and _token_matches(
        env_tokens[x_client_id], x_client_token
    ):
        return x_client_id

    try:
        client_id = UUID(x_client_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid client id")

    client = await session.get(Client, client_id)
    if not client or not client.is_active:
        raise HTTPException(status_code=401, detail="Client inactive")
    try:
        decrypted = get_security_manager().decrypt_token(client.api_key_encrypted)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if decrypted != x_client_token:
        raise HTTPException(status_code=401, detail="Invalid token")

    return str(client.id)


def default_client_id() -> str:
    """Resolve default client ID from env vars. Used by routes with optional X-Client-Id."""
    for env_name in ("KAN_CLIENT_ID", "CLIENT_ID", "YCLOUD_DEFAULT_CLIENT_ID"):
        value = str(os.getenv(env_name) or "").strip()
        if value:
            return value
    return ""


async def require_client_token_with_fallback(
    x_client_token: str = Header(..., alias="X-Client-Token"),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    session: AsyncSession = Depends(get_session),
) -> str:
    """Like require_client_token but falls back to env-based client ID when header is missing."""
    client_id = str(x_client_id or "").strip() or default_client_id()
    if not client_id:
        raise HTTPException(status_code=401, detail="Missing client id")
    return await validate_client_credentials(
        session,
        x_client_id=client_id,
        x_client_token=x_client_token,
    )


def verify_meta_signature(raw_body: bytes, header_signature: Optional[str]) -> bool:
    require_signatures = os.getenv("REQUIRE_WEBHOOK_SIGNATURES", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    app_secret = os.getenv("META_APP_SECRET")
    if not require_signatures:
        return True
    if not app_secret:
        return False
    if not header_signature:
        return False

    expected = "sha256=" + hmac.new(
        app_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_signature)


def verify_shopify_hmac(raw_body: bytes, header_hmac: Optional[str]) -> bool:
    secret = os.getenv("SHOPIFY_WEBHOOK_SECRET")
    if not secret:
        return True
    if not header_hmac:
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, header_hmac)
