"""Event bus used to stream agent activity to the CLI and the web UI.

The bus is deliberately tiny and thread-safe: the agent graph runs inside a
worker thread while the HTTP layer consumes events from an asyncio queue.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: Event kinds emitted by the agent.
STEP_START = "step_start"
STEP_END = "step_end"
LOG = "log"
DIFF = "diff"
TEST_REPORT = "test_report"
REPORT = "report"
ERROR = "error"
DONE = "done"


@dataclass
class Event:
    """A single agent event."""

    kind: str
    data: Dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the event for JSON transport."""
        return {"kind": self.kind, "data": self.data, "run_id": self.run_id, "ts": self.ts}


class EventBus:
    """Fan-out of agent events to any number of subscribers."""

    def __init__(self, run_id: str = "") -> None:
        """Create a bus for a given run."""
        self.run_id = run_id
        self._subscribers: List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._history: List[Event] = []
        self._lock = threading.Lock()
        self._closed = False

    # -- subscription ------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        """Register an asyncio queue as a subscriber (call from the loop thread)."""
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.append((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        with self._lock:
            self._subscribers = [(l, q) for l, q in self._subscribers if q is not queue]

    # -- publishing --------------------------------------------------------
    def emit(self, kind: str, **data: Any) -> Event:
        """Publish an event to all subscribers."""
        event = Event(kind=kind, data=data, run_id=self.run_id)
        with self._lock:
            self._history.append(event)
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # pragma: no cover - loop already closed
                pass
        return event

    def history(self) -> List[Event]:
        """Return every event emitted so far."""
        with self._lock:
            return list(self._history)

    def close(self) -> None:
        """Close the bus and wake up all subscribers."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, Event(kind=DONE, run_id=self.run_id))
            except RuntimeError:  # pragma: no cover
                pass


#: The bus of the currently executing run (context-local).
current_bus: contextvars.ContextVar[Optional[EventBus]] = contextvars.ContextVar("issuepilot_bus", default=None)


def get_bus() -> Optional[EventBus]:
    """Return the event bus bound to the current context, if any."""
    return current_bus.get()


def emit(kind: str, **data: Any) -> None:
    """Emit an event on the current bus (no-op when no bus is bound)."""
    bus = current_bus.get()
    if bus is not None:
        bus.emit(kind, **data)
