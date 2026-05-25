"""
File-integrity utilities based on SHA-256.

The hash is stored as blob metadata so it can be retrieved cheaply
without downloading the full file content.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Read in 1 MB chunks to limit peak memory usage for large files.
_CHUNK_SIZE = 1024 * 1024


def compute_sha256(file_path: Path) -> str:
    """Return the SHA-256 hex digest of *file_path*.

    Raises:
        OSError: if the file cannot be opened or read.
    """
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            h.update(chunk)
    digest = h.hexdigest()
    logger.debug("SHA-256 %s  %s", digest, file_path)
    return digest


def verify_integrity(file_path: Path, expected_hash: str) -> bool:
    """Return ``True`` if *file_path* matches *expected_hash*.

    Logs a warning when the hashes differ so mismatches are always visible
    in the log even if the caller silently discards the return value.
    """
    actual = compute_sha256(file_path)
    if actual == expected_hash:
        return True
    logger.warning(
        "Integrity mismatch for %s — expected=%s  actual=%s",
        file_path,
        expected_hash,
        actual,
    )
    return False
