"""DJ-23: ``RecordingController._stop_watchdog_thread`` must reap (join)
and null the watchdog thread, mirroring ``_stop_mic_level_worker``
(level_monitor.py:376-384).

Pre-fix, ``_stop_watchdog_thread`` only signaled the thread via the
stop event — no ``join()``, no ``self._watchdog_thread = None``. The
dead ``Thread`` object stayed referenced on ``self._watchdog_thread``
until the next ``_start_watchdog_thread`` overwrote it (which can be
hours apart in a long-running tray app). The asymmetry with
``_stop_mic_level_worker`` (which DOES join + null) suggested this was
an oversight.

The fix mirrors ``_stop_mic_level_worker``:
    t = self._watchdog_thread
    if t is not None and t is not threading.current_thread():
        with contextlib.suppress(Exception):
            t.join(timeout=1.0)
    self._watchdog_thread = None

The ``current_thread()`` guard prevents a self-join deadlock: the
watchdog thread calls ``_stop_watchdog_thread`` via
``_force_recover_from_stuck_transcription`` from inside its own loop.
"""

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
    """DJ-23: ``_stop_watchdog_thread`` joins the thread (best-effort)
    and sets ``_watchdog_thread = None``.

    Pre-fix, the thread reference persisted after stop() — this test
    pins the post-fix contract that the reference is cleared.
    """
    ctrl = _make_controller_for_watchdog()

    # Start a real thread that waits on the stop event so we can
    # verify the join + null path.
    def _loop():
        while not ctrl._watchdog_stop_event.is_set():
            ctrl._watchdog_event.wait(timeout=0.05)

    t = threading.Thread(target=_loop, name="TestWatchdog", daemon=True)
    t.start()
    ctrl._watchdog_thread = t
    assert ctrl._watchdog_thread is t

    ctrl._stop_watchdog_thread()

    # DJ-23 contract: thread reference MUST be None after stop.
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
    """DJ-23: ``_stop_watchdog_thread`` is a no-op when the thread is
    already None (e.g. called twice in a row, or called before any
    ``_start_watchdog_thread``).
    """
    ctrl = _make_controller_for_watchdog()
    ctrl._watchdog_thread = None

    # Must not raise.
    ctrl._stop_watchdog_thread()
    assert ctrl._watchdog_thread is None


def test_stop_watchdog_thread_skips_self_join():
    """DJ-23: when called from the watchdog thread itself (the
    force-recover path), ``_stop_watchdog_thread`` MUST NOT join
    itself (would deadlock). The ``current_thread()`` guard skips the
    join but still nulls the reference.

    The watchdog thread calls ``_stop_watchdog_thread`` via
    ``_force_recover_from_stuck_transcription`` from inside its own
    loop — joining ourselves would block forever.
    """
    ctrl = _make_controller_for_watchdog()

    # Start a thread that calls _stop_watchdog_thread on itself.
    def _self_stop_loop():
        ctrl._watchdog_thread = threading.current_thread()
        ctrl._stop_watchdog_thread()

    t = threading.Thread(target=_self_stop_loop, name="SelfStopWatchdog", daemon=True)
    t.start()
    # Wait for the thread to finish — if the self-join deadlocked,
    # this join would time out and the assertion below would fail.
    t.join(timeout=2.0)
    assert not t.is_alive(), (
        "DJ-23: _stop_watchdog_thread must NOT self-join (would "
        "deadlock). The current_thread() guard must skip the join."
    )
    # The thread reference must still be nulled.
    assert ctrl._watchdog_thread is None, "DJ-23: even when skipping self-join, the thread reference must be nulled."


def test_stop_watchdog_thread_join_is_bounded():
    """DJ-23 supplemental: the join is bounded by timeout=1.0 so a
    hung watchdog thread doesn't block the caller indefinitely.

    We start a thread that NEVER checks the stop event (simulating a
    hung thread) and verify that ``_stop_watchdog_thread`` returns
    within ~1.5s (the join timeout + a small margin).
    """
    ctrl = _make_controller_for_watchdog()

    stop_flag = threading.Event()

    def _hung_loop():
        # Never checks _watchdog_stop_event — simulates a hung thread.
        stop_flag.wait(timeout=5.0)

    t = threading.Thread(target=_hung_loop, name="HungWatchdog", daemon=True)
    t.start()
    ctrl._watchdog_thread = t

    start = time.monotonic()
    ctrl._stop_watchdog_thread()
    elapsed = time.monotonic() - start

    # Must return well under 5s (the join timeout is 1.0s + small margin).
    assert elapsed < 3.0, (
        f"DJ-23: _stop_watchdog_thread must bound the join at timeout=1.0s; "
        f"took {elapsed:.2f}s (a hung thread would block the caller "
        f"indefinitely without the timeout)."
    )
    # The reference must still be nulled even though the join timed out.
    assert ctrl._watchdog_thread is None
    # Clean up: signal the hung thread to exit.
    stop_flag.set()
    t.join(timeout=1.0)
