"""Tests for the centralized KeyboardOwnership singleton.

ARCH-ESC-001: verifies the ownership model that prevents the ESC
cancel hotkey from firing while the frontend is in hotkey capture
mode, and ensures ownership transitions are thread-safe.
"""

from __future__ import annotations

import threading

import pytest
from voice_typer.server.keyboard_ownership import (
    KeyboardOwnership,
    keyboard_ownership,
)

# Hint for xdist schedulers that respect ``xdist_group`` (loadgroup /
# loadscope): pin every test in this module — and its sibling
# ``test_keyboard_ownership_watchdog.py`` — onto a single worker. Both
# modules reset the ``KeyboardOwnership`` class-attribute singleton via
# autouse fixtures, and the singleton is process-wide state, so letting
# the two modules run on different workers of the same process pool
# (or interleaving them with other modules that touch the singleton)
# is exactly what the resets exist to defend against; the marker is
# defense-in-depth for same-worker grouping. xdist's default ``load``
# scheduler does NOT strictly honor this marker — it is a hint, not a
# correctness guarantee. No-op when xdist isn't active. (C-TEST-5.)
pytestmark = pytest.mark.xdist_group("keyboard_ownership")


@pytest.fixture(autouse=True)
def _reset_ownership():
    """Reset the singleton to "normal" between tests."""
    keyboard_ownership().reset()
    yield
    keyboard_ownership().reset()


def test_singleton_returns_same_instance() -> None:
    """KeyboardOwnership is a singleton — all calls return the same instance."""
    a = keyboard_ownership()
    b = keyboard_ownership()
    c = KeyboardOwnership()
    assert a is b is c


def test_default_owner_is_normal() -> None:
    """Freshly reset singleton has owner="normal"."""
    assert keyboard_ownership().current_owner() == "normal"


def test_set_owner_to_hotkey_capture() -> None:
    """Setting owner to hotkey_capture is reflected by is_hotkey_capture_active."""
    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="test")
    assert kb.current_owner() == "hotkey_capture"
    assert kb.is_hotkey_capture_active() is True
    assert kb.is_recording_active() is False


def test_set_owner_to_recording() -> None:
    """Setting owner to recording is reflected by is_recording_active."""
    kb = keyboard_ownership()
    kb.set_owner("recording", reason="test")
    assert kb.current_owner() == "recording"
    assert kb.is_recording_active() is True
    assert kb.is_hotkey_capture_active() is False


def test_set_owner_to_normal_resets() -> None:
    """Setting owner to normal after capture clears the capture flag."""
    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture")
    assert kb.is_hotkey_capture_active() is True
    kb.set_owner("normal")
    assert kb.is_hotkey_capture_active() is False
    assert kb.current_owner() == "normal"


def test_reset_clears_ownership() -> None:
    """reset() returns the owner to normal."""
    kb = keyboard_ownership()
    kb.set_owner("recording")
    assert kb.is_recording_active() is True
    kb.reset()
    assert kb.current_owner() == "normal"
    assert kb.is_recording_active() is False
    assert kb.is_hotkey_capture_active() is False


def test_thread_safety_concurrent_set_owner() -> None:
    """Concurrent set_owner calls from multiple threads don't corrupt state.

    The singleton uses a threading.Lock — this test runs 100 threads
    each setting ownership 100 times and verifies the final state is
    one of the valid owners (not corrupted).
    """
    kb = keyboard_ownership()
    barrier = threading.Barrier(100)

    def worker() -> None:
        barrier.wait()
        for _ in range(100):
            kb.set_owner("hotkey_capture")
            kb.set_owner("normal")
            kb.set_owner("recording")
            kb.reset()

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # After all threads complete, the owner must be a valid value.
    assert kb.current_owner() in ("normal", "hotkey_capture", "recording")


def test_ownership_priority_capture_over_recording() -> None:
    """If hotkey_capture is active, is_hotkey_capture_active returns True
    even if a recording was previously active.

    This encodes the priority: hotkey_capture > recording > normal.
    The frontend's capture mode takes precedence over the recording
    subsystem's ESC cancel.
    """
    kb = keyboard_ownership()
    kb.set_owner("recording")
    assert kb.is_recording_active() is True
    kb.set_owner("hotkey_capture")
    assert kb.is_hotkey_capture_active() is True
    # Recording is no longer the active owner.
    assert kb.is_recording_active() is False
