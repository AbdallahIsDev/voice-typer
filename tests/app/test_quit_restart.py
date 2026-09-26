"""All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``"""

import contextlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _stub_restart_for_log_test(app, monkeypatch):
    """Stub out restart_app side effects so it can run for log capture."""
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda msg: None,
    )
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(
        "sys.exit",
        lambda code=0: (_ for _ in ()).throw(SystemExit(code)),
    )
    monkeypatch.setattr("os._exit", lambda code: None)
    app.hotkeys._hotkey_backend = MagicMock()
    app.hotkeys._esc_backend = MagicMock()
    app.hotkeys._repaste_backend = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app.tray = MagicMock()
    app.recorder = MagicMock()
    app.recorder.recording = False
    app.recording._transcription_thread = None
    app.recording.get_streaming_session = MagicMock(return_value=None)
    app.recording.set_streaming_session = MagicMock()


class TestQuitAppCleanShutdown:
    """RELIABILITY-001: ``quit_app`` must NOT use ``os._exit(0)``."""

    def test_quit_app_does_not_call_os_exit(self, app, monkeypatch):
        """os._exit(0) must never be called from quit_app."""
        os_exit_called = []
        monkeypatch.setattr(
            "os._exit",
            lambda code: os_exit_called.append(code),
        )
        # Stub out clean-shutdown side effects so quit() can run without
        app._cancel_pending_timers = MagicMock()
        app.recording.get_streaming_session = MagicMock(return_value=None)
        app.recording.set_streaming_session = MagicMock()
        app.recorder = MagicMock()
        app.recording._transcription_thread = None
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.quit_app()

        assert os_exit_called == [], f"quit_app must not call os._exit; called with {os_exit_called}"

    def test_quit_app_calls_self_quit(self, app, monkeypatch):
        """quit_app should delegate to self.quit() (the audited cleanup"""
        quit_called = []

        def fake_quit():
            quit_called.append(True)
            raise SystemExit(0)

        monkeypatch.setattr(app, "quit", fake_quit)
        # Stub the side-effect that runs before quit(), push_event
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: None,
        )
        # Belt-and-suspenders: if quit_app falls through to os._exit
        monkeypatch.setattr("os._exit", lambda code: None)

        with pytest.raises(SystemExit):
            app.quit_app()

        assert quit_called == [True], "quit_app must call self.quit()"

    def test_quit_app_notifies_host_first(self, app, monkeypatch):
        """Before any cleanup, quit_app pushes a quit_app event over IPC"""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        # Stub self.quit() so we can verify the push happens before it.
        monkeypatch.setattr(app, "quit", lambda: (_ for _ in ()).throw(SystemExit(0)))
        # Belt-and-suspenders: don't let os._exit kill the pytest process.
        monkeypatch.setattr("os._exit", lambda code: None)

        with pytest.raises(SystemExit):
            app.quit_app()

        assert pushed == [{"type": "quit_app"}]

    def test_quit_stops_esc_and_repaste_backends(self, app, monkeypatch):
        """RELIABILITY-003: quit() (called by quit_app) must stop"""
        app._cancel_pending_timers = MagicMock()
        app.recording.get_streaming_session = MagicMock(return_value=None)
        app.recording.set_streaming_session = MagicMock()
        app.recorder = MagicMock()
        app.recording._transcription_thread = None
        # ``shutdown_controller._teardown_hotkeys`` now NULLS
        hotkey_backend = MagicMock()
        esc_backend = MagicMock()
        repaste_backend = MagicMock()
        app.hotkeys._hotkey_backend = hotkey_backend
        app.hotkeys._esc_backend = esc_backend
        app.hotkeys._repaste_backend = repaste_backend
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.quit()

        hotkey_backend.stop.assert_called_once()
        esc_backend.stop.assert_called_once()
        repaste_backend.stop.assert_called_once()


class TestRestartAppCleanShutdown:
    """RELIABILITY-001: ``restart_app`` must NOT use ``os._exit(0)``."""

    def test_restart_app_does_not_call_os_exit(self, app, monkeypatch):
        os_exit_called = []
        monkeypatch.setattr(
            "os._exit",
            lambda code: os_exit_called.append(code),
        )
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        # Force the pre-restart sleep to no-op so the test is fast.
        monkeypatch.setattr("time.sleep", lambda s: None)
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert os_exit_called == [], f"restart_app must not call os._exit; called with {os_exit_called}"

    def test_restart_app_stops_esc_and_repaste_backends(self, app, monkeypatch):
        """RELIABILITY-003: restart_app must stop esc_backend and"""
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("time.sleep", lambda s: None)
        # Belt-and-suspenders: don't let os._exit kill the pytest process.
        monkeypatch.setattr("os._exit", lambda code: None)
        hotkey_backend = MagicMock()
        esc_backend = MagicMock()
        repaste_backend = MagicMock()
        app.hotkeys._hotkey_backend = hotkey_backend
        app.hotkeys._esc_backend = esc_backend
        app.hotkeys._repaste_backend = repaste_backend
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        hotkey_backend.stop.assert_called_once()
        esc_backend.stop.assert_called_once()
        repaste_backend.stop.assert_called_once()

    def test_restart_app_calls_tray_stop(self, app, monkeypatch):
        """restart_app must call self.tray.stop() to break the pystray"""
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("time.sleep", lambda s: None)
        # Belt-and-suspenders: don't let os._exit kill the pytest process.
        monkeypatch.setattr("os._exit", lambda code: None)
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.tray.stop.assert_called_once()

    def test_restart_app_sets_shutting_down_before_exit(self, app, monkeypatch):
        """RELIABILITY-006: restart_app must set _shutting_down=True so"""
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("os._exit", lambda code: None)
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        # Sanity: flag starts False.
        assert app._shutting_down is False

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # Must be True after restart_app so the atexit handler
        assert app._shutting_down is True, (
            "RELIABILITY-006 regression: restart_app did not set "
            "_shutting_down=True; atexit handler will misclassify "
            "intentional restarts as external kills."
        )


class TestRestartAppCleanupPath:
    """TEST-004: verify that restart_app stops all three hotkey backends"""

    def test_restart_stops_all_backends(self, app, monkeypatch):
        """restart_app must stop _hotkey_backend, _esc_backend, and"""
        import subprocess as _sp

        monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("os._exit", lambda code: None)
        monkeypatch.setattr("sys.exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
        hotkey_backend = MagicMock()
        esc_backend = MagicMock()
        repaste_backend = MagicMock()
        app.hotkeys._hotkey_backend = hotkey_backend
        app.hotkeys._esc_backend = esc_backend
        app.hotkeys._repaste_backend = repaste_backend
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        hotkey_backend.stop.assert_called_once()
        esc_backend.stop.assert_called_once()
        repaste_backend.stop.assert_called_once()

    def test_restart_calls_tray_stop(self, app, monkeypatch):
        """restart_app must call tray.stop() to break the pystray loop."""
        import subprocess as _sp

        monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("os._exit", lambda code: None)
        monkeypatch.setattr("sys.exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.tray.stop.assert_called_once()

    def test_restart_does_not_use_os_exit(self, app, monkeypatch):
        """restart_app must exit via sys.exit(0), not os._exit(0)."""
        import subprocess as _sp

        os_exit_calls = []
        monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("os._exit", lambda code: os_exit_calls.append(code))
        monkeypatch.setattr("sys.exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert os_exit_calls == [], f"restart_app must not call os._exit; got {os_exit_calls}"


class TestAppRestartLogMessage:
    """``%s`` placeholder survived verbatim into the formatted log line"""

    def test_restart_log_format_string_has_argument(self):
        """format argument so the placeholder is substituted."""
        import inspect

        from voice_typer.server.app_lifecycle import LifecycleController

        src = inspect.getsource(LifecycleController.restart_app)
        restart_log_idx = src.find("Restarting %s...")
        assert restart_log_idx != -1, 'APP-2: restart_app must contain log.info("[RESTART] Restarting %s...")'
        line_end = src.find("\n", restart_log_idx)
        line = src[restart_log_idx:line_end]
        assert "APP_NAME" in line, (
            'APP-2: log.info("[RESTART] Restarting %s...") must pass '
            "APP_NAME as the format argument; got: " + repr(line)
        )

    def test_restart_log_does_not_leave_percent_s_in_output(self, app, monkeypatch, caplog):
        """Runtime check: when restart_app runs, the formatted log line"""
        import logging

        _stub_restart_for_log_test(app, monkeypatch)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.app"), contextlib.suppress(SystemExit):
            app.restart_app()

        restart_lines = [r.message for r in caplog.records if "Restarting" in r.message]
        assert restart_lines, "APP-2: restart_app must emit a 'Restarting' log line at INFO level"
        for line in restart_lines:
            assert "%s" not in line, (
                "APP-2: the formatted restart log line must not contain a "
                "literal %s (missing format argument); got: " + repr(line)
            )


class TestAppQuitAppAlwaysPushesEvent:
    """double-quit, the second call early-returned without pushing —"""

    def test_quit_app_pushes_event_even_when_already_shutting_down(self, app, monkeypatch):
        """When _shutting_down is already True, quit_app must STILL"""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        monkeypatch.setattr("os._exit", lambda code: None)
        quit_calls = []
        monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))
        app._shutting_down = True
        app._shutting_down_event.set()

        app.quit_app()

        assert pushed == [{"type": "quit_app"}], (
            "APP-10: quit_app must push the quit_app event EVEN WHEN "
            "_shutting_down is already True (so the Tauri host is notified on "
            "a double-quit). Got pushes: " + repr(pushed)
        )
        assert quit_calls == [], (
            "APP-10: when _shutting_down is True, quit_app must skip "
            "the duplicate self.quit() call (only the event push runs)"
        )

    def test_quit_app_calls_self_quit_when_not_shutting_down(self, app, monkeypatch):
        """Sanity: when _shutting_down is False, quit_app must push"""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        quit_calls = []
        monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))
        monkeypatch.setattr("os._exit", lambda code: None)

        assert app._shutting_down is False

        app.quit_app()

        assert pushed == [{"type": "quit_app"}]
        assert quit_calls == [True], (
            "APP-10: when _shutting_down is False, quit_app must still call self.quit() after pushing the event"
        )

    def test_quit_app_push_happens_before_shutting_down_check(self):
        """Source-level invariant: the event_bus.publish call must"""
        import inspect

        from voice_typer.server.app_lifecycle import LifecycleController

        src = inspect.getsource(LifecycleController.quit_app)
        publish_idx = src.find('event_bus.publish({"type": "quit_app"})')
        assert publish_idx != -1, "APP-10: quit_app must call event_bus.publish with the quit_app event"
        guard_idx = src.find("if app._shutting_down_event.is_set():", publish_idx)
        assert guard_idx != -1, (
            "APP-10: quit_app must have an 'if app._shutting_down_event.is_set():' "
            "guard AFTER the event_bus.publish call (not before, which "
            "was the pre-fix ordering that dropped quit_app events on "
            "double-quit)"
        )


class TestSingleInstanceEnforcement:
    """TEST-037: verify LausuApp is only instantiated once per"""

    def test_voice_typer_app_has_single_call_site(self):
        """LausuApp() must be called from exactly one location."""
        import ast

        import voice_typer.server as server_pkg

        pkg_dir = Path(server_pkg.__file__).parent
        call_sites = []
        for py_file in pkg_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "LausuApp":
                    call_sites.append(f"{py_file.relative_to(pkg_dir).as_posix()}:{node.lineno}")
        assert len(call_sites) == 1, (
            f"LausuApp() should be called from exactly one location "
            f"(ipc/entrypoint.main); found {len(call_sites)} call sites: {call_sites}"
        )
        assert "ipc/entrypoint.py" in call_sites[0], (
            f"LausuApp() should only be called from ipc/entrypoint.py; found call at {call_sites[0]}"
        )

    def test_ensure_single_instance_is_called_from_main(self):
        """ipc/entrypoint.main() must call _ensure_single_instance before"""
        import voice_typer.server.ipc.entrypoint as entrypoint

        source = Path(entrypoint.__file__).read_text(encoding="utf-8")
        assert "_ensure_single_instance" in source, (
            "ipc/entrypoint.py must call _ensure_single_instance to enforce the single-process invariant"
        )
        assert "LausuApp()" in source, "ipc/entrypoint.py must instantiate LausuApp exactly once"
        si_idx = source.index("_ensure_single_instance")
        app_idx = source.index("LausuApp()")
        assert si_idx < app_idx, (
            "_ensure_single_instance must be called BEFORE LausuApp() "
            "so a duplicate process exits before loading heavy modules."
        )


# APP-N regression tests () ─────────────────────────────────────
