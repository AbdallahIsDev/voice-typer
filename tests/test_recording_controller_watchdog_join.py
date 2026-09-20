"""DJ-23: ``RecordingController._stop_watchdog_thread`` must reap (join)"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from voice_typer.server.recording_controller import RecordingController


def _make_controller_for_watchdog() -> RecordingController:
    """Build a RecordingController with just the watchdog-related fields."""
    ctrl = RecordingController.__new__(RecordingController)
    ctrl._app = MagicMock()
    ctrl._watchdog_event = threading.Event()
    ctrl._watchdog_stop_event = threading.Event()
    ctrl._watchdog_thread = None
    ctrl._watchdog_lock = threading.Lock()
    ctrl._watchdog_firings = 0
    ctrl._watchdog_max_firings = 3
    return ctrl


def test_stop_watchdog_thread_joins_and_nulls():
    """DJ-23: ``_stop_watchdog_thread`` joins the thread (best-effort)"""
    ctrl = _make_controller_for_watchdog()

    # Start a real thread that waits on the stop event so we can
    def _loop():
        while not ctrl._watchdog_stop_event.is_set():
            ctrl._watchdog_event.wait(timeout=0.05)

    t = threading.Thread(target=_loop, name="TestWatchdog", daemon=True)
    t.start()
    ctrl._watchdog_thread = t
    assert ctrl._watchdog_thread is t

    ctrl._stop_watchdog_thread()

    assert ctrl._watchdog_thread is None, (
        "DJ-23: _stop_watchdog_thread must null the thread reference. "
        "Pre-fix, the dead Thread object stayed referenced on "
        "self._watchdog_thread until the next _start_watchdog_thread."
    )
    # The thread must have actually exited (join worked).
    assert not t.is_alive(), (
        "DJ-23: _stop_watchdog_thread must join the thread (best-effort "
        "with timeout=1.0). The thread was still alive after stop()."
    )


def test_stop_watchdog_thread_safe_when_thread_is_none():
    """DJ-23: ``_stop_watchdog_thread`` is a no-op when the thread is"""
    ctrl = _make_controller_for_watchdog()
    ctrl._watchdog_thread = None

    # Must not raise.
    ctrl._stop_watchdog_thread()
    assert ctrl._watchdog_thread is None


def test_stop_watchdog_thread_skips_self_join():
    """force-recover path), ``_stop_watchdog_thread`` MUST NOT join"""
    ctrl = _make_controller_for_watchdog()

    # Start a thread that calls _stop_watchdog_thread on itself.
    def _self_stop_loop():
        ctrl._watchdog_thread = threading.current_thread()
        ctrl._stop_watchdog_thread()

    t = threading.Thread(target=_self_stop_loop, name="SelfStopWatchdog", daemon=True)
    t.start()
    # Wait for the thread to finish, if the self-join deadlocked,
    t.join(timeout=2.0)
    assert not t.is_alive(), (
        "DJ-23: _stop_watchdog_thread must NOT self-join (would "
        "deadlock). The current_thread() guard must skip the join."
    )
    # The thread reference must still be nulled.
    assert ctrl._watchdog_thread is None, "DJ-23: even when skipping self-join, the thread reference must be nulled."


def test_stop_watchdog_thread_join_is_bounded():
    """hung watchdog thread doesn't block the caller indefinitely."""
    ctrl = _make_controller_for_watchdog()

    # Simulated hung duration. The elapsed budget below is a ratio of
    # this value so it scales instead of flaking on slow runners.
    hung_s = 5.0
    stop_flag = threading.Event()

    def _hung_loop():
        # Never checks _watchdog_stop_event, simulates a hung thread.
        stop_flag.wait(timeout=hung_s)

    t = threading.Thread(target=_hung_loop, name="HungWatchdog", daemon=True)
    t.start()
    ctrl._watchdog_thread = t

    start = time.monotonic()
    ctrl._stop_watchdog_thread()
    elapsed = time.monotonic() - start

    # Must return well under the hung duration (the join timeout is
    # 1.0s + small margin), expressed as a ratio of the simulation.
    assert elapsed < 0.6 * hung_s, (
        f"DJ-23: _stop_watchdog_thread must bound the join at timeout=1.0s; "
        f"took {elapsed:.2f}s (a hung thread would block the caller "
        f"indefinitely without the timeout)."
    )
    # The reference must NOT be nulled when the thread is still alive
    assert ctrl._watchdog_thread is t, (
        "_stop_watchdog_thread must NOT null the thread reference when the "
        "thread is still alive (zombie leak mitigation, keep the reference "
        "so _start_watchdog_thread's is_alive() guard prevents a duplicate "
        "spawn)."
    )
    assert ctrl._watchdog_stop_event.is_set(), (
        "stop event must be left SET when the thread is still alive so the zombie exits on its next iteration boundary."
    )
    # Clean up: signal the hung thread to exit.
    stop_flag.set()
    t.join(timeout=1.0)
