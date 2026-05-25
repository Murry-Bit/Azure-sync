"""
Tests for src/event_queue.py — pure threading/timing tests, no I/O.
"""

from __future__ import annotations

import time
from pathlib import Path

from src.event_queue import DebouncedEventQueue, EventType, FileEvent


# ---------------------------------------------------------------------------
# Basic delivery
# ---------------------------------------------------------------------------


def test_single_event_is_delivered():
    q = DebouncedEventQueue(debounce_seconds=0.05)
    event = FileEvent(EventType.CREATED, Path("/tmp/file.txt"))
    q.put(event)

    received = q.get(timeout=1.0)

    assert received is not None
    assert received.event_type == EventType.CREATED
    assert received.src_path == Path("/tmp/file.txt")


def test_no_spurious_events():
    """Queue should be empty when nothing has been put in."""
    q = DebouncedEventQueue(debounce_seconds=0.05)
    assert q.get(timeout=0.1) is None


# ---------------------------------------------------------------------------
# Debounce behaviour
# ---------------------------------------------------------------------------


def test_debounce_collapses_rapid_events():
    """Multiple rapid events for the same path should produce exactly one delivery."""
    q = DebouncedEventQueue(debounce_seconds=0.1)
    path = Path("/tmp/active.txt")

    for _ in range(10):
        q.put(FileEvent(EventType.MODIFIED, path))

    time.sleep(0.3)  # Let the debounce timer fire.

    first = q.get(timeout=0.5)
    assert first is not None, "Expected exactly one event"

    second = q.get(timeout=0.15)
    assert second is None, "Expected no further events after debounce"


def test_debounce_preserves_latest_event_type():
    """When events are coalesced, the *last* one wins."""
    q = DebouncedEventQueue(debounce_seconds=0.1)
    path = Path("/tmp/changing.txt")

    q.put(FileEvent(EventType.CREATED, path))
    q.put(FileEvent(EventType.MODIFIED, path))  # Overwrites the first

    time.sleep(0.3)

    received = q.get(timeout=0.5)
    assert received is not None
    assert received.event_type == EventType.MODIFIED


def test_independent_paths_each_deliver_one_event():
    """Events for *different* paths are independent and each delivered once."""
    q = DebouncedEventQueue(debounce_seconds=0.05)
    path_a = Path("/tmp/a.txt")
    path_b = Path("/tmp/b.txt")

    q.put(FileEvent(EventType.CREATED, path_a))
    q.put(FileEvent(EventType.CREATED, path_b))

    time.sleep(0.3)

    paths_received: set[Path] = set()
    for _ in range(2):
        evt = q.get(timeout=0.5)
        assert evt is not None
        paths_received.add(evt.src_path)

    assert paths_received == {path_a, path_b}
    assert q.get(timeout=0.1) is None


# ---------------------------------------------------------------------------
# MOVED event attributes
# ---------------------------------------------------------------------------


def test_moved_event_carries_dest_path():
    q = DebouncedEventQueue(debounce_seconds=0.05)
    src = Path("/tmp/old.txt")
    dest = Path("/tmp/new.txt")

    q.put(FileEvent(EventType.MOVED, src, dest))

    received = q.get(timeout=1.0)

    assert received is not None
    assert received.event_type == EventType.MOVED
    assert received.src_path == src
    assert received.dest_path == dest


# ---------------------------------------------------------------------------
# task_done
# ---------------------------------------------------------------------------


def test_task_done_does_not_raise():
    """task_done() should not raise even if called immediately after get()."""
    q = DebouncedEventQueue(debounce_seconds=0.05)
    q.put(FileEvent(EventType.DELETED, Path("/tmp/gone.txt")))

    evt = q.get(timeout=1.0)
    assert evt is not None
    q.task_done()  # Must not raise
