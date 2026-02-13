import logging
import os
from typing import Any, Dict

from observability import capture_exception
from tools.retry import post_json_with_retry

logger = logging.getLogger("kan_core.n8n")


async def send_to_n8n(payload: Dict[str, Any]) -> None:
    webhook_url = os.getenv("N8N_WEBHOOK_URL")
    if not webhook_url:
        return

    timeout = float(os.getenv("N8N_TIMEOUT", "10"))
    retries = int(os.getenv("N8N_RETRIES", "3"))
    backoff_base = float(os.getenv("N8N_BACKOFF_BASE", "0.5"))
    backoff_max = float(os.getenv("N8N_BACKOFF_MAX", "5.0"))
    try:
        response = await post_json_with_retry(
            webhook_url,
            payload,
            timeout=timeout,
            retries=retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.exception("n8n webhook request failed")
        capture_exception(exc, {"service": "n8n"})
