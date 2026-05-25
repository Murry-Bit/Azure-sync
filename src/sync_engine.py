"""
Sync engine: converts file-system events into Blob Storage operations.

Responsibilities:
- Map local absolute paths to blob names (relative, forward-slash separated).
- Perform an initial full sync at startup (diff-based: only upload changed files).
- Handle incremental create / modify / delete / move events.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.blob_client import BlobStorageClient
from src.config import AppConfig
from src.integrity import compute_sha256

logger = logging.getLogger(__name__)


class SyncEngine:
    """Coordinates uploads and deletes between a local folder and Blob Storage.

    Args:
        config:      Application configuration (provides ``watch_folder``).
        blob_client: Pre-initialised :class:`BlobStorageClient`.
    """

    def __init__(self, config: AppConfig, blob_client: BlobStorageClient) -> None:
        self._watch_folder = config.watch_folder
        self._blob = blob_client

    # ------------------------------------------------------------------
    # Path mapping
    # ------------------------------------------------------------------

    def _to_blob_name(self, local_path: Path) -> str:
        """Convert an absolute local path to a relative blob name.

        Examples::

            /watch/docs/report.pdf  →  docs/report.pdf
            /watch/file.txt         →  file.txt
        """
        return local_path.relative_to(self._watch_folder).as_posix()

    # ------------------------------------------------------------------
    # Initial full sync
    # ------------------------------------------------------------------

    def initial_sync(self) -> None:
        """Bring Blob Storage in sync with the local watch folder.

        Algorithm:
        1. List all blobs and their hashes.
        2. Walk the local folder; upload files that are missing or changed.
        3. Delete blobs that no longer exist locally (orphans).
        """
        logger.info("Starting initial sync from: %s", self._watch_folder)

        cloud_state: dict[str, str] = self._blob.list_blobs()
        local_files: dict[str, Path] = {}

        for local_path in self._watch_folder.rglob("*"):
            if not local_path.is_file():
                continue
            blob_name = self._to_blob_name(local_path)
            local_files[blob_name] = local_path

            cloud_hash = cloud_state.get(blob_name)
            try:
                local_hash = compute_sha256(local_path)
            except OSError as exc:
                logger.warning("Cannot read %s, skipping: %s", local_path, exc)
                continue

            if cloud_hash == local_hash:
                logger.debug("Up to date: %s", blob_name)
            else:
                logger.info("Uploading (new/changed): %s", blob_name)
                self._blob.upload_file(local_path, blob_name)

        # Remove blobs that have been deleted locally since the last run.
        for blob_name in cloud_state:
            if blob_name not in local_files:
                logger.info("Deleting orphaned blob: %s", blob_name)
                self._blob.delete_blob(blob_name)

        logger.info(
            "Initial sync complete — %d local file(s) processed.", len(local_files)
        )

    # ------------------------------------------------------------------
    # Incremental event handlers
    # ------------------------------------------------------------------

    def on_created_or_modified(self, local_path: Path) -> None:
        """Upload *local_path* if its hash differs from the stored blob hash."""
        if not local_path.is_file():
            return
        blob_name = self._to_blob_name(local_path)
        try:
            local_hash = compute_sha256(local_path)
        except OSError as exc:
            logger.warning("Cannot read %s, skipping: %s", local_path, exc)
            return

        cloud_hash = self._blob.get_blob_hash(blob_name)
        if local_hash == cloud_hash:
            logger.debug("No change (hash match): %s", blob_name)
            return

        self._blob.upload_file(local_path, blob_name)

    def on_deleted(self, local_path: Path) -> None:
        """Delete the blob for *local_path* (file) or all blobs under it (folder).

        On Windows, watchdog reports folder deletions with is_directory=False,
        so we always attempt both: delete the exact blob and all blobs under
        the path as a prefix.
        """
        blob_name = self._to_blob_name(local_path)
        self._blob.delete_blob(blob_name)
        self._blob.delete_blobs_with_prefix(blob_name + "/")

    def on_moved(self, src_path: Path, dest_path: Path) -> None:
        """Handle a rename/move as a delete of the old name + upload of the new.

        If the source is outside the watch folder (file moved *into* the folder)
        we skip the delete.  If the destination is outside (file moved *out*)
        we treat it as a plain delete.
        """
        src_inside = self._is_inside_watch_folder(src_path)
        dest_inside = self._is_inside_watch_folder(dest_path)

        if src_inside:
            self.on_deleted(src_path)
        if dest_inside:
            self.on_created_or_modified(dest_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_inside_watch_folder(self, path: Path) -> bool:
        """Return True if *path* is inside (or equal to) the watch folder."""
        try:
            path.relative_to(self._watch_folder)
            return True
        except ValueError:
            return False