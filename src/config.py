"""
Configuration dataclasses loaded from a YAML file.

Example usage::

    config = AppConfig.from_yaml("config/config.yaml")
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_container_name(raw: str) -> str:
    """Convert an arbitrary string to a valid Azure Blob container name.

    Rules enforced:
    - Lowercase letters, digits, and hyphens only.
    - Must start and end with a letter or digit.
    - Length capped at 63 characters.
    """
    name = raw.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = name.strip("-")
    name = name[:63].rstrip("-")
    return name or "backup"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AzureConfig:
    """Azure Storage connection settings."""

    account_url: str
    """Full URL of the Storage Account, e.g. https://<account>.blob.core.windows.net"""

    container_prefix: str = "backup"
    """Prefix used when deriving the per-user container name."""

    sas_token: Optional[str] = None
    """Pre-generated SAS token for customer auth (instead of Azure AD login).

    When set, DefaultAzureCredential is NOT used.  The token should be scoped
    to the customer's container with read/write/delete/list permissions."""

    container_name: Optional[str] = None
    """Explicit container name.  When set, ``container_prefix`` and the
    Windows username derivation are skipped.  Use this when the org admin
    creates named containers and distributes matching SAS tokens."""


@dataclass
class SyncConfig:
    """Sync engine tuning parameters."""

    debounce_seconds: float = 2.0
    """How long to wait after the last event for a file before processing it."""

    max_retries: int = 5
    """Maximum upload/delete retry attempts on transient Azure errors."""

    retry_backoff_base: float = 2.0
    """Multiplier for exponential backoff between retries (seconds)."""

    worker_threads: int = 2
    """Number of parallel event-processing worker threads."""

    initial_sync_on_start: bool = True
    """Whether to perform a full diff-sync when the agent starts."""


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    """Logging level: DEBUG, INFO, WARNING, or ERROR."""

    log_dir: str = "logs"
    """Directory for rotating log files (relative to agent root or absolute)."""

    max_bytes: int = 10 * 1024 * 1024  # 10 MB
    """Maximum size of a single log file before rotation."""

    backup_count: int = 5
    """Number of rotated log files to keep."""


@dataclass
class AppConfig:
    """Root application configuration."""

    watch_folder: Path
    """Local folder to monitor and back up."""

    azure: AzureConfig
    """Azure connection settings."""

    sync: SyncConfig = field(default_factory=SyncConfig)
    """Sync engine parameters."""

    logging: LoggingConfig = field(default_factory=LoggingConfig)
    """Logging parameters."""

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> AppConfig:
        """Load and validate configuration from a YAML file."""
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        azure_raw = data.get("azure", {})
        if "account_url" not in azure_raw:
            raise ValueError("config.yaml must contain azure.account_url")
        azure_cfg = AzureConfig(
            account_url=azure_raw["account_url"],
            container_prefix=azure_raw.get("container_prefix", "backup"),
            sas_token=azure_raw.get("sas_token"),
            container_name=azure_raw.get("container_name"),
        )

        sync_raw = data.get("sync", {})
        sync_cfg = SyncConfig(**sync_raw) if sync_raw else SyncConfig()

        log_raw = data.get("logging", {})
        log_cfg = LoggingConfig(**log_raw) if log_raw else LoggingConfig()

        watch_folder_raw = data.get("watch_folder")
        if not watch_folder_raw:
            # Default to ~/Documents/Backup — works on any Windows user.
            watch_folder_raw = str(
                Path.home() / "Documents" / "Backup"
            )

        return cls(
            watch_folder=Path(watch_folder_raw),
            azure=azure_cfg,
            sync=sync_cfg,
            logging=log_cfg,
        )

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def container_name(self, username: Optional[str] = None) -> str:
        """Derive a safe Azure container name for *username*.

        Falls back to the current OS user when *username* is None.
        The result is: ``{container_prefix}-{safe_username}`` (≤ 63 chars).
        """
        if username is None:
            username = (
                os.environ.get("USERNAME")
                or os.environ.get("USER")
                or "user"
            )
        safe_user = _safe_container_name(username)
        safe_prefix = _safe_container_name(self.azure.container_prefix)
        return f"{safe_prefix}-{safe_user}"[:63].rstrip("-")
