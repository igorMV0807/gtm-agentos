import json
import logging
from datetime import UTC, datetime
from typing import Any


_STANDARD_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}
_SENSITIVE_LOG_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "email",
    "password",
    "prompt",
    "secret",
    "token",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_"):
                normalized = key.lower().replace("-", "_")
                payload[key] = (
                    "[REDACTED]"
                    if any(part in normalized for part in _SENSITIVE_LOG_KEY_PARTS)
                    else _safe_log_value(value)
                )
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


def _safe_log_value(value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (list, tuple)):
        return [_safe_log_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _safe_log_value(item)
            for key, item in list(value.items())[:20]
            if not any(
                part in str(key).lower().replace("-", "_")
                for part in _SENSITIVE_LOG_KEY_PARTS
            )
        }
    return str(value)[:500]
