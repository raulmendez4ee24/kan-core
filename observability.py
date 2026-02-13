import json
import logging
import os
from typing import Any, Dict, Optional

try:
    import sentry_sdk
    from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
    from sentry_sdk.integrations.logging import LoggingIntegration
except Exception:  # pragma: no cover - optional dependency
    sentry_sdk = None
    SentryAsgiMiddleware = None
    LoggingIntegration = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "context"):
            payload["context"] = record.context
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def init_observability(app=None) -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers = [handler]

    if sentry_sdk and os.getenv("SENTRY_DSN"):
        integrations = []
        if LoggingIntegration:
            integrations.append(LoggingIntegration(level=logging.INFO, event_level=logging.ERROR))
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            environment=os.getenv("SENTRY_ENV", "production"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
            send_default_pii=False,
            integrations=integrations,
        )
        if app and SentryAsgiMiddleware:
            app.add_middleware(SentryAsgiMiddleware)


def capture_exception(exc: Exception, context: Optional[Dict[str, Any]] = None) -> None:
    logger = logging.getLogger("kan_core.error")
    exc_info = (type(exc), exc, exc.__traceback__)
    if context:
        logger.error("exception", extra={"context": context}, exc_info=exc_info)
    else:
        logger.error("exception", exc_info=exc_info)
    if sentry_sdk and os.getenv("SENTRY_DSN"):
        sentry_sdk.capture_exception(exc)
