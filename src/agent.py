"""
Main orchestrator: ties together watcher, event queue, and sync engine.

Lifecycle:
1. ``BackupAgent.__init__`` — wires up all components.
2. ``BackupAgent.run()``    — starts watcher + workers + optional initial sync;
                              blocks until interrupted or ``stop()`` is called.
3. ``BackupAgent.stop()``   — signals all threads to exit cleanly.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from src.blob_client import BlobStorageClient
from src.config import AppConfig
from src.event_queue import DebouncedEventQueue, EventType
from src.sync_engine import SyncEngine
from src.watcher import FolderWatcher

logger = logging.getLogger(__name__)


class BackupAgent:
    """Monitors a local folder and keeps it mirrored in Azure Blob Storage.

    Args:
        config: Fully-loaded application configuration.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._stop_event = threading.Event()

        # Use explicit container name from config, or derive from Windows username.
        if config.azure.container_name:
            container_name = config.azure.container_name
            logger.info("Using explicit container: %s", container_name)
        else:
            username = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
            container_name = config.container_name(username)
            logger.info(
                "User: %s  →  container: %s", username, container_name
            )

        self._blob_client = BlobStorageClient(config, container_name)
        self._sync_engine = SyncEngine(config, self._blob_client)
        self._event_queue = DebouncedEventQueue(config.sync.debounce_seconds)
        self._watcher = FolderWatcher(config.watch_folder, self._event_queue)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the agent and block until stopped or interrupted.

        Performs an optional initial full sync, then enters the event loop.
        Safe to call from the main thread.
        """
        logger.info(
            "Backup agent starting.  Watch folder: %s", self._config.watch_folder
        )
        self._config.watch_folder.mkdir(parents=True, exist_ok=True)

        if self._config.sync.initial_sync_on_start:
            try:
                self._sync_engine.initial_sync()
            except Exception as exc:
                # Log but do not abort – incremental sync still works.
                logger.error(
                    "Initial sync failed: %s", exc, exc_info=True
                )

        self._watcher.start()

        workers = [
            threading.Thread(
                target=self._worker,
                name=f"sync-worker-{i}",
                daemon=True,
            )
            for i in range(self._config.sync.worker_threads)
        ]
        for worker in workers:
            worker.start()

        logger.info(
            "Agent running with %d worker(s).  Press Ctrl+C to stop.",
            self._config.sync.worker_threads,
        )

        try:
            self._stop_event.wait()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received.")
        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the agent to stop.  Returns immediately; shutdown is async."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _shutdown(self) -> None:
        if not self._stop_event.is_set():
            self._stop_event.set()
        logger.info("Shutting down agent…")
        self._watcher.stop()
        logger.info("Agent stopped.")

    def _worker(self) -> None:
        """Event-processing loop executed by each worker thread."""
        while not self._stop_event.is_set():
            event = self._event_queue.get(timeout=1.0)
            if event is None:
                continue
            try:
                if event.event_type in (EventType.CREATED, EventType.MODIFIED):
                    self._sync_engine.on_created_or_modified(event.src_path)
                elif event.event_type == EventType.DELETED:
                    self._sync_engine.on_deleted(event.src_path)
                elif event.event_type == EventType.MOVED:
                    if event.dest_path is not None:
                        self._sync_engine.on_moved(
                            event.src_path, event.dest_path
                        )
                    else:
                        logger.warning(
                            "MOVED event missing dest_path for: %s",
                            event.src_path,
                        )
            except Exception as exc:
                logger.error(
                    "Unhandled error processing %s event for %s: %s",
                    event.event_type.name,
                    event.src_path,
                    exc,
                    exc_info=True,
                )
            finally:
                self._event_queue.task_done()
