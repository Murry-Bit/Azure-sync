"""
Tests for src/integrity.py — pure unit tests, no Azure connection required.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from src.integrity import compute_sha256, verify_integrity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_temp(content: bytes) -> Path:
    """Write *content* to a temporary file and return the path."""
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# compute_sha256
# ---------------------------------------------------------------------------


def test_compute_sha256_known_value():
    content = b"hello world"
    expected = hashlib.sha256(content).hexdigest()
    p = _write_temp(content)
    try:
        assert compute_sha256(p) == expected
    finally:
        p.unlink()


def test_compute_sha256_empty_file():
    p = _write_temp(b"")
    try:
        assert compute_sha256(p) == hashlib.sha256(b"").hexdigest()
    finally:
        p.unlink()


def test_compute_sha256_large_file(tmp_path: Path):
    """Checks that the chunked reader produces a correct hash for a >1 MB file."""
    content = b"x" * (2 * 1024 * 1024)  # 2 MB
    expected = hashlib.sha256(content).hexdigest()
    p = tmp_path / "large.bin"
    p.write_bytes(content)
    assert compute_sha256(p) == expected


def test_compute_sha256_raises_on_missing_file(tmp_path: Path):
    missing = tmp_path / "does_not_exist.txt"
    with pytest.raises(OSError):
        compute_sha256(missing)


# ---------------------------------------------------------------------------
# verify_integrity
# ---------------------------------------------------------------------------


def test_verify_integrity_match():
    content = b"integrity test"
    expected = hashlib.sha256(content).hexdigest()
    p = _write_temp(content)
    try:
        assert verify_integrity(p, expected) is True
    finally:
        p.unlink()


def test_verify_integrity_mismatch():
    content = b"integrity test"
    p = _write_temp(content)
    try:
        assert verify_integrity(p, "0" * 64) is False
    finally:
        p.unlink()


def test_verify_integrity_different_content():
    """Two files with different content must not match each other's hash."""
    p1 = _write_temp(b"file one")
    p2 = _write_temp(b"file two")
    hash1 = compute_sha256(p1)
    try:
        assert verify_integrity(p2, hash1) is False
    finally:
        p1.unlink()
        p2.unlink()
