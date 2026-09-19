"""XV-7 / XV-8 / XV-10: ShutdownController parallel-teardown + timeout fallbacks."""

from __future__ import annotations

import os
import sys
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    """No-op override of the conftest autouse fixture."""
    yield


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``."""

    def __init__(self) -> None:
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._host_pid: int | None = None
        self._mutex_handle = None

        self.recorder = MagicMock()
        self.recorder.recording = False  # skip recorder.stop() branch by default
        self.recording = MagicMock()
        self.recording._transcription_thread = None
        self.hotkeys = MagicMock()
        self.hotkeys._hotkey_backend = MagicMock()
        self.hotkeys._esc_backend = MagicMock()
        self.hotkeys._repaste_backend = MagicMock()
        self.history_db = MagicMock()
        self._crash_recovery = MagicMock()
        self.tray = MagicMock()
        self._thread_registry = MagicMock()
        self.waveform_wiring = MagicMock()

        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()


@pytest.fixture
def fake_app(monkeypatch):
    """Return a ``_FakeApp`` with all dynamic-lookup helpers stubbed."""
    fake_app_module = MagicMock()
    fake_app_module._clear_backend_pid_file = MagicMock()
    fake_app_module._close_devnull_files = MagicMock()
    fake_app_module._register_devnull_file = MagicMock()
    fake_app_module.is_windows = lambda: False
    fake_app_module._config_dir = lambda: "/tmp/voice-typer-test-xv7"
    monkeypatch.setitem(sys.modules, "voice_typer.server.app", fake_app_module)

    # The PID-file teardown resolves ``_clear_backend_pid_file`` through
    fake_backend_pid = MagicMock()
    fake_backend_pid._clear_backend_pid_file = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.backend_pid", fake_backend_pid)

    fake_event_bus = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)
    # ``from voice_typer.server import event_bus`` (inside the teardown
    import voice_typer.server as _server_pkg

    monkeypatch.setattr(_server_pkg, "event_bus", fake_event_bus, raising=False)

    fake_level_monitor = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.level_monitor", fake_level_monitor)

    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app``."""
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


class TestParallelTeardownBatch:
    """``ThreadPoolExecutor`` with a shared 10 s deadline. The bookends"""

    def test_do_cleanup_invokes_all_teardown_helpers(self, controller, fake_app):
        """Every XV-7 teardown helper must be wired into the parallel"""
        # All 14 helpers must be callable attributes on the controller.
        helper_names = [
            "_teardown_timers_and_recording",
            "_teardown_recorder",
            "_teardown_level_monitor",
            "_teardown_restore_volume",
            "_teardown_hotkeys",
            "_teardown_crash_recovery",
            "_teardown_history_db",
            "_teardown_waveform_wiring",
            "_teardown_sounddevice",
            "_teardown_host_child",
            "_teardown_pid_file",
            "_teardown_mutex_handle",
            "_teardown_devnull_files",
            "_teardown_event_bus",
        ]
        for name in helper_names:
            assert hasattr(controller, name), f"missing teardown helper: {name}"
            assert callable(getattr(controller, name)), f"{name} is not callable"

        controller._do_cleanup()

        fake_app.tray.stop.assert_called_once_with()

    def test_subsystem_teardowns_run_concurrently(self, controller, fake_app):
        """XV-7: independent teardowns must run CONCURRENTLY, not"""
        import time as _time

        windows: dict[str, tuple[float, float]] = {}

        def _make_slow_teardown(name: str):
            def _slow_teardown(*args, **kwargs):
                t0 = _time.monotonic()
                _time.sleep(0.3)
                windows[name] = (t0, _time.monotonic())

            return _slow_teardown

        # Patch the bound methods on the controller (NOT on fake_app —
        controller._teardown_hotkeys = MagicMock(side_effect=_make_slow_teardown("hotkeys"))
        controller._teardown_host_child = MagicMock(side_effect=_make_slow_teardown("host_child"))

        controller._do_cleanup()

        # Both instrumented helpers must have run.
        assert set(windows) == {"hotkeys", "host_child"}, (
            f"XV-7: expected both instrumented teardowns to run; got windows for {sorted(windows)}."
        )

        # THE concurrency proof: the two execution WINDOWS intersect.
        h0, h1 = windows["hotkeys"]
        e0, e1 = windows["host_child"]
        overlap = min(h1, e1) - max(h0, e0)
        assert overlap > 0.15, (
            f"XV-7: teardown helpers did not run concurrently, window "
            f"overlap {overlap:.3f}s (hotkeys={windows['hotkeys']}, "
            f"host_child={windows['host_child']}). Two 0.3s helpers that "
            f"overlap prove the ThreadPoolExecutor batch is concurrent; "
            f"sequential execution would show overlap <= 0."
        )

    def test_teardown_failure_does_not_propagate(self, controller, fake_app):
        """the orchestrator logs the exception at DEBUG level. Other"""
        controller._teardown_history_db = MagicMock(side_effect=RuntimeError("boom"))

        # _do_cleanup must not raise, the Future.exception() is logged
        controller._do_cleanup()

        fake_app.tray.stop.assert_called_once_with()

    def test_tray_stop_runs_after_parallel_batch(self, controller, fake_app, monkeypatch):
        """XV-7 / PVT-G5-003: ``tray.stop()`` is the late bookend, it"""
        call_order: list[str] = []

        # Spy on event_bus.shutdown, patch the already-injected
        fake_event_bus = sys.modules["voice_typer.server.event_bus"]

        def _spy_eb_shutdown():
            call_order.append("event_bus.shutdown")

        fake_event_bus.shutdown = _spy_eb_shutdown

        # Spy on tray.stop.
        original_tray_stop = fake_app.tray.stop

        def _spy_tray_stop():
            call_order.append("tray.stop")
            original_tray_stop()

        fake_app.tray.stop = _spy_tray_stop

        controller._do_cleanup()

        assert "tray.stop" in call_order, "tray.stop() must be called"
        assert "event_bus.shutdown" in call_order, "event_bus.shutdown() must be called"
        eb_idx = call_order.index("event_bus.shutdown")
        tray_idx = call_order.index("tray.stop")
        assert eb_idx < tray_idx, (
            f"XV-7: event_bus.shutdown (at {eb_idx}) must run BEFORE tray.stop (at {tray_idx}); got order: {call_order}"
        )


class TestTrayStopTimeoutFallback:
    """XV-10: if ``tray.stop()`` times out AND we're on a non-main"""

    def test_os_exit_called_when_tray_stop_times_out_on_non_main_thread(self, controller, fake_app, monkeypatch):
        """current thread is NOT the main thread, ``_do_cleanup`` must"""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        # Make tray.stop block past the 5 s timeout. We use a real
        import voice_typer.server.shutdown_controller as _sc
        import voice_typer.server.shutdown_controller._plans as _sc_plans

        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description == "tray.stop":
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc_plans, "_run_with_timeout", _fast_run_with_timeout)

        blocked = threading.Event()

        def _blocking_tray_stop():
            # Block past the 0.1 s timeout.
            blocked.wait(timeout=5.0)

        fake_app.tray.stop = _blocking_tray_stop

        # Run _do_cleanup on a NON-MAIN thread so the os._exit path
        done = threading.Event()
        error_holder: list = []

        def _run_cleanup():
            try:
                controller._do_cleanup()
            except Exception as exc:
                error_holder.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=_run_cleanup, name="test-cleanup-thread")
        t.start()
        # Wait for the thread to finish (or for os._exit to be called,
        done.wait(timeout=5.0)
        # Unblock the tray.stop worker thread so it doesn't linger.
        blocked.set()

        assert exit_calls == [0], (
            f"XV-10: _do_cleanup must call os._exit(0) when tray.stop "
            f"times out on a non-main thread; got exit_calls={exit_calls}"
        )

    def test_no_os_exit_when_tray_stop_completes_on_main_thread(self, controller, fake_app, monkeypatch):
        """the main thread, ``_do_cleanup`` must NOT call ``os._exit``."""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        controller._do_cleanup()

        assert exit_calls == [], (
            f"XV-10: _do_cleanup must NOT call os._exit when tray.stop "
            f"completes normally on the main thread; got exit_calls={exit_calls}"
        )
        fake_app.tray.stop.assert_called_once_with()

    def test_no_os_exit_when_tray_stop_times_out_on_main_thread(self, controller, fake_app, monkeypatch):
        """XV-10: when ``tray.stop()`` times out BUT we're on the main"""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        # Speed up the test by patching _run_with_timeout to use a 0.1 s
        import voice_typer.server.shutdown_controller as _sc
        import voice_typer.server.shutdown_controller._plans as _sc_plans

        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description == "tray.stop":
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc_plans, "_run_with_timeout", _fast_run_with_timeout)

        blocked = threading.Event()

        def _blocking_tray_stop():
            blocked.wait(timeout=5.0)

        fake_app.tray.stop = _blocking_tray_stop

        # Run on the MAIN thread (pytest's thread).
        controller._do_cleanup()
        # Unblock the worker.
        blocked.set()

        assert exit_calls == [], (
            f"XV-10: _do_cleanup must NOT call os._exit when tray.stop "
            f"times out on the MAIN thread (quit()'s sys.exit handles it); "
            f"got exit_calls={exit_calls}"
        )
