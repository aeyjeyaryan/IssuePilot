"""Structured logging helpers.

Logs are emitted as single-line JSON so they can be grepped or piped into a
log shipper, and every record is mirrored onto the active :class:`EventBus`
so the web UI can display the same stream the terminal sees.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, Optional

from issuepilot.events import LOG, EventBus, current_bus

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Format log records as JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a JSON line."""
        payload: Dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class EventBusHandler(logging.Handler):
    """Forward log records to the event bus as ``log`` events."""

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        """Create the handler, optionally bound to a specific bus."""
        super().__init__()
        self._explicit_bus = bus
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """Publish the record."""
        try:
            bus = self._explicit_bus or current_bus.get()
            if bus is None:
                return
            extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
            bus.emit(LOG, level=record.levelname, message=record.getMessage(), **extra)
        except Exception:  # pragma: no cover - never break logging
            pass


def setup_logging(level: str = "INFO", json_output: bool = True, bus: Optional[EventBus] = None) -> None:
    """Configure root logging for the process.

    Args:
        level: Logging level name (e.g. ``"DEBUG"``).
        json_output: Emit JSON lines instead of plain text.
        bus: Optional bus to mirror records onto.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(stream)

    bus_handler = EventBusHandler(bus)
    bus_handler.setLevel(logging.INFO)
    root.addHandler(bus_handler)

    # These are very chatty at DEBUG level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
