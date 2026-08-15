"""Tests for the universal autostart launcher.

Covers:
  - Port-open path (app already running → focus existing)
  - Port-closed path (app not running → fresh start)
  - --hidden flag → VT_START_HIDDEN=1
  - VT_FOCUS_ONLY env var for lean electron focus probe
  - _is_port_open helper
  - _focus_running_app helper
  - PID file writing
"""

import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

from voice_typer.server.autostart_launcher import (
    IPC_HOST,
    _focus_running_app,
    _is_port_open,
    _write_pid_file,
    launch,
)


class TestIsPortOpen:
    """_is_port_open() correctly detects whether a TCP port is listening."""

    def test_returns_true_when_port_open(self):
        """A TCP server on IPC_PORT should be detected as open."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((IPC_HOST, 0))  # use ephemeral port to avoid conflict
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert _is_port_open(IPC_HOST, port) is True
        finally:
            srv.close()

    def test_returns_false_when_port_closed(self):
        """An unused port should be detected as closed."""
        # Bind+close to find an unused port, then check it.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((IPC_HOST, 0))
        port = s.getsockname()[1]
        s.close()
        assert _is_port_open(IPC_HOST, port) is False


class TestWritePidFile:
    """_write_pid_file persists launcher + child PIDs."""

    def test_writes_pid_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._config_dir",
            lambda: tmp_path,
        )
        _write_pid_file(1234, 5678)
        content = (tmp_path / "autostart.pid").read_text()
        assert "launcher=1234" in content
        assert "child=5678" in content

    def test_handles_missing_dir(self, tmp_path, monkeypatch):
        """Should create the config directory if it doesn't exist."""
        config = tmp_path / "deep" / "nested"
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._config_dir",
            lambda: config,
        )
        _write_pid_file(100, 200)
        assert (config / "autostart.pid").exists()

    def test_child_pid_optional(self, tmp_path, monkeypatch):
        """When child PID is None, the line should still be written."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._config_dir",
            lambda: tmp_path,
        )
        _write_pid_file(100, None)
        content = (tmp_path / "autostart.pid").read_text()
        assert "launcher=100" in content
        assert "child=" in content


class TestFocusRunningApp:
    """_focus_running_app() spawns a lean electron to trigger second-instance."""

    def test_returns_false_when_no_binary(self, monkeypatch):
        """If the electron binary is absent, should return False."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._electron_binary",
            lambda: None,
        )
        assert _focus_running_app() is False

    def test_returns_false_when_no_main_entry(self, monkeypatch):
        """If the built main entry is absent, should return False."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._electron_binary",
            lambda: "/fake/electron",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._main_entry_built",
            lambda: False,
        )
        assert _focus_running_app() is False

    def test_spawns_lean_electron_with_focus_only(self, monkeypatch):
        """When binary + entry exist, should spawn electron with VT_FOCUS_ONLY=1."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._electron_binary",
            lambda: "/fake/electron",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._main_entry_built",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.CLIENT_DIR",
            Path("/fake/client"),
        )
        spawned_env = {}

        def fake_popen(cmd, **kwargs):
            spawned_env.update(kwargs.get("env", {}))
            proc = MagicMock()
            proc.pid = 999
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _focus_running_app()
        assert result is True
        assert spawned_env.get("VT_FOCUS_ONLY") == "1"


class TestLaunchPortOpenPath:
    """When the backend port is already open, launch focuses existing instance."""

    def test_focuses_existing_when_port_open(self, monkeypatch):
        """If port 9876 is open, should call _focus_running_app and exit 0."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: True,
        )
        focus_called = []
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._focus_running_app",
            lambda: focus_called.append(True) or True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._setup_logging",
            lambda: None,
        )
        monkeypatch.setattr(time, "sleep", lambda s: None)
        ret = launch()
        assert ret == 0
        assert focus_called


class TestLaunchPortClosedPath:
    """When the backend port is closed, launch starts a fresh instance."""

    def test_fails_gracefully_without_client_dir(self, monkeypatch, tmp_path):
        """If the client directory doesn't exist, should return 1."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr(
            "voice_typer.server.app._backend_pid_file",
            lambda: tmp_path / "nonexistent.pid",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._client_dir_exists",
            lambda: False,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._setup_logging",
            lambda: None,
        )
        ret = launch()
        assert ret == 1

    def test_sets_vt_start_hidden_when_hidden_flag(self, monkeypatch):
        """When --hidden is passed, VT_START_HIDDEN=1 should be in spawn env."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr(
            "voice_typer.server.app._backend_pid_file",
            lambda: Path("/nonexistent.pid"),
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._client_dir_exists",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._setup_logging",
            lambda: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._write_pid_file",
            lambda lp, cp: None,
        )
        monkeypatch.setattr(time, "sleep", lambda s: None)

        captured_env = {}

        def fake_popen(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            proc = MagicMock()
            proc.pid = 1234
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden"])

        ret = launch()
        assert ret == 0
        assert captured_env.get("VT_START_HIDDEN") == "1"

    def test_no_vt_start_hidden_without_hidden_flag(self, monkeypatch):
        """Without --hidden, VT_START_HIDDEN should not be set."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr(
            "voice_typer.server.app._backend_pid_file",
            lambda: Path("/nonexistent.pid"),
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._client_dir_exists",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._setup_logging",
            lambda: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._write_pid_file",
            lambda lp, cp: None,
        )
        monkeypatch.setattr(time, "sleep", lambda s: None)

        captured_env = {}

        def fake_popen(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            proc = MagicMock()
            proc.pid = 1234
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py"])

        ret = launch()
        assert ret == 0
        assert captured_env.get("VT_START_HIDDEN") is None


class TestLauncherOutcomeLogging:
    """main() logs a single greppable outcome line for every autostart attempt.

    The OS runs this file as a bare script (``pythonw autostart_launcher.py``),
    so ``__name__`` is ``"__main__"`` — the module logger MUST use the
    explicit dotted name (``voice_typer.server.autostart_launcher``) or its
    records never reach the ``voice_typer`` file handler and autostart
    attempts are invisible in ``voice-typer.log`` (the bug that produced
    zero ``[AUTOSTART]`` lines ever).
    """

    def test_logger_uses_dotted_name_not_main(self):
        """The module logger must be under the ``voice_typer`` root so its
        records reach the rotating file handler when run as a script."""
        import voice_typer.server.autostart_launcher as launcher_mod

        assert launcher_mod.log.name == "voice_typer.server.autostart_launcher"
        assert launcher_mod.log.name != "__main__"

    def test_main_logs_success_outcome(self, monkeypatch, caplog):
        from voice_typer.server.autostart_launcher import main

        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.launch",
            lambda: 0,
        )
        import logging

        with caplog.at_level(logging.INFO, logger="voice_typer"):
            assert main() == 0
        result_lines = [r.getMessage() for r in caplog.records if "[AUTOSTART] RESULT" in r.getMessage()]
        assert any("RESULT success exit=0" in m for m in result_lines), result_lines

    def test_main_logs_failure_outcome(self, monkeypatch, caplog):
        from voice_typer.server.autostart_launcher import main

        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.launch",
            lambda: 1,
        )
        import logging

        with caplog.at_level(logging.INFO, logger="voice_typer"):
            assert main() == 1
        result_lines = [r.getMessage() for r in caplog.records if "[AUTOSTART] RESULT" in r.getMessage()]
        assert any("RESULT failure exit=1" in m for m in result_lines), result_lines

    def test_main_catches_unhandled_exception_and_logs_traceback(self, monkeypatch, caplog):
        """A pythonw launch that crashes mid-way must not lose the traceback
        (no console) — main() logs it and returns 1."""
        from voice_typer.server.autostart_launcher import main

        def boom():
            raise RuntimeError("launcher exploded")

        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.launch",
            boom,
        )
        import logging

        with caplog.at_level(logging.ERROR, logger="voice_typer"):
            assert main() == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("RESULT failure unhandled-exception" in m for m in messages), messages
        # log.exception stores the traceback in exc_info, not getMessage().
        exc_records = [r for r in caplog.records if r.exc_info]
        assert exc_records, "expected an exception record with traceback"
        import traceback as _tb

        formatted = "".join(_tb.format_exception(*exc_records[0].exc_info))
        assert "launcher exploded" in formatted, formatted
        assert any("RESULT failure exit=1" in m for m in messages), messages
