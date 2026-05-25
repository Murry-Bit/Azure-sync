"""
Tests for src/sync_engine.py — all Azure calls are mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from src.config import AppConfig, AzureConfig, LoggingConfig, SyncConfig
from src.integrity import compute_sha256
from src.sync_engine import SyncEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def watch_folder(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def config(watch_folder: Path) -> AppConfig:
    return AppConfig(
        watch_folder=watch_folder,
        azure=AzureConfig(account_url="https://fake.blob.core.windows.net"),
        sync=SyncConfig(),
        logging=LoggingConfig(),
    )


@pytest.fixture()
def blob_client() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def engine(config: AppConfig, blob_client: MagicMock) -> SyncEngine:
    return SyncEngine(config, blob_client)


# ---------------------------------------------------------------------------
# Path mapping
# ---------------------------------------------------------------------------


def test_to_blob_name_simple(engine: SyncEngine, watch_folder: Path):
    local = watch_folder / "report.pdf"
    assert engine._to_blob_name(local) == "report.pdf"


def test_to_blob_name_nested(engine: SyncEngine, watch_folder: Path):
    local = watch_folder / "docs" / "2024" / "report.pdf"
    assert engine._to_blob_name(local) == "docs/2024/report.pdf"


# ---------------------------------------------------------------------------
# initial_sync
# ---------------------------------------------------------------------------


def test_initial_sync_uploads_new_files(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    """Files present locally but not in cloud are uploaded."""
    (watch_folder / "a.txt").write_text("hello")
    blob_client.list_blobs.return_value = {}  # Empty cloud

    engine.initial_sync()

    blob_client.upload_file.assert_called_once_with(watch_folder / "a.txt", "a.txt")


def test_initial_sync_skips_unchanged_files(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    """Files whose hash matches the stored blob hash are not re-uploaded."""
    f = watch_folder / "a.txt"
    f.write_text("hello")
    sha = compute_sha256(f)
    blob_client.list_blobs.return_value = {"a.txt": sha}

    engine.initial_sync()

    blob_client.upload_file.assert_not_called()


def test_initial_sync_uploads_changed_files(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    """Files whose hash differs from the cloud are re-uploaded."""
    f = watch_folder / "a.txt"
    f.write_text("new content")
    blob_client.list_blobs.return_value = {"a.txt": "stale-hash"}

    engine.initial_sync()

    blob_client.upload_file.assert_called_once()


def test_initial_sync_deletes_orphaned_blobs(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    """Blobs with no corresponding local file are deleted."""
    blob_client.list_blobs.return_value = {"orphan.txt": "abc123"}

    engine.initial_sync()

    blob_client.delete_blob.assert_called_once_with("orphan.txt")


def test_initial_sync_multiple_files(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    """Mix of new, changed, unchanged, and orphaned blobs."""
    new_file = watch_folder / "new.txt"
    new_file.write_text("new")

    unchanged = watch_folder / "same.txt"
    unchanged.write_text("same")
    same_sha = compute_sha256(unchanged)

    changed = watch_folder / "changed.txt"
    changed.write_text("updated")

    blob_client.list_blobs.return_value = {
        "same.txt": same_sha,
        "changed.txt": "old-hash",
        "orphan.txt": "orphan-hash",
    }

    engine.initial_sync()

    # new.txt and changed.txt uploaded; same.txt skipped.
    assert blob_client.upload_file.call_count == 2
    uploaded_names = {c.args[1] for c in blob_client.upload_file.call_args_list}
    assert uploaded_names == {"new.txt", "changed.txt"}

    blob_client.delete_blob.assert_called_once_with("orphan.txt")


# ---------------------------------------------------------------------------
# on_created_or_modified
# ---------------------------------------------------------------------------


def test_on_created_uploads_when_blob_absent(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    f = watch_folder / "file.txt"
    f.write_text("data")
    blob_client.get_blob_hash.return_value = None

    engine.on_created_or_modified(f)

    blob_client.upload_file.assert_called_once_with(f, "file.txt")


def test_on_modified_skips_when_hash_matches(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    f = watch_folder / "file.txt"
    f.write_text("data")
    sha = compute_sha256(f)
    blob_client.get_blob_hash.return_value = sha

    engine.on_created_or_modified(f)

    blob_client.upload_file.assert_not_called()


def test_on_created_skips_directories(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    subdir = watch_folder / "subdir"
    subdir.mkdir()

    engine.on_created_or_modified(subdir)

    blob_client.upload_file.assert_not_called()


# ---------------------------------------------------------------------------
# on_deleted
# ---------------------------------------------------------------------------


def test_on_deleted_deletes_blob(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    local = watch_folder / "goodbye.txt"
    engine.on_deleted(local)
    blob_client.delete_blob.assert_called_once_with("goodbye.txt")


# ---------------------------------------------------------------------------
# on_moved
# ---------------------------------------------------------------------------


def test_on_moved_deletes_old_and_uploads_new(
    engine: SyncEngine, watch_folder: Path, blob_client: MagicMock
):
    src = watch_folder / "old.txt"
    dest = watch_folder / "new.txt"
    dest.write_text("renamed content")

    blob_client.get_blob_hash.return_value = None

    engine.on_moved(src, dest)

    blob_client.delete_blob.assert_called_once_with("old.txt")
    blob_client.upload_file.assert_called_once_with(dest, "new.txt")
