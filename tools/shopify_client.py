import logging
import os
from typing import Any, Dict, Optional

import httpx

from observability import capture_exception
from tools.retry import request_with_retry

logger = logging.getLogger("kan_core.shopify")


async def shopify_request(
    method: str,
    path: str,
    *,
    access_token: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    base_url = os.getenv("SHOPIFY_API_BASE", "https://{shop}.myshopify.com")
    shop = os.getenv("SHOPIFY_SHOP_DOMAIN")
    if "{shop}" in base_url:
        if not shop:
            raise ValueError("SHOPIFY_SHOP_DOMAIN is not set")
        base_url = base_url.format(shop=shop)

    url = f"{base_url.rstrip('/')}{path}"
    timeout = float(os.getenv("SHOPIFY_TIMEOUT", "10"))
    retries = int(os.getenv("SHOPIFY_RETRIES", "3"))
    backoff_base = float(os.getenv("SHOPIFY_BACKOFF_BASE", "0.5"))
    backoff_max = float(os.getenv("SHOPIFY_BACKOFF_MAX", "5.0"))

    headers = {
        "X-Shopify-Access-Token": access_token,
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
        logger.exception("Shopify API request failed")
        capture_exception(exc, {"service": "shopify", "url": url})
        return None
