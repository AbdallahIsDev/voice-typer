"""TY-25: tests for the tray-unavailable periodic drain loop."""

from __future__ import annotations

import threading
import time

from voice_typer.server.tray import TrayIcon  # noqa: E402
from voice_typer.server.tray_types import AppState  # noqa: E402


def _make_tray_unavailable_tray() -> TrayIcon:
    """Build a minimal ``TrayIcon`` configured for the tray-unavailable path."""
    tray = TrayIcon.__new__(TrayIcon)
    tray._tray_unavailable = True
    tray._icon = None
    tray._run_event = threading.Event()
    tray._state = AppState.IDLE
    tray._message = ""
    tray._pending_states = []
    tray._pending_notifications = []
    tray._queue_lock = threading.Lock()
    tray._icon_lock = threading.RLock()
    tray._elapsed_timer_helper = None  # defensive: no timer on this path
    tray._elapsed_timer = None
    tray._recording_started_at = None
    tray._cpu_fallback_active = False
    tray._config = None
    tray._notifications_enabled = True
    return tray


class TestDrainPending:
    """``_drain_pending`` clears both pending queues."""

    def test_drain_clears_pending_states(self):
        tray = _make_tray_unavailable_tray()
        tray._pending_states = [
            (AppState.IDLE, "msg1"),
            (AppState.RECORDING, "msg2"),
        ]
        tray._drain_pending()
        assert tray._pending_states == []

    def test_drain_clears_pending_notifications(self):
        tray = _make_tray_unavailable_tray()
        tray._pending_notifications = [
            ("title1", "body1"),
            ("title2", "body2"),
        ]
        tray._drain_pending()
        assert tray._pending_notifications == []

    def test_drain_is_idempotent_on_empty_queues(self):
        tray = _make_tray_unavailable_tray()
        # Queues start empty, drain should be a no-op.
        tray._drain_pending()
        assert tray._pending_states == []
        assert tray._pending_notifications == []

    def test_drain_is_thread_safe(self):
        """Concurrent appends + drain don't raise (queue lock held)."""
        tray = _make_tray_unavailable_tray()
        errors = []

        def appender():
            try:
                for i in range(100):
                    tray._pending_states.append((AppState.IDLE, f"msg{i}"))
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        t = threading.Thread(target=appender, daemon=True)
        t.start()
        for _ in range(20):
            tray._drain_pending()
            time.sleep(0.002)
        t.join()
        assert errors == []
        # After the appender finishes + a final drain, queues are empty.
        tray._drain_pending()
        assert tray._pending_states == []


class TestRunDrainLoop:
    """``run()`` drains periodically on the tray-unavailable path."""

    def test_run_calls_drain_when_wait_times_out(self, monkeypatch):
        """When ``_run_event.wait(60)`` returns False (timeout),"""
        tray = _make_tray_unavailable_tray()

        # Track drain calls.
        drain_calls = []
        original_drain = tray._drain_pending

        def tracking_drain():
            drain_calls.append(time.time())
            original_drain()

        monkeypatch.setattr(tray, "_drain_pending", tracking_drain)

        # Make _run_event.wait return False once (timeout), then True
        wait_calls = []

        def fake_wait(timeout=None):
            wait_calls.append(timeout)
            return len(wait_calls) != 1  # False first (timeout), True second (stop)

        monkeypatch.setattr(tray._run_event, "wait", fake_wait)

        tray.run()

        # Exactly one drain call after the first wait timeout.
        assert len(drain_calls) == 1, f"Expected 1 drain call after timeout, got {len(drain_calls)}"
        # The wait was called twice with timeout=60: once returns False
        assert wait_calls == [60, 60], f"Expected wait called twice with timeout=60, got {wait_calls}"

    def test_run_drains_accumulated_pending_states(self, monkeypatch):
        """Pending states accumulated during the wait are cleared by"""
        tray = _make_tray_unavailable_tray()
        # Pre-populate the queues as if set_state/notify were called
        tray._pending_states = [(AppState.RECORDING, "recording")]
        tray._pending_notifications = [("title", "body")]

        # First wait returns False (timeout → drain), second returns
        wait_calls = []

        def fake_wait(timeout=None):
            wait_calls.append(len(wait_calls))
            return wait_calls[-1] != 0  # False first, True second

        monkeypatch.setattr(tray._run_event, "wait", fake_wait)

        tray.run()

        assert tray._pending_states == [], f"Drain should have cleared _pending_states, got {tray._pending_states}"
        assert tray._pending_notifications == [], (
            f"Drain should have cleared _pending_notifications, got {tray._pending_notifications}"
        )

    def test_run_returns_promptly_on_stop(self):
        """``stop()`` sets the event so ``run()`` returns without"""
        tray = _make_tray_unavailable_tray()

        run_returned = threading.Event()

        def _run_thread():
            tray.run()
            run_returned.set()

        t = threading.Thread(target=_run_thread, daemon=True)
        t.start()
        # Give run() time to enter the _run_event.wait(60) call.
        time.sleep(0.05)
        tray.stop()
        assert run_returned.wait(timeout=2.0), (
            "run() did not return within 2s after stop(), the 60s drain "
            "interval shouldn't delay shutdown (stop() sets the event so "
            "wait() returns True immediately)."
        )

    def test_run_does_not_raise_on_tray_unavailable(self, monkeypatch):
        """The drain loop must not raise, preserves the PVT-G5-001"""
        tray = _make_tray_unavailable_tray()

        def fake_wait(timeout=None):
            return True  # immediate stop

        monkeypatch.setattr(tray._run_event, "wait", fake_wait)

        # Must not raise.
        tray.run()


class TestDrainLoopBoundedGrowth:
    """the tray-unavailable path, even when set_state/notify are called"""

    def test_queues_drained_repeatedly_across_multiple_timeouts(self, monkeypatch):
        """Multiple wait timeouts → multiple drain calls → queues stay"""
        tray = _make_tray_unavailable_tray()

        drain_calls = []
        original_drain = tray._drain_pending

        def tracking_drain():
            drain_calls.append(len(tray._pending_states))
            original_drain()

        monkeypatch.setattr(tray, "_drain_pending", tracking_drain)

        # Simulate 3 timeout cycles, then stop.
        wait_call_count = [0]

        def fake_wait(timeout=None):
            wait_call_count[0] += 1
            if wait_call_count[0] <= 3:
                # Between waits, simulate set_state/notify appends.
                tray._pending_states.append((AppState.IDLE, f"msg{wait_call_count[0]}"))
                tray._pending_notifications.append((f"t{wait_call_count[0]}", f"b{wait_call_count[0]}"))
                return False  # timeout
            return True  # stop

        monkeypatch.setattr(tray._run_event, "wait", fake_wait)

        tray.run()

        assert len(drain_calls) == 3, f"Expected 3 drain calls (one per timeout), got {len(drain_calls)}"
        # Each drain sees exactly 1 entry (the one appended between waits).
        assert drain_calls == [1, 1, 1], f"Expected each drain to see exactly 1 entry, got {drain_calls}"
        # After run() returns, queues are empty (last drain cleared them).
        assert tray._pending_states == []
        assert tray._pending_notifications == []
