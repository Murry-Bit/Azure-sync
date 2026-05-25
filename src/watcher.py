"""
Folder watcher built on top of the *watchdog* library.

Translates low-level watchdog events into ``FileEvent`` objects and feeds
them into a ``DebouncedEventQueue``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from src.event_queue import DebouncedEventQueue, EventType, FileEvent

logger = logging.getLogger(__name__)


class _FolderEventHandler(FileSystemEventHandler):
    """Internal watchdog handler that converts events to ``FileEvent`` objects."""

    def __init__(self, event_queue: DebouncedEventQueue) -> None:
        super().__init__()
        self._queue = event_queue

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        self._queue.put(FileEvent(EventType.CREATED, Path(event.src_path)))

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        self._queue.put(FileEvent(EventType.MODIFIED, Path(event.src_path)))

    def on_deleted(self, event: FileDeletedEvent) -> None:
        # On Windows, is_directory is unreliable for deleted events (always False).
        # Always queue as DELETED and let the sync engine handle both cases.
        self._queue.put(FileEvent(EventType.DELETED, Path(event.src_path)))

    def on_moved(self, event: FileMovedEvent) -> None:
        if event.is_directory:
            return
        self._queue.put(
            FileEvent(
                EventType.MOVED,
                Path(event.src_path),
                Path(event.dest_path),
            )
        )


class FolderWatcher:
    """Watches a directory tree for file changes and feeds events to a queue.

    Args:
        watch_folder: The root directory to monitor recursively.
        event_queue:  The queue to which ``FileEvent`` objects are delivered.
    """

    def __init__(
        self,
        watch_folder: Path,
        event_queue: DebouncedEventQueue,
    ) -> None:
        self._watch_folder = watch_folder
        self._handler = _FolderEventHandler(event_queue)
        self._observer = Observer()

    def start(self) -> None:
        """Start the background observer thread."""
        self._observer.schedule(
            self._handler, str(self._watch_folder), recursive=True
        )
        self._observer.start()
        logger.info("Watching folder: %s", self._watch_folder)

    def stop(self) -> None:
        """Stop the observer and wait for it to finish cleanly."""
        self._observer.stop()
        self._observer.join()
        logger.info("Stopped watching folder.")
