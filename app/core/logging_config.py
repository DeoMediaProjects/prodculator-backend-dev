"""Structured logging setup.

When ``LOG_JSON`` is enabled, application and uvicorn logs are emitted as one JSON
object per line — queryable in a log aggregator (CloudWatch, Datadog, Loki, …) —
and each record carries the per-request ``request_id`` set by the request-ID
middleware in ``app.main``. When disabled, logging stays human-readable text for
local dev.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

from app.core.config import Settings

# Populated per-request by the request-ID middleware; None outside a request
# (e.g. startup, background jobs).
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes present on every LogRecord — anything not in here is treated as an
# explicit `extra=` field and merged into the JSON output.
_RESERVED = set(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Surface any structured `extra=` fields the caller attached.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    requested_level = (settings.LOG_LEVEL or ("DEBUG" if settings.DEBUG else "INFO")).upper()
    level = getattr(logging, requested_level, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    logging.getLogger("app").setLevel(level)

    if settings.LOG_JSON:
        formatter: logging.Formatter = JsonFormatter()
        # Ensure there is a handler to format (uvicorn installs its own; in other
        # contexts there may be none), then apply the JSON formatter everywhere so
        # app logs and uvicorn's own logs share one machine-readable format.
        if not root.handlers:
            root.addHandler(logging.StreamHandler())
        for handler in root.handlers:
            handler.setFormatter(formatter)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            for handler in logging.getLogger(name).handlers:
                handler.setFormatter(formatter)
