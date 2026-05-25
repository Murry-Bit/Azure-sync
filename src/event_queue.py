"""
Thread-safe event queue with per-path debouncing.

Rapid file-system events for the same path (e.g. a large file being written
in many small chunks) are coalesced: only the *last* event is forwarded after
the debounce timer expires.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class EventType(Enum):
    CREATED = auto()
    MODIFIED = auto()
    DELETED = auto()
    MOVED = auto()
    DIR_DELETED = auto()


@dataclass
class FileEvent:
    """Represents a single file-system change."""

    event_type: EventType
    src_path: Path
    dest_path: Optional[Path] = field(default=None)
    """Only populated for MOVED events."""


class DebouncedEventQueue:
    """Accepts file-system events and delivers them to workers after a quiet period.

    For each unique ``src_path`` a timer is (re-)started on every incoming
    event.  Once the timer fires without being reset, the *latest* event for
    that path is placed on the internal queue for consumption via ``get()``.

    Args:
        debounce_seconds: How long to wait after the most recent event before
            forwarding it.
    """

    def __init__(self, debounce_seconds: float = 2.0) -> None:
        self._debounce = debounce_seconds
        self._queue: queue.Queue[FileEvent] = queue.Queue()
        # Maps src_path → (active timer, latest event)
        self._pending: dict[Path, tuple[threading.Timer, FileEvent]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------

    def put(self, event: FileEvent) -> None:
        """Accept an event, resetting the debounce timer for its path."""
        with self._lock:
            existing = self._pending.get(event.src_path)
            if existing is not None:
                existing[0].cancel()
            timer = threading.Timer(
                self._debounce, self._flush, args=(event.src_path,)
            )
            self._pending[event.src_path] = (timer, event)
            timer.daemon = True
            timer.start()

    def _flush(self, path: Path) -> None:
        """Called by a timer thread; moves the event to the real queue."""
        with self._lock:
            entry = self._pending.pop(path, None)
        if entry is not None:
            _, event = entry
            self._queue.put(event)
            logger.debug("Queued event: %s %s", event.event_type.name, event.src_path)

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    def get(self, timeout: Optional[float] = 1.0) -> Optional[FileEvent]:
        """Block up to *timeout* seconds for the next event.

        Returns ``None`` on timeout so callers can check a stop condition.
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def task_done(self) -> None:
        """Signal that the previously returned event has been processed."""
        self._queue.task_done()
