import asyncio
import logging
import random
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("kan_core.retry")

RETRYABLE_STATUS = {408, 409, 425, 429}
RETRYABLE_STATUS.update(range(500, 600))


def _compute_backoff(attempt: int, base: float, maximum: float) -> float:
    delay = min(maximum, base * (2 ** (attempt - 1)))
    jitter = 0.5 + random.random() * 0.5
    return delay * jitter


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
    retries: int = 3,
    backoff_base: float = 0.5,
    backoff_max: float = 5.0,
) -> httpx.Response:
    last_exc: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            response = await client.request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            if response.status_code in RETRYABLE_STATUS:
                last_exc = httpx.HTTPStatusError(
                    "Retryable HTTP status",
                    request=response.request,
                    response=response,
                )
                raise last_exc
            return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            await asyncio.sleep(_compute_backoff(attempt, backoff_base, backoff_max))

    if last_exc:
        raise last_exc
    raise RuntimeError("Retry failed without exception")


async def post_json_with_retry(
    url: str,
    payload: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    retries: int = 3,
    backoff_base: float = 0.5,
    backoff_max: float = 5.0,
) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await request_with_retry(
            client,
            "POST",
            url,
            json=payload,
            params=params,
            headers=headers,
            timeout=timeout,
            retries=retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
        )
