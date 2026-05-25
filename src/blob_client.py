"""
Azure Blob Storage client wrapper with automatic retries.

All upload and delete operations retry on transient Azure errors using
exponential back-off (via *tenacity*).  The SHA-256 hash of each uploaded
file is stored as blob metadata under the key ``sha256`` so that the sync
engine can detect changes without fetching file content.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from azure.core.exceptions import (
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import AppConfig
from src.integrity import compute_sha256

logger = logging.getLogger(__name__)

_HASH_METADATA_KEY = "sha256"

# Transient exceptions worth retrying.
_RETRYABLE_EXCEPTIONS = (
    ServiceRequestError,   # Network-level failures (DNS, timeout, connection reset)
    ServiceResponseError,  # Incomplete responses
)


class BlobStorageClient:
    """Thread-safe wrapper around :class:`azure.storage.blob.BlobServiceClient`.

    Supports two authentication modes:

    1. **SAS token** (customer mode) — when ``config.azure.sas_token`` is set,
       the token is appended to the account URL.  No Azure AD login required.
    2. **DefaultAzureCredential** (org/admin mode) — interactive browser login
       on first use; token is cached for subsequent runs.

    The container is created automatically on first use if it does not exist
    and the identity has sufficient permissions.

    Args:
        config:         Application configuration.
        container_name: Exact container name to use (must already conform to
                        Azure naming rules).
    """

    def __init__(self, config: AppConfig, container_name: str) -> None:
        self._max_retries = config.sync.max_retries
        self._backoff_base = config.sync.retry_backoff_base
        self._container_name = container_name

        if config.azure.sas_token:
            # Customer mode: SAS token auth — no Azure AD login needed.
            logger.info("Authenticating with SAS token (customer mode).")
            service = BlobServiceClient(
                account_url=config.azure.account_url,
                credential=config.azure.sas_token,
            )
        else:
            # Org/admin mode: Azure AD via DefaultAzureCredential.
            logger.info("Authenticating with DefaultAzureCredential (admin mode).")
            credential = DefaultAzureCredential()
            service = BlobServiceClient(
                account_url=config.azure.account_url,
                credential=credential,
            )

        self._container = service.get_container_client(container_name)
        self._ensure_container()

    # ------------------------------------------------------------------
    # Container management
    # ------------------------------------------------------------------

    def _ensure_container(self) -> None:
        """Create the container if it does not already exist.

        If the authenticated identity lacks account-level permission to create
        containers (e.g. RBAC scoped to a single container), the 403 is logged
        as a warning and we proceed — the container must already exist.
        """
        try:
            self._container.create_container()
            logger.info("Created container: %s", self._container_name)
        except ResourceExistsError:
            logger.debug("Container already exists: %s", self._container_name)
        except HttpResponseError as exc:
            if exc.status_code == 403:
                logger.warning(
                    "No permission to create container '%s' (HTTP 403). "
                    "Assuming it already exists (container-scoped RBAC).",
                    self._container_name,
                )
            else:
                raise

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_file(self, local_path: Path, blob_name: str) -> None:
        """Upload *local_path* to Blob Storage as *blob_name*.

        The file's SHA-256 hash is computed before the upload and stored in
        blob metadata.  The upload itself is retried on transient Azure errors.

        Args:
            local_path: Absolute path to the local file.
            blob_name:  Target blob name (relative path with forward slashes).

        Raises:
            OSError: If the file cannot be read (not retried – file may be gone).
            tenacity.RetryError: After all retry attempts are exhausted.
        """
        # Compute hash before entering the retry loop.  If the file has been
        # deleted between the event and the upload we want to fail fast here
        # rather than retrying a 404.
        try:
            sha256 = compute_sha256(local_path)
        except OSError as exc:
            logger.warning(
                "File no longer accessible, skipping upload: %s — %s",
                local_path,
                exc,
            )
            return

        for attempt in Retrying(
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(
                multiplier=self._backoff_base, min=1, max=60
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                blob_client = self._container.get_blob_client(blob_name)
                with open(local_path, "rb") as data:
                    blob_client.upload_blob(
                        data,
                        overwrite=True,
                        metadata={_HASH_METADATA_KEY: sha256},
                    )
                logger.info(
                    "Uploaded: %s → %s  (sha256=%s)", local_path, blob_name, sha256
                )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_blob(self, blob_name: str) -> None:
        """Delete *blob_name* from Blob Storage.

        A 404 (blob already deleted) is treated as success.  Transient errors
        are retried with exponential back-off.

        Args:
            blob_name: The blob to delete.
        """
        for attempt in Retrying(
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(
                multiplier=self._backoff_base, min=1, max=60
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                try:
                    self._container.get_blob_client(blob_name).delete_blob()
                    logger.info("Deleted blob: %s", blob_name)
                except ResourceNotFoundError:
                    # Already deleted – not an error.
                    logger.debug(
                        "Blob not found (already deleted?): %s", blob_name
                    )

    # ------------------------------------------------------------------
    # List / query
    # ------------------------------------------------------------------

    def list_blobs(self) -> dict[str, str]:
        """Return a mapping of ``{blob_name: sha256}`` for all blobs.

        Blobs without a ``sha256`` metadata entry get an empty string.
        """
        result: dict[str, str] = {}
        for blob in self._container.list_blobs(include=["metadata"]):
            sha = (blob.metadata or {}).get(_HASH_METADATA_KEY, "")
            result[blob.name] = sha
        return result

    def delete_blobs_with_prefix(self, prefix: str) -> None:
        """Delete all blobs whose name starts with *prefix*."""
        names = [
            b.name
            for b in self._container.list_blobs(name_starts_with=prefix)
        ]
        for blob_name in names:
            self.delete_blob(blob_name)
        if names:
            logger.info("Deleted %d blob(s) under prefix: %s", len(names), prefix)

    def get_blob_hash(self, blob_name: str) -> Optional[str]:
        """Return the stored SHA-256 for *blob_name*, or ``None`` if not found."""
        try:
            props = (
                self._container.get_blob_client(blob_name).get_blob_properties()
            )
            return (props.metadata or {}).get(_HASH_METADATA_KEY)
        except ResourceNotFoundError:
            return None
