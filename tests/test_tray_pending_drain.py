"""TY-25: tests for the tray-unavailable periodic drain loop.

When the tray is unavailable (Wayland-without-SNI,
``VOICE_TYPER_NO_TRAY=1``, headless), ``run()`` blocks the main
thread on ``self._run_event``. Previously the block was indefinite
(``self._run_event.wait()`` with no timeout), so
``set_state()`` / ``notify()`` / ``notify_safety()`` calls (which
append to ``_pending_states`` / ``_pending_notifications`` because
``_icon`` is None) accumulated indefinitely until ``stop()`` set the
event. Growth rate: ~4-6 state changes per dictation cycle × ~150
bytes/entry.

TY-25 fixes this by replacing the indefinite wait with a periodic
drain loop: ``while not self._run_event.wait(timeout=60):
self._drain_pending()``. The drain clears the queues every 60s so
memory doesn't accumulate. The state is already published to Tauri
via ``_publish_tray_state``, so the pystray queue is redundant on the
unavailable path.

These tests verify:
  - ``_drain_pending`` clears both pending queues.
  - The drain loop in ``run()`` fires when ``_run_event.wait`` times
    out (simulated by monkeypatching ``wait`` to return False once
    then True).
  - ``stop()`` still releases the blocked ``run()`` promptly (the
    drain loop's 60s timeout doesn't delay shutdown).
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

# Mock pystray at module level so the tray module can be imported
# without needing an X display (headless CI).
_mock_pystray = MagicMock()
_mock_pystray.Menu = MagicMock
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

from voice_typer.server.tray import TrayIcon  # noqa: E402
from voice_typer.server.tray_types import AppState  # noqa: E402


def _make_tray_unavailable_tray() -> TrayIcon:
    """Build a minimal ``TrayIcon`` configured for the tray-unavailable path.

    We construct via ``__new__`` + manual attribute setup (mirroring the
    pattern in ``tests/tauri/test_tray_menu.py::_FakeTray``) so we don't
    need a real controller / config / pystray Icon. Only the attributes
    referenced by ``run()`` / ``_drain_pending`` / ``stop()`` are set.
    """
    tray = TrayIcon.__new__(TrayIcon)
    tray._tray_unavailable = True
    tray._icon = None
    tray._run_event = threading.Event()
    tray._state = AppState.IDLE
    tray._message = ""
    tray._pending_states = []
    tray._pending_notifications = []
    tray._queue_lock = threading.Lock()
    # stop() now acquires _icon_lock around the _icon teardown pair
    # so a concurrent _apply_state cannot write to a torn-down Icon.
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
        # Queues start empty — drain should be a no-op.
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
        """When ``_run_event.wait(60)`` returns False (timeout),
        ``_drain_pending`` is called before re-waiting."""
        tray = _make_tray_unavailable_tray()

        # Track drain calls.
        drain_calls = []
        original_drain = tray._drain_pending

        def tracking_drain():
            drain_calls.append(time.time())
            original_drain()

        monkeypatch.setattr(tray, "_drain_pending", tracking_drain)

        # Make _run_event.wait return False once (timeout), then True
        # (stop). This simulates one 60s timeout cycle in milliseconds.
        wait_calls = []

        def fake_wait(timeout=None):
            wait_calls.append(timeout)
            return len(wait_calls) != 1  # False first (timeout), True second (stop)

        monkeypatch.setattr(tray._run_event, "wait", fake_wait)

        tray.run()

        # Exactly one drain call after the first wait timeout.
        assert len(drain_calls) == 1, f"Expected 1 drain call after timeout, got {len(drain_calls)}"
        # The wait was called twice with timeout=60: once returns False
        # (timeout → drain), once returns True (stop → exit loop).
        assert wait_calls == [60, 60], f"Expected wait called twice with timeout=60, got {wait_calls}"

    def test_run_drains_accumulated_pending_states(self, monkeypatch):
        """Pending states accumulated during the wait are cleared by
        the drain."""
        tray = _make_tray_unavailable_tray()
        # Pre-populate the queues as if set_state/notify were called
        # while run() was blocked.
        tray._pending_states = [(AppState.RECORDING, "recording")]
        tray._pending_notifications = [("title", "body")]

        # First wait returns False (timeout → drain), second returns
        # True (stop).
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
        """``stop()`` sets the event so ``run()`` returns without
        waiting the full 60s timeout."""
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
            "run() did not return within 2s after stop() — the 60s drain "
            "interval shouldn't delay shutdown (stop() sets the event so "
            "wait() returns True immediately)."
        )

    def test_run_does_not_raise_on_tray_unavailable(self, monkeypatch):
        """The drain loop must not raise — preserves the PVT-G5-001
        contract that run() is a no-raise on the unavailable path."""
        tray = _make_tray_unavailable_tray()

        def fake_wait(timeout=None):
            return True  # immediate stop

        monkeypatch.setattr(tray._run_event, "wait", fake_wait)

        # Must not raise.
        tray.run()


class TestDrainLoopBoundedGrowth:
    """TY-25 regression: the pending queues must NOT grow unbounded on
    the tray-unavailable path, even when set_state/notify are called
    continuously while run() is blocked."""

    def test_queues_drained_repeatedly_across_multiple_timeouts(self, monkeypatch):
        """Multiple wait timeouts → multiple drain calls → queues stay
        bounded."""
        tray = _make_tray_unavailable_tray()

        drain_calls = []
        # Capture the bound original method BEFORE monkeypatching so
        # ``tracking_drain`` can call the real clear logic without
        # recursing into itself.
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
