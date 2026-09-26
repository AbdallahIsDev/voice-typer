"""Phase 7 regression tests for the ``ShutdownController`` extraction."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest
import voice_typer.server.shutdown_controller
from voice_typer.server.shutdown_controller import ShutdownController

_AUTOSTART = "voice_typer.server.server_platform.autostart"
_MIC_LIST = "voice_typer.server.server_platform.microphone_list"


class _FakeApp:
    """Minimal duck-typed stand-in for ``LausuApp``."""

    def __init__(self):
        # Shutdown state (mirrors LausuApp.__init__)
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._host_pid: int | None = None
        self._mutex_handle = None
        self._quit_app_published = False

        # Subsystem collaborators (MagicMock so any attribute/method call
        self.recorder = MagicMock()
        self.recorder.recording = True
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

        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()

        # Bubble level worker (optional on LausuApp, _do_cleanup
        self._bubble_level_worker_stop = None
        self._bubble_level_queue = None
        self._bubble_level_worker = None

        # ``_do_cleanup`` delegate on LausuApp. Default to a no-op
        self._do_cleanup = MagicMock()


@pytest.fixture
def fake_app(tmp_config_dir, monkeypatch):
    """A ``_FakeApp`` with the shutdown environment stubbed out."""
    monkeypatch.setattr("voice_typer.server.app._clear_backend_pid_file", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._close_devnull_files", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._register_devnull_file", lambda f: None, raising=False)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False, raising=False)
    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app``."""
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


class TestShutdownControllerWiring:
    """Verify ``LausuApp.__init__`` wires up ``ShutdownController``."""

    def test_app_has_shutdown_attribute(self, tmp_config_dir, monkeypatch):
        """``self.shutdown`` must be a ``ShutdownController`` instance."""
        monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
        monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
        monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
        monkeypatch.setattr(f"{_MIC_LIST}.list_microphones", lambda: [], raising=False)

        from voice_typer.server.app import LausuApp

        instance = LausuApp()
        assert hasattr(instance, "shutdown"), "LausuApp.__init__ must construct self.shutdown (ShutdownController)"
        assert isinstance(instance.shutdown, ShutdownController), "self.shutdown must be a ShutdownController instance"

    def test_shutdown_back_references_app(self, tmp_config_dir, monkeypatch):
        """``ShutdownController._app`` must be the ``LausuApp`` instance."""
        monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
        monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
        monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
        monkeypatch.setattr(f"{_MIC_LIST}.list_microphones", lambda: [], raising=False)

        from voice_typer.server.app import LausuApp

        instance = LausuApp()
        assert instance.shutdown._app is instance, (
            "ShutdownController._app must be the LausuApp instance that "
            "constructed it (back-reference for state access)"
        )


class TestQuitCallsDoCleanupAndExits:
    """``ShutdownController.quit`` must delegate to ``_do_cleanup`` and"""

    def test_quit_calls_app_do_cleanup_delegate(self, controller, fake_app, monkeypatch):
        """``monkeypatch.setattr(app, \"_do_cleanup\", spy)`` still intercept"""
        # Replace the delegate with a plain MagicMock so we can assert
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))

        with contextlib.suppress(SystemExit):
            controller.quit()

        fake_app._do_cleanup.assert_called_once_with()

    def test_quit_calls_sys_exit_zero_in_main_thread(self, controller, fake_app, monkeypatch):
        """When called from the main thread, ``quit()`` must call"""
        fake_app._do_cleanup = MagicMock()
        exit_calls = []
        monkeypatch.setattr(sys, "exit", lambda code=0: exit_calls.append(code))

        controller.quit()

        assert exit_calls == [0], f"quit() must call sys.exit(0) when invoked from the main thread; got {exit_calls}"

    def test_quit_is_idempotent_when_already_shutting_down(self, controller, fake_app, monkeypatch):
        """If ``_shutting_down`` is already True, ``quit()`` must"""
        fake_app._shutting_down = True
        fake_app._do_cleanup = MagicMock()
        exit_calls = []
        monkeypatch.setattr(sys, "exit", lambda code=0: exit_calls.append(code))

        controller.quit()

        fake_app._do_cleanup.assert_not_called()
        assert exit_calls == [], "quit() must not call sys.exit when _shutting_down is already True"

    def test_quit_sets_shutting_down_before_cleanup(self, controller, fake_app, monkeypatch):
        """``quit()`` must set ``_shutting_down = True`` BEFORE calling"""
        flag_values_at_cleanup_entry = []

        def spy_do_cleanup():
            flag_values_at_cleanup_entry.append(fake_app._shutting_down)

        fake_app._do_cleanup = MagicMock(side_effect=spy_do_cleanup)
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        controller.quit()

        assert flag_values_at_cleanup_entry == [True], (
            "quit() must set _shutting_down=True BEFORE calling _do_cleanup(); "
            f"got sequence: {flag_values_at_cleanup_entry}"
        )

    def test_quit_calls_thread_registry_shutdown_all(self, controller, fake_app, monkeypatch):
        """``quit()`` must call ``thread_registry.shutdown_all()`` BEFORE"""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        controller.quit()

        fake_app._thread_registry.shutdown_all.assert_called_once_with()

    def test_quit_publishes_quit_app_event_when_not_published(self, controller, fake_app, monkeypatch):
        """``quit()`` must publish the ``quit_app`` event over the TCP"""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        fake_app._quit_app_published = False

        controller.quit()

        assert any(m.get("type") == "quit_app" for m in pushed), (
            f"quit() must publish quit_app when not invoked via quit_app(); got pushes: {pushed!r}"
        )

    def test_quit_skips_quit_app_publish_when_already_published(self, controller, fake_app, monkeypatch):
        """When ``quit()`` is reached via ``quit_app()`` (tray menu / IPC"""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        fake_app._quit_app_published = True

        controller.quit()

        assert not pushed, f"quit() must skip the quit_app publish when quit_app() already published; got: {pushed!r}"

    def test_quit_publish_failure_does_not_block_shutdown(self, controller, fake_app, monkeypatch):
        """A raising ``event_bus.publish`` must be swallowed, shutdown"""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        def _raising(_msg):
            raise RuntimeError("transport gone")

        monkeypatch.setattr("voice_typer.server.event_bus.publish", _raising)
        fake_app._quit_app_published = False

        controller.quit()

        fake_app._do_cleanup.assert_called_once_with()


class TestDoCleanupIdempotency:
    """``_do_cleanup`` must be safe to call multiple times, the"""

    def test_do_cleanup_twice_is_noop(self, controller, fake_app):
        """Calling ``_do_cleanup()`` twice must invoke each subsystem"""
        hk = fake_app.hotkeys._hotkey_backend
        esc = fake_app.hotkeys._esc_backend
        repaste = fake_app.hotkeys._repaste_backend
        controller._do_cleanup()
        controller._do_cleanup()

        fake_app._cancel_pending_timers.assert_called_once()
        fake_app.recorder.stop.assert_called_once()
        fake_app.recorder.shutdown_mic_watcher.assert_called_once()
        fake_app.history_db.flush.assert_called_once()
        fake_app.history_db.close.assert_called_once()
        fake_app._crash_recovery.flush.assert_called_once()
        fake_app._crash_recovery.shutdown.assert_called_once()
        hk.stop.assert_called_once()
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()
        fake_app.tray.stop.assert_called_once()
        fake_app._restore_volume.assert_called_once_with(fade_ms=0)

    def test_do_cleanup_sets_cleanup_done_flag(self, controller, fake_app):
        """After ``_do_cleanup()`` returns, ``_cleanup_done`` must be True"""
        assert fake_app._cleanup_done is False
        controller._do_cleanup()
        assert fake_app._cleanup_done is True

    def test_do_cleanup_idempotent_when_recorder_stop_raises(self, controller, fake_app):
        """Idempotency must hold even when an inner operation raises."""
        fake_app.recorder.stop.side_effect = RuntimeError("PortAudio already closed")
        fake_app.recorder.discard.side_effect = RuntimeError("already discarded")

        controller._do_cleanup()
        # Second call must be a no-op.
        controller._do_cleanup()

        fake_app.recorder.stop.assert_called_once()
        fake_app.recorder.discard.assert_called_once()
        fake_app.history_db.flush.assert_called_once()

    def test_do_cleanup_concurrent_callers_only_one_runs_body(self, controller, fake_app):
        """PVT-G5-026: the check-then-set on ``_cleanup_done`` must be"""
        import threading as _threading

        # Barrier so both threads reach the check-then-set at the same
        barrier = _threading.Barrier(2)

        def _spy_cancel():
            # No-op; we just want to count calls.
            pass

        fake_app._cancel_pending_timers = _spy_cancel

        # Replace _cancel_pending_timers with a counter that's
        call_count = [0]
        call_lock = _threading.Lock()

        def _counting_cancel():
            with call_lock:
                call_count[0] += 1

        fake_app._cancel_pending_timers = _counting_cancel

        def _call_cleanup():
            barrier.wait()
            controller._do_cleanup()

        t1 = _threading.Thread(target=_call_cleanup)
        t2 = _threading.Thread(target=_call_cleanup)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        # ``_cancel_pending_timers`` (the FIRST operation
        assert call_count[0] == 1, (
            f"Concurrent _do_cleanup() callers must not both execute the body; "
            f"_cancel_pending_timers was called {call_count[0]} times (expected 1)."
        )
        # ``_cleanup_done`` is set (the winner set it).
        assert fake_app._cleanup_done is True


class TestDoCleanupSubsystemCoverage:
    """``_do_cleanup`` must call shutdown on every subsystem, no"""

    def test_calls_cancel_pending_timers(self, controller, fake_app):
        controller._do_cleanup()
        fake_app._cancel_pending_timers.assert_called_once_with()

    def test_calls_recording_stop_watchdog_thread(self, controller, fake_app):
        controller._do_cleanup()
        fake_app.recording._stop_watchdog_thread.assert_called_once_with()

    def test_calls_recorder_stop_when_recording(self, controller, fake_app):
        """When ``recorder.recording`` is truthy, ``_do_cleanup`` must"""
        fake_app.recorder.recording = True
        controller._do_cleanup()
        fake_app.recorder.stop.assert_called_once_with()

    def test_calls_recorder_shutdown_mic_watcher(self, controller, fake_app):
        controller._do_cleanup()
        fake_app.recorder.shutdown_mic_watcher.assert_called_once_with()

    def test_calls_restore_volume_with_zero_fade(self, controller, fake_app):
        controller._do_cleanup()
        fake_app._restore_volume.assert_called_once_with(fade_ms=0)

    def test_calls_all_three_hotkey_backend_stops(self, controller, fake_app):
        hk = fake_app.hotkeys._hotkey_backend
        esc = fake_app.hotkeys._esc_backend
        repaste = fake_app.hotkeys._repaste_backend
        controller._do_cleanup()
        hk.stop.assert_called_once_with()
        esc.stop.assert_called_once_with()
        repaste.stop.assert_called_once_with()

    def test_calls_crash_recovery_flush_and_shutdown(self, controller, fake_app):
        controller._do_cleanup()
        fake_app._crash_recovery.flush.assert_called_once_with(timeout=2.0)
        fake_app._crash_recovery.shutdown.assert_called_once_with()

    def test_calls_history_db_flush_and_close(self, controller, fake_app):
        controller._do_cleanup()
        fake_app.history_db.flush.assert_called_once_with()
        fake_app.history_db.close.assert_called_once_with()

    def test_calls_tray_stop(self, controller, fake_app):
        controller._do_cleanup()
        fake_app.tray.stop.assert_called_once_with()

    def test_tray_stop_is_called_after_event_bus_shutdown(self, controller, fake_app, monkeypatch):
        """``_do_cleanup()`` (after ``event_bus.shutdown()``). Previously"""
        call_order: list[str] = []

        original_tray_stop = fake_app.tray.stop

        def _spy_tray_stop():
            call_order.append("tray.stop")
            original_tray_stop()

        fake_app.tray.stop = _spy_tray_stop

        # Spy on event_bus.shutdown(), patch the module-level
        import voice_typer.server.event_bus as _eb

        def _spy_eb_shutdown():
            call_order.append("event_bus.shutdown")

        monkeypatch.setattr(_eb, "shutdown", _spy_eb_shutdown)

        controller._do_cleanup()

        assert "tray.stop" in call_order, "tray.stop() must be called"
        assert "event_bus.shutdown" in call_order, "event_bus.shutdown() must be called"
        tray_idx = call_order.index("tray.stop")
        eb_idx = call_order.index("event_bus.shutdown")
        assert eb_idx < tray_idx, (
            f"PVT-G5-003: event_bus.shutdown() (at index {eb_idx}) must be "
            f"called BEFORE tray.stop() (at index {tray_idx}); got order: {call_order}"
        )
        assert call_order[-1] == "tray.stop", (
            f"PVT-G5-003: tray.stop() must be the LAST step in _do_cleanup(); got order: {call_order}"
        )

    def test_calls_clear_backend_pid_file(self, controller, fake_app, monkeypatch):
        """``_do_cleanup`` must call the dynamic-lookup"""
        clear_calls: list[bool] = []
        monkeypatch.setattr(
            "voice_typer.server.backend_pid._clear_backend_pid_file",
            lambda: clear_calls.append(True),
        )
        controller._do_cleanup()
        assert clear_calls == [True], (
            "_do_cleanup must call _clear_backend_pid_file() so the next "
            "launch isn't falsely blocked by a stale PID file"
        )

    def test_calls_close_devnull_files(self, controller, fake_app, monkeypatch):
        close_calls: list[bool] = []
        monkeypatch.setattr(
            "voice_typer.server.app._close_devnull_files",
            lambda: close_calls.append(True),
        )
        controller._do_cleanup()
        assert close_calls == [True]

    def test_stops_bubble_level_worker_when_present(self, controller, fake_app):
        """When the bubble level worker is wired, ``_do_cleanup`` must"""
        waveform_wiring = MagicMock()
        fake_app.waveform_wiring = waveform_wiring

        controller._do_cleanup()

        waveform_wiring.stop.assert_called_once_with()


class TestInstallSignalHandlers:
    """``_install_signal_handlers`` must register SIGINT/SIGTERM handlers"""

    @pytest.mark.skipif(
        not hasattr(signal, "SIGTERM"),
        reason="SIGTERM not available on this platform (Windows)",
    )
    def test_registers_sigint_and_sigterm_handlers(self, controller, monkeypatch):
        """default SIG_DFL / SIG_IGN)."""
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        try:
            controller._install_signal_handlers()
            new_sigint = signal.getsignal(signal.SIGINT)
            new_sigterm = signal.getsignal(signal.SIGTERM)
            assert new_sigint is not signal.SIG_DFL, "_install_signal_handlers must register a SIGINT handler"
            assert new_sigint is not signal.SIG_IGN, "_install_signal_handlers must register a SIGINT handler"
            assert new_sigterm is not signal.SIG_DFL, "_install_signal_handlers must register a SIGTERM handler"
            assert new_sigterm is not signal.SIG_IGN, "_install_signal_handlers must register a SIGTERM handler"
            assert new_sigint is new_sigterm, "Both signals should share the same handler closure"
        finally:
            # Restore the original handlers.
            with contextlib.suppress(Exception):
                signal.signal(signal.SIGINT, original_sigint)
            with contextlib.suppress(Exception):
                signal.signal(signal.SIGTERM, original_sigterm)

    @pytest.mark.skipif(
        not hasattr(signal, "SIGHUP"),
        reason="SIGHUP not available on this platform (Windows)",
    )
    def test_registers_sighup_handler_on_posix(self, controller, monkeypatch):
        """PVT-G5-014: ``_install_signal_handlers`` must also register"""
        original_sighup = signal.getsignal(signal.SIGHUP)

        try:
            controller._install_signal_handlers()
            new_sighup = signal.getsignal(signal.SIGHUP)
            assert new_sighup is not signal.SIG_DFL, "_install_signal_handlers must register a SIGHUP handler on POSIX"
            assert new_sighup is not signal.SIG_IGN, "_install_signal_handlers must register a SIGHUP handler on POSIX"
            # SIGHUP shares the same handler closure as SIGINT/SIGTERM.
            new_sigint = signal.getsignal(signal.SIGINT)
            assert new_sighup is new_sigint, "SIGHUP should share the same handler closure as SIGINT/SIGTERM"
        finally:
            with contextlib.suppress(Exception):
                signal.signal(signal.SIGHUP, original_sighup)


class TestAtexitCleanupSafetyNet:
    """``_atexit_cleanup`` must be safe to call multiple times, must"""

    def test_atexit_cleanup_when_not_shutting_down_runs_do_cleanup(self, controller, fake_app):
        """When ``_shutting_down`` is False, ``_atexit_cleanup`` must"""
        fake_app._shutting_down = False
        controller._atexit_cleanup()
        fake_app._do_cleanup.assert_called_once_with()

    def test_atexit_cleanup_when_shutting_down_short_circuits(self, controller, fake_app):
        """When ``_shutting_down`` is True (quit/restart already ran),"""
        fake_app._shutting_down = True
        controller._atexit_cleanup()
        fake_app._do_cleanup.assert_not_called()

    def test_atexit_cleanup_safe_to_call_multiple_times(self, controller, fake_app):
        """Calling ``_atexit_cleanup`` multiple times must not raise."""
        fake_app._shutting_down = False
        controller._atexit_cleanup()
        controller._atexit_cleanup()
        # The delegate was called twice (atexit doesn't know about
        assert fake_app._do_cleanup.call_count == 2
        fake_app.history_db.flush.assert_called_once()

    def test_atexit_cleanup_never_raises_when_do_cleanup_raises(self, controller, fake_app):
        """If ``app._do_cleanup()`` raises, ``_atexit_cleanup`` must"""
        fake_app._shutting_down = False
        fake_app._do_cleanup = MagicMock(side_effect=RuntimeError("boom"))
        # Must not raise.
        controller._atexit_cleanup()


class TestAtexitLog:
    """``_atexit_log`` must warn when the process exits without"""

    def test_atexit_log_warns_when_not_shutting_down(self, controller, fake_app, caplog):
        """When ``_shutting_down_event`` is not set, ``_atexit_log`` must"""
        fake_app._shutting_down_event.clear()
        with caplog.at_level("WARNING"):
            controller._atexit_log()
        assert any("likely killed externally" in rec.message for rec in caplog.records), (
            "_atexit_log must warn that the process likely exited without quit()"
        )

    def test_atexit_log_silent_when_shutting_down(self, controller, fake_app, caplog):
        """When ``_shutting_down_event`` is set (intentional shutdown),"""
        fake_app._shutting_down_event.set()
        with caplog.at_level("WARNING"):
            controller._atexit_log()
        assert not any("likely killed externally" in rec.message for rec in caplog.records), (
            "_atexit_log must not warn when _shutting_down_event is set"
        )


class TestInstallWin32ConsoleHandler:
    """``_install_win32_console_handler`` must short-circuit on non-Windows"""

    def test_noop_when_not_windows(self, controller, fake_app, monkeypatch):
        """When ``is_windows()`` returns False, the handler must return"""
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False)
        controller._install_win32_console_handler()
        # _console_handler / _kernel32 should NOT have been set on the app.
        assert not hasattr(fake_app, "_console_handler") or fake_app._console_handler is None
        assert not hasattr(fake_app, "_kernel32") or fake_app._kernel32 is None

    def test_noop_when_pythonw_exe(self, controller, fake_app, monkeypatch):
        """Even on Windows, ``_install_win32_console_handler`` must skip"""
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: True)
        monkeypatch.setattr(sys, "executable", "/fake/path/pythonw.exe")
        controller._install_win32_console_handler()
        # _console_handler / _kernel32 should NOT have been set.
        assert not hasattr(fake_app, "_console_handler") or fake_app._console_handler is None


class TestWin32ConsoleHandlerRouting:
    """``_win32_console_handler`` must return True for handled ctrl-types"""

    def test_close_event_calls_free_console_and_returns_true(self, controller, fake_app):
        """ctrl_close_event (2) must call ``_kernel32.FreeConsole()`` and"""
        fake_app._kernel32 = MagicMock()
        # Ensure _devnull is already open so the open() branch is skipped.
        fake_app._devnull = MagicMock()
        fake_app._devnull.closed = False

        result = controller._win32_console_handler(2)
        assert result is True
        fake_app._kernel32.FreeConsole.assert_called_once_with()

    def test_unknown_ctrl_type_returns_false(self, controller, fake_app):
        """An unknown ctrl_type (e.g. 99) must return False so Windows"""
        result = controller._win32_console_handler(99)
        assert result is False


class TestShutdownWatchdog:
    """when ``quit()`` runs on a non-main thread, ``_do_cleanup()``"""

    def test_watchdog_armed_when_quit_runs_on_non_main_thread(self, controller, fake_app, monkeypatch):
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        armed_calls: list[float] = []

        def _spy_arm(timeout_s: float) -> None:
            armed_calls.append(timeout_s)

        monkeypatch.setattr(controller, "_arm_shutdown_watchdog", _spy_arm)

        done = threading.Event()
        error_holder: list = []

        def _run_quit():
            try:
                controller.quit()
            except BaseException as exc:
                error_holder.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=_run_quit, name="test-quit-thread")
        t.start()
        done.wait(timeout=5.0)

        assert not error_holder, f"quit() on non-main thread raised: {error_holder}"
        assert armed_calls == [voice_typer.server.shutdown_controller.SHUTDOWN_WATCHDOG_TIMEOUT_S], (
            f"GT-43: quit() on non-main thread must arm the watchdog; got armed_calls={armed_calls}"
        )

    def test_watchdog_NOT_armed_when_quit_runs_on_main_thread(self, controller, fake_app, monkeypatch):  # noqa: N802
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        armed_calls: list[float] = []
        monkeypatch.setattr(
            controller,
            "_arm_shutdown_watchdog",
            lambda timeout_s: armed_calls.append(timeout_s),
        )

        controller.quit()

        assert armed_calls == [], (
            f"GT-43: quit() on main thread must NOT arm the watchdog; got armed_calls={armed_calls}"
        )

    def test_watchdog_calls_os_exit_after_timeout(self, monkeypatch):
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = MagicMock()
        ctrl = ShutdownController(fake_app)

        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        start = time.monotonic()
        ctrl._arm_shutdown_watchdog(0.2)
        time.sleep(0.6)
        elapsed = time.monotonic() - start

        assert exit_calls == [0], (
            f"GT-43: watchdog must call os._exit(0) after the timeout; got exit_calls={exit_calls}"
        )
        assert elapsed >= 0.2, f"GT-43: watchdog fired too early, expected ≥0.2s, got {elapsed:.2f}s"

    def test_drain_shutdown_watchdogs_cancels_before_os_exit(self, monkeypatch):
        """A leaked shutdown watchdog (armed by a non-main-thread"""
        from voice_typer.server.shutdown.lifecycle import (
            _LIVE_SHUTDOWN_WATCHDOG_THREADS,
            _WATCHDOG_CANCEL_EVENTS,
            _drain_shutdown_watchdogs,
        )
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = MagicMock()
        ctrl = ShutdownController(fake_app)

        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        # Arm a real watchdog with a LONG timeout (like a leaked one).
        ctrl._arm_shutdown_watchdog(30.0)
        assert len(list(_LIVE_SHUTDOWN_WATCHDOG_THREADS)) == 1, (
            "the armed watchdog must be registered so the drain can find it"
        )

        # Drain must cancel it without waiting 30s and without os._exit.
        _drain_shutdown_watchdogs()

        assert exit_calls == [], f"drain must cancel the watchdog before os._exit; got exit_calls={exit_calls}"
        assert not list(_LIVE_SHUTDOWN_WATCHDOG_THREADS), "drained watchdog threads must be removed from the registry"
        assert not list(_WATCHDOG_CANCEL_EVENTS), "drained watchdog cancel events must be removed from the registry"


class TestRecorderForceClosedBarrier:
    """when ``recorder.stop()`` (or ``recorder.discard()``) times"""

    def test_shutdown_mic_watcher_skipped_when_recorder_stop_times_out(self, controller, fake_app, monkeypatch):
        fake_app.recorder.recording = True

        import voice_typer.server.shutdown_controller as _sc

        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description == "recorder.stop":
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_run_with_timeout)

        blocked = threading.Event()

        def _blocking_stop():
            blocked.wait(timeout=5.0)

        fake_app.recorder.stop = _blocking_stop

        controller._do_cleanup()

        blocked.set()

        fake_app.recorder.shutdown_mic_watcher.assert_not_called()
        assert getattr(fake_app.recorder, "_force_closed", None) is True, (
            "GT-70: recorder.stop() timeout must set app.recorder._force_closed = True"
        )

    def test_shutdown_mic_watcher_called_when_recorder_stop_completes(self, controller, fake_app):
        fake_app.recorder.recording = True

        controller._do_cleanup()

        fake_app.recorder.stop.assert_called_once_with()
        fake_app.recorder.shutdown_mic_watcher.assert_called_once_with()

    def test_timeout_sentinel_distinct_from_none(self):
        from voice_typer.server.shutdown_controller import TIMEOUT

        assert TIMEOUT is not None, "GT-70: TIMEOUT sentinel must not be None"
        assert TIMEOUT is TIMEOUT, "TIMEOUT sentinel identity check"


# _force_closed read-side behavior test ──────────────────────


class TestForceClosedReadSideGuard:
    """the ``_force_closed`` flag must be a REAL behavior gate, not"""

    def _make_test_recorder(self):
        """Construct a real ``Recorder`` with mocked collaborators."""
        from voice_typer.server.recording.recorder import Recorder

        # Recorder.__init__ signature: (config, audio_processor, thread_registry)
        config = MagicMock()
        recorder = Recorder(
            config=config,
            audio_processor=MagicMock(),
            thread_registry=MagicMock(),
        )
        # Replace the DeviceManager delegate with a MagicMock so we can
        recorder._devices = MagicMock()
        return recorder

    def test_shutdown_mic_watcher_short_circuits_when_force_closed_set(self):
        """when ``_force_closed = True``, calling"""
        recorder = self._make_test_recorder()
        # Sanity: flag starts False (declared in Recorder.__init__).
        assert recorder._force_closed is False, "ZR-35: Recorder.__init__ must declare _force_closed: bool = False"

        # Simulate the force-closed condition (as shutdown_controller
        recorder._force_closed = True

        # Call shutdown_mic_watcher, should short-circuit.
        recorder.shutdown_mic_watcher()

        # The delegate MUST NOT have been called.
        recorder._devices.shutdown_mic_watcher.assert_not_called()

    def test_shutdown_mic_watcher_delegates_when_force_closed_false(self):
        """guard doesn't false-positive (skipping the delegate when it"""
        recorder = self._make_test_recorder()
        assert recorder._force_closed is False

        recorder.shutdown_mic_watcher()

        # The delegate MUST have been called exactly once.
        recorder._devices.shutdown_mic_watcher.assert_called_once_with()

    def test_force_closed_is_declared_in_init(self):
        """(init contract): ``Recorder.__init__`` must declare"""
        recorder = self._make_test_recorder()
        # The attribute must exist with the default False value —
        assert hasattr(recorder, "_force_closed"), (
            "ZR-35: Recorder.__init__ must declare _force_closed so the "
            "attribute exists before shutdown_controller writes it"
        )
        assert recorder._force_closed is False, "ZR-35: _force_closed must default to False"


class TestInFlightTimerDrain:
    """delegate only calls ``Timer.cancel()`` (a no-op for already-fired"""

    def test_do_cleanup_joins_in_flight_timer_threads(self, controller, fake_app, monkeypatch):
        import threading as _threading

        in_flight_started = _threading.Event()
        in_flight_can_finish = _threading.Event()

        def _slow_func():
            in_flight_started.set()
            in_flight_can_finish.wait(timeout=5.0)

        timer = _threading.Timer(0.01, _slow_func)
        timer.daemon = True

        timers_coord = MagicMock()
        timers_coord._pending_timers_lock = _threading.Lock()
        timers_coord._pending_timers = [timer]
        fake_app.timers = timers_coord

        timer.start()
        assert in_flight_started.wait(timeout=2.0), "GT-72: test setup failed, in-flight timer never started"

        cleanup_done = _threading.Event()
        cleanup_errors: list = []

        def _run_cleanup():
            try:
                controller._do_cleanup()
            except BaseException as exc:
                cleanup_errors.append(exc)
            finally:
                cleanup_done.set()

        cleanup_thread = _threading.Thread(target=_run_cleanup, name="test-cleanup-thread-gt72")
        cleanup_thread.start()

        time.sleep(0.1)
        in_flight_can_finish.set()

        assert cleanup_done.wait(timeout=5.0), "GT-72: _do_cleanup didn't complete after releasing in-flight timer"
        assert not cleanup_errors, f"GT-72: _do_cleanup raised: {cleanup_errors}"

        fake_app._cancel_pending_timers.assert_called_once_with()

    def test_do_cleanup_does_not_deadlock_when_no_timers_coord(self, controller, fake_app):
        fake_app.timers = None

        controller._do_cleanup()

        fake_app._cancel_pending_timers.assert_called_once_with()
