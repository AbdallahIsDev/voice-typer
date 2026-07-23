"""Tests for :mod:`voice_typer.server.recording_controller` audio-callback
shutdown paths.

These tests pin the XV-134 contract: ``on_silence_auto_stop`` and
``on_max_duration_auto_stop`` MUST dispatch ``_stop_dictation`` off the
audio callback thread (which holds ``Recorder._lock``) so the stop
sequence doesn't deadlock.  Previously both call sites used
``self._app._schedule_timer(0, ...)`` which built a ``threading.Timer``
object, appended it to ``_pending_timers``, and started a Timer thread
that immediately fired — paying the Timer scheduling cost and polluting
the pending-timer list with a zero-delay entry that was never going to
be cancelled meaningfully.

XV-134 (fixed in :mod:`voice_typer.server.timer_coordinator`) makes
``_schedule_timer(0, func)`` short-circuit to a plain daemon thread via
the new ``TimerCoordinator.defer`` method.  These tests verify the
recording-controller call sites still trigger the off-thread dispatch
end-to-end — they don't re-test the TimerCoordinator internals (those
are covered by ``tests/test_timer_coordinator.py``).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest


def _make_minimal_controller():
    """Build a RecordingController with only the attributes the
    auto-stop callbacks read.

    The full ``__init__`` pulls in models, recorder, tray, etc. — none
    of which the auto-stop callbacks touch.  Using ``__new__`` keeps
    the test fast and dependency-free.
    """
    from voice_typer.server.recording_controller import RecordingController

    ctrl = RecordingController.__new__(RecordingController)
    ctrl._app = MagicMock()
    return ctrl


# ─── XV-134: silence / max-duration auto-stop dispatch ────────────────────


class TestSilenceAutoStopDispatch:
    """XV-134: ``on_silence_auto_stop`` dispatches ``_stop_dictation``
    on a background thread (not the calling thread)."""

    def test_schedules_stop_dictation_with_zero_delay(self):
        """The callback must call ``self._app._schedule_timer(0, ...)``
        so the stop sequence runs off the audio callback thread.

        XV-134 makes that call short-circuit to a daemon thread — we
        verify the call site still passes ``0`` (the contract that
        triggers the short-circuit) and that the dispatched callback
        actually runs.
        """
        ctrl = _make_minimal_controller()
        captured: list[tuple] = []

        def fake_schedule(delay, func, *args, **kwargs):
            captured.append((delay, func))

            def _run():
                func()

            t = threading.Thread(target=_run, name="test-defer", daemon=True)
            t.start()
            return t

        ctrl._app._schedule_timer.side_effect = fake_schedule
        ctrl._app.tray.notify_safety = MagicMock()

        ctrl.on_silence_auto_stop()

        # Must have scheduled with delay=0 (XV-134 short-circuit trigger).
        assert len(captured) == 1
        delay, func = captured[0]
        assert delay == 0, (
            f"XV-134: on_silence_auto_stop must schedule _stop_dictation with "
            f"delay=0 (got {delay}) so TimerCoordinator short-circuits to a "
            f"plain daemon thread."
        )
        assert func == ctrl._app._stop_dictation

    def test_stop_dictation_actually_runs_asynchronously(self):
        """XV-134: the dispatched ``_stop_dictation`` runs on a
        background thread, NOT on the calling (audio callback) thread.

        This is the deadlock-avoidance guarantee the original comment
        documents: calling ``recorder.stop()`` directly from the audio
        callback would re-acquire ``Recorder._lock`` and deadlock.
        """
        ctrl = _make_minimal_controller()
        ran_on: list[threading.Thread] = []

        # Use a real TimerCoordinator so XV-134's short-circuit path
        # actually fires.
        from voice_typer.server.timer_coordinator import TimerCoordinator

        coordinator = TimerCoordinator(app=None)

        def fake_stop_dictation():
            ran_on.append(threading.current_thread())

        ctrl._app._schedule_timer = coordinator._schedule_timer
        ctrl._app._stop_dictation = fake_stop_dictation
        ctrl._app.tray.notify_safety = MagicMock()

        ctrl.on_silence_auto_stop()

        # Wait for the background thread to run.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not ran_on:
            time.sleep(0.005)

        assert len(ran_on) == 1, (
            f"XV-134: _stop_dictation should have run on a background thread, "
            f"got {ran_on}"
        )
        assert ran_on[0] is not threading.current_thread(), (
            "XV-134: _stop_dictation must NOT run on the audio callback thread "
            "(would deadlock on Recorder._lock)"
        )
        assert ran_on[0].daemon, "deferred thread should be daemon=True"

    def test_tray_notification_fired_synchronously(self):
        """The user-facing notification fires on the calling thread
        (immediate feedback) — only the stop sequence is deferred."""
        ctrl = _make_minimal_controller()
        notify_on: list[threading.Thread] = []

        def fake_notify(*args, **kwargs):
            notify_on.append(threading.current_thread())

        ctrl._app._schedule_timer = lambda delay, func, *a, **kw: None
        ctrl._app.tray.notify_safety.side_effect = fake_notify

        ctrl.on_silence_auto_stop()

        assert len(notify_on) == 1
        assert notify_on[0] is threading.current_thread(), (
            "on_silence_auto_stop should notify on the calling thread for "
            "immediate user feedback"
        )


class TestMaxDurationAutoStopDispatch:
    """XV-134: ``on_max_duration_auto_stop`` dispatches ``_stop_dictation``
    on a background thread (mirrors the silence-auto-stop path)."""

    def test_schedules_stop_dictation_with_zero_delay(self):
        ctrl = _make_minimal_controller()
        captured: list[tuple] = []

        def fake_schedule(delay, func, *args, **kwargs):
            captured.append((delay, func))
            return MagicMock()

        ctrl._app._schedule_timer.side_effect = fake_schedule
        ctrl._app.tray.notify_safety = MagicMock()

        ctrl.on_max_duration_auto_stop()

        assert len(captured) == 1
        delay, func = captured[0]
        assert delay == 0, (
            f"XV-134: on_max_duration_auto_stop must schedule _stop_dictation "
            f"with delay=0 (got {delay}) so TimerCoordinator short-circuits."
        )
        assert func == ctrl._app._stop_dictation

    def test_stop_dictation_runs_asynchronously(self):
        ctrl = _make_minimal_controller()
        ran_on: list[threading.Thread] = []

        from voice_typer.server.timer_coordinator import TimerCoordinator

        coordinator = TimerCoordinator(app=None)

        def fake_stop_dictation():
            ran_on.append(threading.current_thread())

        ctrl._app._schedule_timer = coordinator._schedule_timer
        ctrl._app._stop_dictation = fake_stop_dictation
        ctrl._app.tray.notify_safety = MagicMock()

        ctrl.on_max_duration_auto_stop()

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not ran_on:
            time.sleep(0.005)

        assert len(ran_on) == 1
        assert ran_on[0] is not threading.current_thread()
        assert ran_on[0].daemon
