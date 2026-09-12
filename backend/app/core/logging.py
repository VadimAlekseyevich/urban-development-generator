import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

LogContext = dict[str, str]
_log_context: contextvars.ContextVar[LogContext] = contextvars.ContextVar(
    "urban_generator_log_context", default={}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_log_context.get())

        event_fields = getattr(record, "event_fields", None)
        if isinstance(event_fields, dict):
            payload.update(event_fields)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level.upper())

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True


def bind_log_context(**fields: str | None) -> contextvars.Token[LogContext]:
    current = dict(_log_context.get())
    current.update({key: value for key, value in fields.items() if value is not None})
    return _log_context.set(current)


def reset_log_context(token: contextvars.Token[LogContext]) -> None:
    _log_context.reset(token)
