import logging
import os
from typing import Any, Dict, Optional

import httpx

from observability import capture_exception
from tools.retry import request_with_retry

logger = logging.getLogger("kan_core.meta")


async def meta_request(
    method: str,
    path: str,
    *,
    access_token: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    base_url = os.getenv("META_GRAPH_BASE_URL", "https://graph.facebook.com/v20.0")
    url = f"{base_url.rstrip('/')}{path}"

    timeout = float(os.getenv("META_TIMEOUT", "10"))
    retries = int(os.getenv("META_RETRIES", "3"))
    backoff_base = float(os.getenv("META_BACKOFF_BASE", "0.5"))
    backoff_max = float(os.getenv("META_BACKOFF_MAX", "5.0"))

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await request_with_retry(
                client,
                method,
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
                retries=retries,
                backoff_base=backoff_base,
                backoff_max=backoff_max,
            )
            response.raise_for_status()
            if response.content:
                return response.json()
            return None
    except Exception as exc:
        logger.exception("Meta API request failed")
        capture_exception(exc, {"service": "meta", "url": url})
        return None
