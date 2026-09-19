"""Tests for the universal autostart launcher."""

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
        content = (tmp_path / "run" / "autostart.pid").read_text()
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
        assert (config / "run" / "autostart.pid").exists()

    def test_child_pid_optional(self, tmp_path, monkeypatch):
        """When child PID is None, the line should still be written."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._config_dir",
            lambda: tmp_path,
        )
        _write_pid_file(100, None)
        content = (tmp_path / "run" / "autostart.pid").read_text()
        assert "launcher=100" in content
        assert "child=" in content

    def test_non_int_child_pid_normalized_to_empty(self, tmp_path, monkeypatch):
        """A non-int child PID (e.g. a test double's MagicMock leaking into"""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._config_dir",
            lambda: tmp_path,
        )
        _write_pid_file(100, MagicMock())
        content = (tmp_path / "run" / "autostart.pid").read_text()
        assert content.strip().splitlines() == ["launcher=100", "child="], (
            f"non-int child pid must be normalized to empty, got: {content!r}"
        )

    def test_non_int_launcher_pid_not_persisted(self, tmp_path, monkeypatch):
        """A non-int launcher PID is normalized to ``0``, never a garbage"""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._config_dir",
            lambda: tmp_path,
        )
        _write_pid_file(MagicMock(), 5678)
        content = (tmp_path / "run" / "autostart.pid").read_text()
        assert content.strip().splitlines() == ["launcher=0", "child=5678"], (
            f"non-int launcher pid must be normalized to 0, got: {content!r}"
        )


class TestFocusRunningApp:
    """_focus_running_app() spawns the Tauri binary to trigger second-instance."""

    def test_returns_false_when_no_binary(self, monkeypatch):
        """If the Tauri binary is absent, should return False."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: None,
        )
        assert _focus_running_app() is False

    def test_returns_false_when_integrity_gate_fails(self, monkeypatch):
        """If integrity verification fails, the focus probe is refused."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/fake/voice-typer-tauri",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
            lambda path: False,
        )
        assert _focus_running_app() is False

    def test_spawns_tauri_with_focus_only(self, monkeypatch):
        """When the binary verifies, spawn Tauri with VT_FOCUS_ONLY=1."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/fake/voice-typer-tauri",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
            lambda path: True,
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
    """When the backend port is closed, launch starts a fresh Tauri instance."""

    def test_fails_gracefully_without_tauri_mode(self, monkeypatch, tmp_path):
        """No Tauri mode + no Tauri binary → exit 1 (predecessor path removed)."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr(
            "voice_typer.server.backend_pid._backend_pid_file",
            lambda: tmp_path / "nonexistent.pid",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_tauri_mode",
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
            "voice_typer.server.backend_pid._backend_pid_file",
            lambda: Path("/nonexistent.pid"),
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_tauri_mode",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/fake/voice-typer-tauri",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
            lambda path: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._setup_logging",
            lambda: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._write_pid_file",
            lambda lp, cp: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._wait_for_ipc_ready",
            lambda: None,
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
            "voice_typer.server.backend_pid._backend_pid_file",
            lambda: Path("/nonexistent.pid"),
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_tauri_mode",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/fake/voice-typer-tauri",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
            lambda path: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._setup_logging",
            lambda: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._write_pid_file",
            lambda lp, cp: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._wait_for_ipc_ready",
            lambda: None,
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


class TestLegacyDelayClamp:
    """Legacy ``--delay`` values are clamped to a short cap."""

    def _run_launch_with_delay(self, monkeypatch, delay_arg):
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr(
            "voice_typer.server.backend_pid._backend_pid_file",
            lambda: Path("/nonexistent.pid"),
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._setup_logging",
            lambda: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._prewarm_would_help",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_tauri_mode",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/fake/voice-typer-tauri",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
            lambda path: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._spawn_tauri_host",
            lambda binary, hidden=False: MagicMock(pid=1234),
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._write_pid_file",
            lambda lp, cp: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._wait_for_ipc_ready",
            lambda *args, **kwargs: None,
        )
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden", "--delay", delay_arg])
        assert launch() == 0
        return sleeps

    def test_legacy_15s_delay_is_clamped(self, monkeypatch):
        """``--delay 15`` sleeps at most the cap, not the full 15 s."""
        import voice_typer.server.autostart_launcher as launcher_mod

        sleeps = self._run_launch_with_delay(monkeypatch, "15")
        assert len(sleeps) == 1
        assert sleeps[0] == launcher_mod._LAUNCHER_DELAY_CAP_S
        assert sleeps[0] < 15.0

    def test_small_delay_passes_through(self, monkeypatch):
        """``--delay 2`` sleeps the full 2 s (under the cap)."""
        sleeps = self._run_launch_with_delay(monkeypatch, "2")
        assert sleeps == [2.0]


class TestLauncherOutcomeLogging:
    """main() logs a single greppable outcome line for every autostart attempt."""

    def test_logger_uses_dotted_name_not_main(self):
        """The module logger must be under the ``voice_typer`` root so its"""
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
        """A pythonw launch that crashes mid-way must not lose the traceback"""
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
        exc_records = [r for r in caplog.records if r.exc_info]
        assert exc_records, "expected an exception record with traceback"
        import traceback as _tb

        formatted = "".join(_tb.format_exception(*exc_records[0].exc_info))
        assert "launcher exploded" in formatted, formatted
        assert any("RESULT failure exit=1" in m for m in messages), messages


def test_pid_helpers_resolve_without_importing_app():
    """BP-126: the login path must stay light, importing single_instance"""
    code = (
        "import sys, voice_typer.server.single_instance; "
        "sys.exit(0 if 'voice_typer.server.app' not in sys.modules else 1)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"importing voice_typer.server.single_instance pulled in voice_typer.server.app (stderr: {proc.stderr[-500:]})"
    )
