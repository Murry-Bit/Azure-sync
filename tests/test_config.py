"""
Tests for src/config.py — validates YAML loading and container name derivation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config import AppConfig, AzureConfig, _safe_container_name


# ---------------------------------------------------------------------------
# _safe_container_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("alice", "alice"),
        ("Alice.Smith", "alice-smith"),
        ("JOHN DOE", "john-doe"),
        ("user@company.com", "user-company-com"),
        ("--bad--", "bad"),
        ("a" * 70, "a" * 63),          # Truncated to 63 chars
        ("123user", "123user"),
        ("", "backup"),                 # Falls back to "backup"
    ],
)
def test_safe_container_name(raw: str, expected: str):
    result = _safe_container_name(raw)
    assert result == expected
    # Must match Azure naming rules
    assert len(result) <= 63
    assert result == result.lower()


# ---------------------------------------------------------------------------
# AppConfig.from_yaml
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    with open(p, "w") as fh:
        yaml.dump(data, fh)
    return p


def test_from_yaml_minimal(tmp_path: Path):
    p = _write_yaml(
        tmp_path,
        {
            "watch_folder": str(tmp_path),
            "azure": {"account_url": "https://mystorage.blob.core.windows.net"},
        },
    )
    cfg = AppConfig.from_yaml(p)

    assert cfg.watch_folder == tmp_path
    assert cfg.azure.account_url == "https://mystorage.blob.core.windows.net"
    # Defaults
    assert cfg.sync.max_retries == 5
    assert cfg.sync.debounce_seconds == 2.0
    assert cfg.logging.level == "INFO"


def test_from_yaml_full(tmp_path: Path):
    p = _write_yaml(
        tmp_path,
        {
            "watch_folder": str(tmp_path),
            "azure": {
                "account_url": "https://mystorage.blob.core.windows.net",
                "container_prefix": "mybackup",
            },
            "sync": {
                "debounce_seconds": 5.0,
                "max_retries": 3,
                "retry_backoff_base": 1.5,
                "worker_threads": 4,
                "initial_sync_on_start": False,
            },
            "logging": {
                "level": "DEBUG",
                "log_dir": "my-logs",
                "max_bytes": 5242880,
                "backup_count": 3,
            },
        },
    )
    cfg = AppConfig.from_yaml(p)

    assert cfg.azure.container_prefix == "mybackup"
    assert cfg.sync.debounce_seconds == 5.0
    assert cfg.sync.max_retries == 3
    assert cfg.sync.initial_sync_on_start is False
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.backup_count == 3


def test_from_yaml_missing_account_url(tmp_path: Path):
    p = _write_yaml(tmp_path, {"watch_folder": str(tmp_path), "azure": {}})
    with pytest.raises(ValueError, match="account_url"):
        AppConfig.from_yaml(p)


def test_from_yaml_watch_folder_defaults_to_documents(tmp_path: Path):
    """When watch_folder is omitted, it defaults to ~/Documents/Backup."""
    p = _write_yaml(
        tmp_path,
        {"azure": {"account_url": "https://x.blob.core.windows.net"}},
    )
    cfg = AppConfig.from_yaml(p)
    expected = Path.home() / "Documents" / "Backup"
    assert cfg.watch_folder == expected


# ---------------------------------------------------------------------------
# AppConfig.container_name
# ---------------------------------------------------------------------------


def test_container_name_structure():
    cfg = AppConfig(
        watch_folder=Path("/tmp"),
        azure=AzureConfig(
            account_url="https://x.blob.core.windows.net",
            container_prefix="backup",
        ),
    )
    name = cfg.container_name("Alice.Smith")
    assert name == "backup-alice-smith"


def test_container_name_max_length():
    """Result must never exceed 63 characters."""
    cfg = AppConfig(
        watch_folder=Path("/tmp"),
        azure=AzureConfig(
            account_url="https://x.blob.core.windows.net",
            container_prefix="backup",
        ),
    )
    name = cfg.container_name("a" * 60)
    assert len(name) <= 63
