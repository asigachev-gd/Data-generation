"""Safe structured logging and optional Langfuse workflow telemetry.

Observability must never make a data operation fail.  Events intentionally contain
identifiers and operational outcomes only; prompts, generated rows, credentials,
and SQL result values are excluded unless a developer explicitly enables local
content capture.
"""

from __future__ import annotations

import atexit
import contextvars
import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.config import Settings

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
_telemetry: Telemetry | None = None
_SENSITIVE_TOKENS = ("password", "secret", "token", "credential", "authorization", "api_key")


class JsonFormatter(logging.Formatter):
    """Render application logs as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": _correlation_id.get(),
        }
        if hasattr(record, "event"):
            payload["event"] = redact(record.event)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging() -> None:
    """Configure process logging once without changing third-party loggers."""

    logger = logging.getLogger("app")
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def redact(value: Any, *, capture_content: bool = False) -> Any:
    """Recursively remove credentials and content-like fields from telemetry."""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in _SENSITIVE_TOKENS):
                result[str(key)] = "[REDACTED]"
            elif not capture_content and lowered in {
                "prompt",
                "question",
                "instructions",
                "sql",
                "rows",
                "data",
                "content",
            }:
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact(item, capture_content=capture_content)
        return result
    if isinstance(value, list | tuple | set):
        return [redact(item, capture_content=capture_content) for item in value]
    return value


class Telemetry:
    """A fail-open adapter around Langfuse, suitable for local use and tests."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.capture_content = bool(settings and settings.observability_capture_content)
        self.client: Any | None = None
        if settings and settings.langfuse_enabled:
            try:
                from langfuse import Langfuse

                self.client = Langfuse(
                    public_key=settings.langfuse_public_key.get_secret_value(),
                    secret_key=settings.langfuse_secret_key.get_secret_value(),
                    host=settings.langfuse_host,
                )
                atexit.register(self.flush)
            except Exception:
                logging.getLogger("app").warning(
                    "Langfuse initialization failed; telemetry disabled"
                )

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @contextmanager
    def workflow(self, name: str, **metadata: Any) -> Iterator[dict[str, Any]]:
        """Capture an outcome and latency without allowing telemetry to affect work."""

        correlation_id = uuid.uuid4().hex
        token = _correlation_id.set(correlation_id)
        started = time.perf_counter()
        safe_metadata = redact(metadata, capture_content=self.capture_content)
        trace: Any | None = None
        outcome: dict[str, Any] = {"validation_outcome": "started"}
        try:
            if self.client:
                trace = self.client.trace(
                    name=name,
                    metadata={"correlation_id": correlation_id, **safe_metadata},
                )
            yield outcome
            if outcome.get("validation_outcome") == "started":
                outcome["validation_outcome"] = "success"
        except Exception as error:
            outcome["validation_outcome"] = "failed"
            outcome["error_type"] = type(error).__name__
            raise
        finally:
            event = {
                **safe_metadata,
                **redact(outcome, capture_content=self.capture_content),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            logging.getLogger("app").info(
                "workflow completed", extra={"event": {"name": name, **event}}
            )
            if trace:
                try:
                    trace.update(output=event)
                except Exception:
                    logging.getLogger("app").warning("Langfuse trace update failed")
            _correlation_id.reset(token)

    def flush(self) -> None:
        if self.client:
            try:
                self.client.flush()
            except Exception:
                logging.getLogger("app").warning("Langfuse flush failed")


def configure_telemetry(settings: Settings | None) -> Telemetry:
    """Install the process telemetry adapter after validated settings are available."""

    global _telemetry
    configure_logging()
    _telemetry = Telemetry(settings)
    return _telemetry


def get_telemetry() -> Telemetry:
    """Return a disabled adapter until application startup configures telemetry."""

    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()
    return _telemetry
