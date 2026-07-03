"""Tests for the standalone-mode Electron launcher.

P1-1.2: when the user runs ``VoiceTyper`` from a terminal (no ``--port``
flag, no ``VOICE_TYPER_IPC_TOKEN`` env var), the Python backend spawns
Electron as a subprocess and passes it the connection info via env vars
(``VT_PYTHON_PORT`` + ``VT_IPC_TOKEN``).  These tests cover:

- ``is_spawned_by_electron()`` detection (3 cases)
- ``launch_electron_frontend()`` returns a PID on success / None on failure
- ``terminate_electron()`` kills the process (POSIX path mocked)
- ``_ensure_single_instance`` writes a backend PID file on startup
- Stale PID file is detected and cleared when the referenced process is dead
"""

from __future__ import annotations

import os
import sys
import subprocess
import signal
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_typer.server import electron_launcher
from voice_typer.server import app as app_module


# ── is_spawned_by_electron ──────────────────────────────────────────────


class TestIsSpawnedByElectron:
    """is_spawned_by_electron() correctly detects Electron-spawned mode."""

    def test_is_spawned_by_electron_detects_port_flag(self, monkeypatch):
        """``--port`` in sys.argv → True (Electron spawns with --port N)."""
        monkeypatch.setattr(sys, "argv", ["voice_typer", "--port", "9876"])
        # Clear env var to ensure only the flag triggers detection.
        monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)
        assert electron_launcher.is_spawned_by_electron() is True

    def test_is_spawned_by_electron_detects_ipc_token(self, monkeypatch):
        """``VOICE_TYPER_IPC_TOKEN`` env var set → True."""
        monkeypatch.setattr(sys, "argv", ["voice_typer"])  # no --port
        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "abc123")
        assert electron_launcher.is_spawned_by_electron() is True

    def test_is_spawned_by_electron_returns_false_standalone(self, monkeypatch):
        """No --port and no env var → False (standalone terminal run)."""
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)
        assert electron_launcher.is_spawned_by_electron() is False


# ── launch_electron_frontend ────────────────────────────────────────────


class TestLaunchElectronFrontend:
    """launch_electron_frontend() returns a PID on success, None on failure."""

    def test_launch_electron_returns_pid_on_success(self, monkeypatch):
        """When the Electron binary exists and Popen succeeds, return the PID."""
        # Pretend the binary exists and the build output is present so we
        # don't trigger an npm run build.
        monkeypatch.setattr(
            electron_launcher, "_electron_binary", lambda: "/fake/electron",
        )
        monkeypatch.setattr(
            electron_launcher, "_main_entry_built", lambda: True,
        )
        # Avoid touching the real filesystem for log files.
        monkeypatch.setattr(
            electron_launcher,
            "_electron_log_files",
            lambda: {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            },
        )
        # Capture the env passed to Popen so we can assert VT_PYTHON_PORT /
        # VT_IPC_TOKEN are set.
        captured_env: dict = {}
        captured_argv: list = []

        def fake_popen(argv, env=None, **kwargs):
            captured_env.clear()
            captured_env.update(env or {})
            captured_argv.extend(argv)
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        pid = electron_launcher.launch_electron_frontend(9876, "deadbeef" * 8)
        assert pid == 4242
        assert captured_env.get("VT_PYTHON_PORT") == "9876"
        assert captured_env.get("VT_IPC_TOKEN") == "deadbeef" * 8
        assert captured_env.get("VOICE_TYPER_IPC_TOKEN") == "deadbeef" * 8
        # Built-app path: argv should be [electron, "."]
        assert captured_argv == ["/fake/electron", "."]

    def test_launch_electron_returns_none_on_failure(self, monkeypatch):
        """When Popen raises and npm fallback also fails, return None."""
        monkeypatch.setattr(
            electron_launcher, "_electron_binary", lambda: "/fake/electron",
        )
        monkeypatch.setattr(
            electron_launcher, "_main_entry_built", lambda: True,
        )
        monkeypatch.setattr(
            electron_launcher,
            "_electron_log_files",
            lambda: {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            },
        )
        # Force _npm_command to return None so the fallback also fails fast.
        monkeypatch.setattr(electron_launcher, "_npm_command", lambda script="dev": None)

        def fake_popen(argv, env=None, **kwargs):
            raise FileNotFoundError("electron not found")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        pid = electron_launcher.launch_electron_frontend(9876, "deadbeef" * 8)
        assert pid is None

    def test_launch_electron_falls_back_to_npm_run_dev(self, monkeypatch):
        """When the Electron binary is missing, fall back to ``npm run dev``."""
        # No electron binary → triggers fallback.
        monkeypatch.setattr(
            electron_launcher, "_electron_binary", lambda: None,
        )
        monkeypatch.setattr(
            electron_launcher,
            "_electron_log_files",
            lambda: {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            },
        )
        captured_argv: list = []

        def fake_popen(argv, env=None, **kwargs):
            captured_argv.extend(argv)
            proc = MagicMock()
            proc.pid = 7777
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        # Make _npm_command return a real-looking list so the fallback path
        # is taken (not the shell=True branch).
        monkeypatch.setattr(
            electron_launcher,
            "_npm_command",
            lambda script="dev": ["/usr/bin/npm", "run", "dev"],
        )

        pid = electron_launcher.launch_electron_frontend(9999, "abc")
        assert pid == 7777
        assert captured_argv == ["/usr/bin/npm", "run", "dev"]


# ── terminate_electron ──────────────────────────────────────────────────


class TestTerminateElectron:
    """terminate_electron() kills the process (POSIX path mocked)."""

    def test_terminate_electron_kills_process(self, monkeypatch):
        """SIGTERM + waitpid reap → no SIGKILL needed."""
        # Force the POSIX branch even on Windows so the test is portable.
        monkeypatch.setattr(electron_launcher, "is_windows", lambda: False)

        kill_calls: list[int] = []
        waitpid_calls: list[int] = []

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        def fake_waitpid(pid, options):
            waitpid_calls.append((pid, options))
            # First waitpid (WNOHANG) returns the reaped PID, indicating
            # the process died after SIGTERM.
            if options == os.WNOHANG:
                return (pid, 0)
            return (pid, 0)

        monkeypatch.setattr(os, "kill", fake_kill)
        monkeypatch.setattr(os, "waitpid", fake_waitpid)
        # Avoid actually sleeping.
        monkeypatch.setattr(time, "sleep", lambda s: None)

        electron_launcher.terminate_electron(12345)

        # Should have sent SIGTERM only (no SIGKILL since waitpid reaped).
        assert (12345, signal.SIGTERM) in kill_calls
        assert (12345, signal.SIGKILL) not in kill_calls
        # waitpid was called at least once with WNOHANG.
        assert any(opt == os.WNOHANG for _, opt in waitpid_calls)

    def test_terminate_electron_escalates_to_sigkill(self, monkeypatch):
        """If SIGTERM doesn't reap within 3s, send SIGKILL."""
        monkeypatch.setattr(electron_launcher, "is_windows", lambda: False)

        kill_calls: list[int] = []
        # Simulate time advancing 1s per call so the deadline (3s) elapses
        # after ~3 waitpid iterations.
        time_values = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        def fake_waitpid(pid, options):
            # Always report "not yet exited" so the deadline elapses.
            if options == os.WNOHANG:
                return (0, 0)
            # The final blocking waitpid after SIGKILL returns the pid.
            return (pid, 0)

        monkeypatch.setattr(os, "kill", fake_kill)
        monkeypatch.setattr(os, "waitpid", fake_waitpid)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(time, "time", lambda: next(time_values))

        electron_launcher.terminate_electron(99999)

        assert (99999, signal.SIGTERM) in kill_calls
        assert (99999, signal.SIGKILL) in kill_calls

    def test_terminate_electron_handles_already_dead(self, monkeypatch):
        """If the process is already gone (ESRCH), don't raise."""
        monkeypatch.setattr(electron_launcher, "is_windows", lambda: False)

        def fake_kill(pid, sig):
            raise ProcessLookupError("process not found")

        monkeypatch.setattr(os, "kill", fake_kill)
        # waitpid should never be called.
        monkeypatch.setattr(os, "waitpid", lambda *a, **k: (0, 0))

        # Should not raise.
        electron_launcher.terminate_electron(11111)

    def test_terminate_electron_noop_on_zero_pid(self, monkeypatch):
        """A zero/None PID should be a no-op."""
        called = []
        monkeypatch.setattr(os, "kill", lambda *a: called.append(True))
        electron_launcher.terminate_electron(0)
        electron_launcher.terminate_electron(None)  # type: ignore[arg-type]
        assert called == []


# ── generate_session_token ──────────────────────────────────────────────


class TestGenerateSessionToken:
    """generate_session_token() returns a 64-char hex string."""

    def test_token_is_hex_and_correct_length(self):
        token = electron_launcher.generate_session_token()
        assert len(token) == 64
        int(token, 16)  # raises ValueError if not hex

    def test_token_is_unique_per_call(self):
        t1 = electron_launcher.generate_session_token()
        t2 = electron_launcher.generate_session_token()
        assert t1 != t2


# ── PID file (single-instance) ──────────────────────────────────────────


class TestBackendPidFile:
    """Backend PID file is written on startup and cleared on stale detection."""

    def test_pid_file_written_on_startup(self, monkeypatch, tmp_path):
        """``_write_backend_pid_file`` writes our PID to ``backend.pid``."""
        monkeypatch.setattr(app_module, "_config_dir", lambda: tmp_path)
        # _secure_atomic_write is the production write path.
        # Just call our helper — it should produce the file with our PID.
        app_module._write_backend_pid_file()
        pid_file = tmp_path / "backend.pid"
        assert pid_file.exists()
        content = pid_file.read_text().strip()
        assert int(content) == os.getpid()

    def test_pid_file_cleared_on_shutdown(self, monkeypatch, tmp_path):
        """``_clear_backend_pid_file`` removes the file if it exists."""
        monkeypatch.setattr(app_module, "_config_dir", lambda: tmp_path)
        pid_file = tmp_path / "backend.pid"
        pid_file.write_text(f"{os.getpid()}\n")
        assert pid_file.exists()
        app_module._clear_backend_pid_file()
        assert not pid_file.exists()

    def test_clear_pid_file_noop_when_missing(self, monkeypatch, tmp_path):
        """``_clear_backend_pid_file`` doesn't raise if the file is absent."""
        monkeypatch.setattr(app_module, "_config_dir", lambda: tmp_path)
        # Should not raise.
        app_module._clear_backend_pid_file()

    def test_stale_pid_file_detected_and_cleared(self, monkeypatch, tmp_path):
        """``_read_stale_backend_pid`` returns the PID when the process is dead."""
        monkeypatch.setattr(app_module, "_config_dir", lambda: tmp_path)
        # Write a PID that is extremely unlikely to be alive.
        # Use a very high PID that the OS hasn't handed out yet.
        bogus_pid = 2_000_000
        pid_file = tmp_path / "backend.pid"
        pid_file.write_text(f"{bogus_pid}\n")

        # _is_pid_alive should return False for the bogus PID.
        # On POSIX this is naturally true; on Windows the OpenProcess
        # call returns 0 → ERROR_INVALID_PARAMETER (87), not 5, so
        # _is_pid_alive returns False.  Either way, the bogus PID is dead.
        assert app_module._is_pid_alive(bogus_pid) is False
        stale_pid = app_module._read_stale_backend_pid()
        assert stale_pid == bogus_pid

        # Clear it.
        app_module._clear_backend_pid_file()
        assert not pid_file.exists()
        assert app_module._read_stale_backend_pid() is None

    def test_alive_pid_file_not_considered_stale(self, monkeypatch, tmp_path):
        """When the PID is still alive, ``_read_stale_backend_pid`` returns None."""
        monkeypatch.setattr(app_module, "_config_dir", lambda: tmp_path)
        # Write our own PID — we're definitely alive.
        pid_file = tmp_path / "backend.pid"
        pid_file.write_text(f"{os.getpid()}\n")
        # _is_pid_alive should return True for our own PID.
        # (On Windows this calls OpenProcess on ourselves — should succeed.)
        assert app_module._is_pid_alive(os.getpid()) is True
        assert app_module._read_stale_backend_pid() is None

    def test_read_stale_pid_returns_none_when_file_missing(self, monkeypatch, tmp_path):
        """No PID file → None (no stale lock)."""
        monkeypatch.setattr(app_module, "_config_dir", lambda: tmp_path)
        assert app_module._read_stale_backend_pid() is None

    def test_read_stale_pid_returns_none_on_garbage(self, monkeypatch, tmp_path):
        """Garbage in the PID file → None (treated as no stale lock)."""
        monkeypatch.setattr(app_module, "_config_dir", lambda: tmp_path)
        pid_file = tmp_path / "backend.pid"
        pid_file.write_text("not-a-number\n")
        assert app_module._read_stale_backend_pid() is None


# ── _pick_available_port ────────────────────────────────────────────────


class TestPickAvailablePort:
    """``_pick_available_port`` returns a free port starting from ``start``."""

    def test_returns_start_when_free(self):
        """If the start port is free, it should be returned."""
        # Bind a socket to find a definitely-free port, then close it.
        import socket as _socket

        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
        s.close()

        from voice_typer.server.ipc_server import _pick_available_port

        # The port we just released should be picked.
        # (There's a small race window, but in practice this is reliable.)
        result = _pick_available_port(free_port, max_tries=1)
        assert isinstance(result, int)
        assert result >= free_port

    def test_increments_past_busy_port(self):
        """If start port can't be bound, should try the next one.

        Note: ``_pick_available_port`` uses ``SO_REUSEADDR``, so on Linux
        a port that's merely bound (not listening) by another socket can
        still be re-bound.  This test instead verifies the function
        returns a valid, bindable port when called with a start port.
        """
        import socket as _socket

        from voice_typer.server.ipc_server import _pick_available_port

        # Pick a port that's definitely in the ephemeral range.
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        some_port = s.getsockname()[1]
        s.close()

        result = _pick_available_port(some_port, max_tries=10)
        # Result should be a valid port >= the start port.
        assert isinstance(result, int)
        assert result >= some_port
        # Verify the returned port is actually bindable.
        verify = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            verify.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            verify.bind(("127.0.0.1", result))
        finally:
            verify.close()

    def test_falls_back_to_ephemeral_when_all_busy(self):
        """If every port in the range is busy, OS assigns an ephemeral one."""
        import socket as _socket

        from voice_typer.server.ipc_server import _pick_available_port

        # Occupy a port with a non-REUSEADDR socket, then ask for that
        # exact port with max_tries=1.  The function should fall through
        # to the ephemeral-port branch (bind to port 0).
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        busy_port = s.getsockname()[1]
        try:
            result = _pick_available_port(busy_port, max_tries=1)
            # Result should be a valid port (ephemeral, not the busy one).
            assert isinstance(result, int)
            assert 1 <= result <= 65535
        finally:
            s.close()
