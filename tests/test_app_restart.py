"""regression tests for ``restart_app()`` thread-aware exit."""

import contextlib
import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies for restart tests."""
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    yield instance
    # Close the real HistoryDB writer thread + CrashRecovery save thread.
    with contextlib.suppress(Exception):
        instance.history_db.close()
    with contextlib.suppress(Exception):
        instance._crash_recovery.shutdown()


def _stub_restart_environment(app, monkeypatch, *, spy_sys_exit):
    """Stub out the side-effects of restart_app() so it can run in tests"""
    # Stub IPC push so restart_app doesn't try to write to a real TCP
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda msg: None,
    )
    # Skip the 300ms pre-exit sleep in restart_app.
    monkeypatch.setattr("time.sleep", lambda s: None)

    def _fake_sys_exit(code=0):
        spy_sys_exit.append(code)
        raise SystemExit(code)

    monkeypatch.setattr("sys.exit", _fake_sys_exit)

    # Belt-and-suspenders: don't let os._exit kill the pytest process
    monkeypatch.setattr("os._exit", lambda code: None)

    # Mock the cleanup collaborators so we can assert they were called.
    app.recorder = MagicMock()
    app.recorder.recording = True
    app.recording._transcription_thread = None
    app.hotkeys._hotkey_backend = MagicMock()
    app.hotkeys._esc_backend = MagicMock()
    app.hotkeys._repaste_backend = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app.tray = MagicMock()
    app.history_db = MagicMock()
    app._crash_recovery = MagicMock()


class TestRestartAppThreadAwareExit:
    """``restart_app()`` must only ``sys.exit(0)`` on the main"""

    def test_restart_app_calls_sys_exit_on_main_thread(self, app, monkeypatch):
        """When called from the main thread, ``restart_app()`` must call"""
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        # Sanity: we're running on the main thread in pytest.
        assert threading.current_thread() is threading.main_thread()

        cleanup_calls = []
        monkeypatch.setattr(
            app,
            "_do_cleanup",
            lambda: cleanup_calls.append(1),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert spy_sys_exit == [0], (
            f"restart_app must call sys.exit(0) when invoked on the main thread; got sys.exit calls: {spy_sys_exit}"
        )
        assert cleanup_calls == [1], (
            "restart_app must run _do_cleanup before exiting, even on "
            "the main thread (the main-thread exit path still needs to "
            "flush history_db / stop PortAudio / release the mutex)."
        )

    def test_restart_app_does_not_call_sys_exit_off_main_thread(self, app, monkeypatch):
        """When called from a non-main thread (the real-world tray"""
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        # Spy on _do_cleanup to verify it ran (this is the CRITICAL
        cleanup_calls = []
        monkeypatch.setattr(
            app,
            "_do_cleanup",
            lambda: cleanup_calls.append(1),
        )

        # Run restart_app() on a worker thread and capture any
        errors: list = []
        worker = threading.Thread(target=lambda: _run_spy(app, errors))
        worker.start()
        worker.join(timeout=5.0)
        assert not worker.is_alive(), "restart_app worker thread did not exit"

        # No SystemExit should have escaped into the worker thread.
        assert errors == [], (
            "restart_app must NOT raise SystemExit on a non-main thread "
            "(tray.wrap_callback would swallow it, but the process "
            f"would still linger ~1s); got errors: {errors}"
        )

        # sys.exit(0) must NOT have been called.
        assert spy_sys_exit == [], (
            f"restart_app must NOT call sys.exit(0) on a non-main thread; got sys.exit calls: {spy_sys_exit}"
        )

        assert cleanup_calls == [1], (
            "restart_app must run _do_cleanup() on the non-main-thread "
            "path too, tray.stop() (called inside _do_cleanup) is what "
            "breaks the pystray event loop so app.start() can return. "
            f"cleanup_calls={cleanup_calls}"
        )

    def test_restart_app_off_main_thread_preserves_cleanup_and_tray_stop(self, app, monkeypatch):
        """End-to-end (no _do_cleanup stub): on a non-main thread,"""
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        errors: list = []
        worker = threading.Thread(
            target=lambda: _run_spy(app, errors),
        )
        worker.start()
        worker.join(timeout=5.0)
        assert not worker.is_alive(), "restart_app worker thread did not exit"
        assert errors == [], f"restart_app must NOT raise SystemExit on a non-main thread; got errors: {errors}"
        assert spy_sys_exit == [], (
            f"restart_app must NOT call sys.exit(0) on a non-main thread; got sys.exit calls: {spy_sys_exit}"
        )

        app.tray.stop.assert_called_once()


class TestRestartAppInPlaceStandalone:
    """IN-PLACE restart: the process stays alive and the entrypoint loop"""

    def test_standalone_restart_sets_in_place_flag(self, app, monkeypatch):
        """In standalone mode (``_host_pid`` set, a host was spawned"""
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        # Standalone mode: a host process was spawned as a child.
        app._host_pid = 12345
        monkeypatch.setattr(app, "_do_cleanup", lambda: None)

        app.restart_app()

        assert app._in_place_restart is True, (
            "standalone restart must set _in_place_restart=True so the "
            "entrypoint loop re-initializes instead of exiting"
        )
        assert app._is_restarting is True, "standalone restart must still mark _is_restarting=True for cleanup"
        # The in-place path must NOT call sys.exit(0), the process stays
        assert spy_sys_exit == [], (
            f"standalone restart must NOT call sys.exit(0) (in-place restart keeps "
            f"the process alive); got sys.exit calls: {spy_sys_exit}"
        )

    def test_non_standalone_restart_does_not_set_in_place_flag(self, app, monkeypatch):
        """
        In dev mode (no ``_host_pid``, the host spawned Python),
        ``restart_app()`` must NOT set ``_in_place_restart``: the old
        """
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        app._host_pid = None  # dev mode, the host is the parent
        monkeypatch.setattr(app, "_do_cleanup", lambda: None)

        # The out-of-process path calls sys.exit(0) on the main thread.
        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert app._in_place_restart is False, "non-standalone restart must NOT set _in_place_restart"
        assert spy_sys_exit == [0], (
            "non-standalone restart must still call sys.exit(0) on the main thread "
            f"(out-of-process relaunch); got sys.exit calls: {spy_sys_exit}"
        )

    def test_in_place_restart_runs_full_cleanup(self, app, monkeypatch):
        """The in-place path must still run the full ``_do_cleanup()``"""
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=[])

        app._host_pid = 12345  # standalone mode
        cleanup_calls = []
        monkeypatch.setattr(
            app,
            "_do_cleanup",
            lambda: cleanup_calls.append(1),
        )

        app.restart_app()

        assert cleanup_calls == [1], (
            "in-place restart must run _do_cleanup (full teardown) so the "
            f"re-initialized app starts clean; cleanup_calls={cleanup_calls}"
        )


def _run_spy(app, errors):
    """Helper: run ``app.restart_app()`` and capture any exception"""
    try:
        app.restart_app()
    except BaseException as exc:  # noqa: BLE001, we want to capture EVERYTHING
        errors.append(exc)
