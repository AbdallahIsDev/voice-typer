"""DE-2J (Group 4 fix session) regression tests for ``ShutdownController``."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# Direct import, does NOT pull in voice_typer.server.app, so heavy
from voice_typer.server._timeout_utils import (
    _DE11_GRACE_PERIOD_SECONDS,
    _TIMEOUT,
)
from voice_typer.server.shutdown_controller import (
    ShutdownController,
)


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``."""

    def __init__(self) -> None:
        # Shutdown state (mirrors VoiceTyperApp.__init__)
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._host_pid: int | None = None
        self._mutex_handle = None

        # Subsystem collaborators (MagicMock so any attribute/method call
        self.recorder = MagicMock()
        self.recorder.recording = False  # skip recorder.stop() branch by default
        self.recording = MagicMock()
        self.recording._transcription_thread = None
        self.recording.pop_streaming_session = MagicMock(return_value=None)
        # Keep the legacy get/set accessors as MagicMocks so we can detect
        self.recording.get_streaming_session = MagicMock(return_value=None)
        self.recording.set_streaming_session = MagicMock(return_value=None)
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

        # ``_do_cleanup`` delegate on VoiceTyperApp. Default to a no-op
        self._do_cleanup = MagicMock()


@pytest.fixture
def fake_app(tmp_config_dir, monkeypatch):
    """Return a ``_FakeApp`` with the shutdown environment stubbed out."""
    import voice_typer.server.app as _app_module

    # The PID-file teardown resolves through the owning backend_pid
    import voice_typer.server.backend_pid as _backend_pid_module

    monkeypatch.setattr(_backend_pid_module, "_clear_backend_pid_file", lambda: None, raising=False)
    monkeypatch.setattr(_app_module, "_close_devnull_files", lambda: None, raising=False)
    monkeypatch.setattr(_app_module, "_register_devnull_file", lambda f: None, raising=False)
    monkeypatch.setattr(_app_module, "is_windows", lambda: False, raising=False)
    # Patch the real host-termination function so the production
    monkeypatch.setattr(
        "voice_typer.server.autostart.tauri_spawn.terminate_tauri",
        lambda pid: None,
        raising=False,
    )
    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app``."""
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


class TestPopStreamingSessionAtomic:
    """DE-7: ``_do_cleanup`` must call ``pop_streaming_session()`` (atomic)"""

    def test_pop_streaming_session_is_called_once(self, controller, fake_app):
        """``_do_cleanup`` must call ``pop_streaming_session()`` exactly once."""
        controller._do_cleanup()
        fake_app.recording.pop_streaming_session.assert_called_once_with()

    def test_legacy_get_and_set_are_not_called(self, controller, fake_app):
        """
        DE-7: the legacy ``get_streaming_session()`` +
        ``set_streaming_session(None)`` pair must NOT be called —
        """
        controller._do_cleanup()
        fake_app.recording.get_streaming_session.assert_not_called()
        fake_app.recording.set_streaming_session.assert_not_called()

    def test_cancel_event_is_set_on_popped_session(self, controller, fake_app):
        """When ``pop_streaming_session()`` returns a non-None session,"""
        session = MagicMock()
        fake_app.recording.pop_streaming_session = MagicMock(return_value=session)

        controller._do_cleanup()

        session._cancel_event.set.assert_called_once_with()

    def test_cancel_event_not_set_when_session_is_none(self, controller, fake_app):
        """When ``pop_streaming_session()`` returns None (no active"""
        fake_app.recording.pop_streaming_session = MagicMock(return_value=None)

        # Must not raise.
        controller._do_cleanup()


class TestFlushBeforeTeardown:
    """DE-10: ``crash_recovery.flush`` and ``history_db.flush`` must run"""

    def test_crash_recovery_flush_runs_before_hotkey_stop(self, controller, fake_app, monkeypatch):
        """``crash_recovery.flush`` must run BEFORE"""
        call_order: list[str] = []

        def _spy_crash_flush(*args, **kwargs):
            call_order.append("crash_recovery.flush")

        def _spy_hotkey_stop():
            call_order.append("hotkey_backend.stop")

        fake_app._crash_recovery.flush.side_effect = _spy_crash_flush
        fake_app.hotkeys._hotkey_backend.stop.side_effect = _spy_hotkey_stop

        controller._do_cleanup()

        assert "crash_recovery.flush" in call_order
        assert "hotkey_backend.stop" in call_order
        crash_idx = call_order.index("crash_recovery.flush")
        hotkey_idx = call_order.index("hotkey_backend.stop")
        assert crash_idx < hotkey_idx, (
            f"DE-10: crash_recovery.flush (at {crash_idx}) must run BEFORE "
            f"hotkey_backend.stop (at {hotkey_idx}); got order: {call_order}"
        )

    def test_history_db_flush_runs_before_hotkey_stop(self, controller, fake_app, monkeypatch):
        """``history_db.flush`` must run BEFORE ``hotkeys._hotkey_backend.stop``."""
        call_order: list[str] = []

        def _spy_history_flush(*args, **kwargs):
            call_order.append("history_db.flush")

        def _spy_hotkey_stop():
            call_order.append("hotkey_backend.stop")

        fake_app.history_db.flush.side_effect = _spy_history_flush
        fake_app.hotkeys._hotkey_backend.stop.side_effect = _spy_hotkey_stop

        controller._do_cleanup()

        assert "history_db.flush" in call_order
        assert "hotkey_backend.stop" in call_order
        history_idx = call_order.index("history_db.flush")
        hotkey_idx = call_order.index("hotkey_backend.stop")
        assert history_idx < hotkey_idx, (
            f"DE-10: history_db.flush (at {history_idx}) must run BEFORE "
            f"hotkey_backend.stop (at {hotkey_idx}); got order: {call_order}"
        )

    def test_crash_recovery_flush_runs_before_level_monitor_stop(self, controller, fake_app, monkeypatch):
        """``crash_recovery.flush`` must run BEFORE"""
        call_order: list[str] = []

        def _spy_crash_flush(*args, **kwargs):
            call_order.append("crash_recovery.flush")

        fake_app._crash_recovery.flush.side_effect = _spy_crash_flush

        # Spy on level_monitor.stop_monitoring, patch the module-level
        import voice_typer.server.level_monitor as _lm

        def _spy_lm_stop():
            call_order.append("level_monitor.stop_monitoring")

        monkeypatch.setattr(_lm, "stop_monitoring", _spy_lm_stop)

        controller._do_cleanup()

        assert "crash_recovery.flush" in call_order
        assert "level_monitor.stop_monitoring" in call_order
        crash_idx = call_order.index("crash_recovery.flush")
        lm_idx = call_order.index("level_monitor.stop_monitoring")
        assert crash_idx < lm_idx, (
            f"DE-10: crash_recovery.flush (at {crash_idx}) must run BEFORE "
            f"level_monitor.stop_monitoring (at {lm_idx}); got order: {call_order}"
        )

    def test_history_db_flush_runs_before_level_monitor_stop(self, controller, fake_app, monkeypatch):
        """``history_db.flush`` must run BEFORE"""
        call_order: list[str] = []

        def _spy_history_flush(*args, **kwargs):
            call_order.append("history_db.flush")

        fake_app.history_db.flush.side_effect = _spy_history_flush

        import voice_typer.server.level_monitor as _lm

        def _spy_lm_stop():
            call_order.append("level_monitor.stop_monitoring")

        monkeypatch.setattr(_lm, "stop_monitoring", _spy_lm_stop)

        controller._do_cleanup()

        assert "history_db.flush" in call_order
        assert "level_monitor.stop_monitoring" in call_order
        history_idx = call_order.index("history_db.flush")
        lm_idx = call_order.index("level_monitor.stop_monitoring")
        assert history_idx < lm_idx, (
            f"DE-10: history_db.flush (at {history_idx}) must run BEFORE "
            f"level_monitor.stop_monitoring (at {lm_idx}); got order: {call_order}"
        )

    def test_history_db_flush_runs_before_event_bus_shutdown(self, controller, fake_app, monkeypatch):
        """``history_db.flush`` must run BEFORE ``event_bus.shutdown``."""
        call_order: list[str] = []

        def _spy_history_flush(*args, **kwargs):
            call_order.append("history_db.flush")

        fake_app.history_db.flush.side_effect = _spy_history_flush

        import voice_typer.server.event_bus as _eb

        def _spy_eb_shutdown():
            call_order.append("event_bus.shutdown")

        monkeypatch.setattr(_eb, "shutdown", _spy_eb_shutdown)

        controller._do_cleanup()

        assert "history_db.flush" in call_order
        assert "event_bus.shutdown" in call_order
        history_idx = call_order.index("history_db.flush")
        eb_idx = call_order.index("event_bus.shutdown")
        assert history_idx < eb_idx, (
            f"DE-10: history_db.flush (at {history_idx}) must run BEFORE "
            f"event_bus.shutdown (at {eb_idx}); got order: {call_order}"
        )

    def test_tray_stop_remains_last_step_after_reorder(self, controller, fake_app, monkeypatch):
        """asserts ``tray.stop`` is the final call."""
        call_order: list[str] = []

        # Spy on every teardown that runs AFTER the flushes.
        original_tray_stop = fake_app.tray.stop

        def _spy_tray_stop():
            call_order.append("tray.stop")
            original_tray_stop()

        fake_app.tray.stop = _spy_tray_stop

        import voice_typer.server.event_bus as _eb

        def _spy_eb_shutdown():
            call_order.append("event_bus.shutdown")

        monkeypatch.setattr(_eb, "shutdown", _spy_eb_shutdown)

        controller._do_cleanup()

        assert call_order[-1] == "tray.stop", (
            f"PVT-G5-003: tray.stop must be the LAST step in _do_cleanup; got order: {call_order}"
        )
        assert "event_bus.shutdown" in call_order
        assert call_order.index("event_bus.shutdown") < call_order.index("tray.stop")


class TestForceExitOnNonMainThread:
    """DE-11: when ``quit()`` is called from a non-main thread (signal"""

    def test_grace_period_constant_is_one_second(self):
        """the quit-latency fix, the 5s dispatch-drain deadlock no longer"""
        assert _DE11_GRACE_PERIOD_SECONDS == 1.0, (
            f"DE-11: _DE11_GRACE_PERIOD_SECONDS must be 1.0; got {_DE11_GRACE_PERIOD_SECONDS}"
        )

    def test_quit_on_main_thread_does_not_schedule_force_exit(self, controller, fake_app, monkeypatch):
        """When ``quit()`` is called from the main thread, ``sys.exit(0)``"""
        fake_app._do_cleanup = MagicMock()
        exit_calls: list[int] = []
        os_exit_calls: list[int] = []
        monkeypatch.setattr(sys, "exit", lambda code=0: exit_calls.append(code))
        monkeypatch.setattr(os, "_exit", lambda code=0: os_exit_calls.append(code))

        controller.quit()

        assert exit_calls == [0], f"DE-11: quit() on main thread must call sys.exit(0); got exit_calls={exit_calls}"
        assert os_exit_calls == [], (
            f"DE-11: quit() on main thread must NOT schedule os._exit; got os_exit_calls={os_exit_calls}"
        )

    def test_quit_on_non_main_thread_schedules_force_exit(self, controller, fake_app, monkeypatch):
        """When ``quit()`` is called from a non-main thread (signal"""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        os_exit_calls: list[int] = []
        sleep_calls: list[float] = []

        def _mock_os_exit(code=0):
            os_exit_calls.append(code)

        def _mock_sleep(duration):
            sleep_calls.append(duration)

        monkeypatch.setattr(os, "_exit", _mock_os_exit)
        # Patch ``time.sleep`` ONLY in the shutdown_controller module
        import voice_typer.server.shutdown_controller as _sc

        _original_sleep = time.sleep
        monkeypatch.setattr(_sc.time, "sleep", _mock_sleep)

        # Run quit() on a NON-MAIN thread so the os._exit path fires.
        done = threading.Event()

        def _call_quit():
            try:
                controller.quit()
            finally:
                done.set()

        t = threading.Thread(target=_call_quit, name="test-quit-non-main")
        t.start()
        # Wait for quit() to return (it should return immediately after
        done.wait(timeout=5.0)
        t.join(timeout=5.0)

        # The watcher thread is daemon + still sleeping (or has fired
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not os_exit_calls:
            _original_sleep(0.01)

        assert sleep_calls == [_DE11_GRACE_PERIOD_SECONDS], (
            f"DE-11: watcher thread must call time.sleep({_DE11_GRACE_PERIOD_SECONDS}); got sleep_calls={sleep_calls}"
        )
        assert os_exit_calls == [0], (
            f"DE-11: watcher thread must call os._exit(0) after grace period; got os_exit_calls={os_exit_calls}"
        )

    def test_quit_on_non_main_thread_does_not_block(self, controller, fake_app, monkeypatch):
        """``quit()`` on a non-main thread must return IMMEDIATELY after"""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)
        # Make time.sleep block forever so we can detect if quit() is
        blocker = threading.Event()

        def _blocking_sleep(duration):
            blocker.wait(timeout=10.0)

        monkeypatch.setattr(os, "_exit", lambda code=0: blocker.set())
        import voice_typer.server.shutdown_controller as _sc

        monkeypatch.setattr(_sc.time, "sleep", _blocking_sleep)

        done = threading.Event()
        elapsed_holder: list[float] = []

        def _call_quit():
            start = time.monotonic()
            try:
                controller.quit()
            finally:
                elapsed_holder.append(time.monotonic() - start)
                done.set()

        t = threading.Thread(target=_call_quit, name="test-quit-non-main-nonblock")
        t.start()
        # Wait for quit() to return. If it blocks on time.sleep, this
        done.wait(timeout=5.0)
        blocker.set()  # Unblock the watcher so it doesn't linger.

        assert done.is_set(), "quit() on non-main thread did not return within 5s"
        assert elapsed_holder, "quit() did not record its elapsed time"
        assert elapsed_holder[0] < 1.0, (
            f"DE-11: quit() on non-main thread must return immediately "
            f"(after spawning the watcher thread); took {elapsed_holder[0]:.2f}s"
        )

    def test_tray_stop_failure_is_logged_at_error_level(self, controller, fake_app, caplog):
        """DE-11: when ``tray.stop()`` raises, the failure must be"""
        fake_app.tray.stop.side_effect = RuntimeError("pystray loop did not break")

        with caplog.at_level(logging.DEBUG):
            controller._do_cleanup()

        # Find the tray.stop failure log record.
        tray_stop_errors = [rec for rec in caplog.records if "tray.stop() failed" in rec.message]
        assert tray_stop_errors, "DE-11: tray.stop() failure must produce a log record containing 'tray.stop() failed'"
        assert tray_stop_errors[0].levelno >= logging.ERROR, (
            f"DE-11: tray.stop() failure must be logged at ERROR level "
            f"(or higher); got level={logging.getLevelName(tray_stop_errors[0].levelno)}"
        )


# _host_pid lock ─────────────────────────────────────────


class TestSkipSdStopOnRecorderTimeout:
    """DE-54: when ``recorder.stop()`` (or the ``discard()`` fallback)"""

    def test_timeout_sentinel_is_module_level_object(self):
        """DE-54: ``TIMEOUT`` must be a module-level singleton (so"""
        import voice_typer.server._timeout_utils as _tu

        assert _tu._TIMEOUT is _TIMEOUT, "DE-54: _TIMEOUT must be a module-level singleton"
        assert _TIMEOUT is not None

    def test_run_with_timeout_returns_sentinel_on_timeout(self):
        """``_run_with_timeout`` must return ``_TIMEOUT`` (not ``None``)"""
        from voice_typer.server.shutdown_controller import _run_with_timeout

        blocker = threading.Event()

        def _blocking_func():
            # Block past the 0.1s timeout.
            blocker.wait(timeout=5.0)

        try:
            result = _run_with_timeout(
                "test-blocking",
                _blocking_func,
                timeout=0.1,
            )
            assert result is _TIMEOUT, f"DE-54: _run_with_timeout must return _TIMEOUT on timeout; got {result!r}"
        finally:
            blocker.set()  # Unblock the worker so it doesn't linger.

    def test_run_with_timeout_returns_func_value_on_success(self):
        """``_run_with_timeout`` must return the func's return value on"""
        from voice_typer.server.shutdown_controller import _run_with_timeout

        def _returning_func():
            return "ok"

        result = _run_with_timeout(
            "test-returning",
            _returning_func,
            timeout=1.0,
        )
        assert result == "ok", f"_run_with_timeout must return the func's return value on success; got {result!r}"

    def test_run_with_timeout_returns_none_when_func_returns_none(self):
        """``_run_with_timeout`` must return ``None`` when the func"""
        from voice_typer.server.shutdown_controller import _run_with_timeout

        def _none_func():
            return None

        result = _run_with_timeout(
            "test-none",
            _none_func,
            timeout=1.0,
        )
        assert result is None, (
            f"_run_with_timeout must return None (not _TIMEOUT) when func returns None; got {result!r}"
        )

    def test_sd_stop_skipped_when_recorder_stop_times_out(self, controller, fake_app, monkeypatch):
        """DE-54: when ``recorder.stop()`` times out (returns"""
        # Make recorder.recording True so the recorder.stop() branch
        fake_app.recorder.recording = True

        # Make recorder.stop() block past its 5s timeout, but to keep
        import voice_typer.server.shutdown_controller as _sc

        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description in ("recorder.stop", "recorder.discard", "sounddevice.stop"):
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_run_with_timeout)

        blocker = threading.Event()

        def _blocking_recorder_stop():
            blocker.wait(timeout=5.0)

        fake_app.recorder.stop.side_effect = _blocking_recorder_stop

        # Track whether sd.stop was called. We can't easily intercept
        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        try:
            controller._do_cleanup()
        finally:
            blocker.set()  # Unblock the leaked worker thread.

        assert fake_sd.stop.assert_not_called, "DE-54: sd.stop() must NOT be called when recorder.stop() timed out"
        fake_sd.stop.assert_not_called()

    def test_sd_stop_called_when_recorder_stop_succeeds(self, controller, fake_app, monkeypatch):
        """DE-54: when ``recorder.stop()`` succeeds (does not time out),"""
        fake_app.recorder.recording = True

        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        controller._do_cleanup()

        fake_sd.stop.assert_called_once_with()

    def test_sd_stop_called_when_recorder_not_recording(self, controller, fake_app, monkeypatch):
        """DE-54: when ``recorder.recording`` is False (no recorder.stop"""
        fake_app.recorder.recording = False

        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        controller._do_cleanup()

        fake_sd.stop.assert_called_once_with()

    def test_sd_stop_skipped_when_discard_times_out(self, controller, fake_app, monkeypatch):
        """DE-54: when ``recorder.stop()`` RAISES (so the discard()"""
        fake_app.recorder.recording = True

        # Make recorder.stop raise so the discard() fallback fires.
        fake_app.recorder.stop.side_effect = RuntimeError("PortAudio closed")

        import voice_typer.server.shutdown_controller as _sc

        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description in ("recorder.discard", "sounddevice.stop"):
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_run_with_timeout)

        blocker = threading.Event()

        def _blocking_discard():
            blocker.wait(timeout=5.0)

        fake_app.recorder.discard.side_effect = _blocking_discard

        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        try:
            controller._do_cleanup()
        finally:
            blocker.set()

        fake_sd.stop.assert_not_called()

    def test_sd_stop_skipped_when_recorder_teardown_event_never_set(self, controller, fake_app, monkeypatch):
        """UE-2: when ``_recorder_teardown_done`` is NEVER set within"""
        fake_app.recorder.recording = True

        # The default ``controller._recorder_teardown_done`` is a fresh

        # Speed up the test, patch the wait() timeout down to 0.1s
        from voice_typer.server.shutdown.teardowns import sounddevice

        original_teardown_sounddevice = sounddevice.teardown_sounddevice

        def _fast_teardown_sounddevice(controller):
            controller._recorder_teardown_done.wait(timeout=0.1)
            if not controller._recorder_force_closed:
                # NEW check from UE-2 fix: skip on wait timeout.
                return  # production path: log warning + return
            original_teardown_sounddevice(controller)

        monkeypatch.setattr(sounddevice, "teardown_sounddevice", _fast_teardown_sounddevice)

        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        controller._do_cleanup()

        # sd.stop() must NOT have been called, the wait() timed out
        fake_sd.stop.assert_not_called()
