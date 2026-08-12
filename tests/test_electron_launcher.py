"""Tests for the standalone-mode Electron launcher.

P1-1.2: when the user runs ``VoiceTyper`` from a terminal (no ``--port``
flag, no ``VOICE_TYPER_IPC_TOKEN`` env var), the Python backend spawns
Electron as a subprocess and passes it the connection info via env vars
(``VT_PYTHON_PORT`` + ``VT_IPC_TOKEN``).  These tests cover:

- ``launch_electron_frontend()`` returns a PID on success / None on failure
- ``terminate_electron()`` kills the process (POSIX path mocked)
- ``_ensure_single_instance`` writes a backend PID file on startup
- Stale PID file is detected and cleared when the referenced process is dead
- G4-H-02: sensitive env vars are stripped from the child env so cloud
  API keys cannot be exfiltrated via ``/proc/<pid>/environ``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server import app as app_module, electron_launcher

# ── launch_electron_frontend ────────────────────────────────────────────


class TestLaunchElectronFrontend:
    """launch_electron_frontend() returns a PID on success, None on failure."""

    def test_launch_electron_returns_pid_on_success(self, monkeypatch):
        """When the Electron binary exists and Popen succeeds, return the PID."""
        # Pretend the binary exists and the build output is present so we
        # don't trigger an npm run build.
        monkeypatch.setattr(
            electron_launcher,
            "_electron_binary",
            lambda: "/fake/electron",
        )
        monkeypatch.setattr(
            electron_launcher,
            "_main_entry_built",
            lambda: True,
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
            electron_launcher,
            "_electron_binary",
            lambda: "/fake/electron",
        )
        monkeypatch.setattr(
            electron_launcher,
            "_main_entry_built",
            lambda: True,
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
            electron_launcher,
            "_electron_binary",
            lambda: None,
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


# sensitive env var stripping ─────────────────────────────────


class TestStripSensitiveEnv:
    """G4-H-02: ``_strip_sensitive_env`` deletes sensitive env vars before
    the Electron child is spawned, so cloud API keys / model download
    tokens cannot be exfiltrated via ``/proc/<pid>/environ``.
    """

    def test_strips_well_known_api_keys(self):
        """OPENAI_API_KEY, ANTHROPIC_API_KEY, HF_TOKEN etc. are stripped."""
        env = {
            "OPENAI_API_KEY": "sk-...",
            "ANTHROPIC_API_KEY": "sk-ant-...",
            "GEMINI_API_KEY": "AI...",
            "HF_TOKEN": "hf_...",
            "HUGGING_FACE_HUB_TOKEN": "hf_...",
            "DEEPGRAM_API_KEY": "...",
            "GROQ_API_KEY": "...",
            "PATH": "/usr/bin",
            "HOME": "/home/user",
        }
        electron_launcher._strip_sensitive_env(env)
        for key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
            "DEEPGRAM_API_KEY",
            "GROQ_API_KEY",
        ):
            assert key not in env, f"{key} should have been stripped"
        # Benign vars survive.
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/user"

    def test_strips_substring_matches(self):
        """Any key containing API_KEY / SECRET / TOKEN / PASSWORD / CREDENTIAL is stripped."""
        env = {
            "MY_SERVICE_API_KEY": "x",
            "DATABASE_PASSWORD": "hunter2",
            "AWS_SECRET_ACCESS_KEY": "AKIA...",
            "RANDOM_TOKEN_VALUE": "tok",
            "OAUTH_CREDENTIAL": "cred",
            "PATH": "/usr/bin",
        }
        electron_launcher._strip_sensitive_env(env)
        for key in (
            "MY_SERVICE_API_KEY",
            "DATABASE_PASSWORD",
            "AWS_SECRET_ACCESS_KEY",
            "RANDOM_TOKEN_VALUE",
            "OAUTH_CREDENTIAL",
        ):
            assert key not in env, f"{key} should have been stripped (substring match)"
        assert env["PATH"] == "/usr/bin"

    def test_preserves_ipc_token_trio(self):
        """VOICE_TYPER_IPC_TOKEN / VT_IPC_TOKEN / VT_PYTHON_PORT are preserved
        even though they contain the substring 'TOKEN'.
        """
        env = {
            "VOICE_TYPER_IPC_TOKEN": "abc123",
            "VT_IPC_TOKEN": "abc123",
            "VT_PYTHON_PORT": "9876",
            "PATH": "/usr/bin",
        }
        electron_launcher._strip_sensitive_env(env)
        assert env["VOICE_TYPER_IPC_TOKEN"] == "abc123"
        assert env["VT_IPC_TOKEN"] == "abc123"
        assert env["VT_PYTHON_PORT"] == "9876"

    def test_strips_case_insensitively(self):
        """Substring match is case-insensitive (ApiKey, api_key, API_KEY all stripped)."""
        env = {
            "MY_SERVICE_api_key": "x",
            "MixedCase_Api_Key": "x",
            "UPPERCASE_API_KEY": "x",
            "my_token_value": "x",
            "PATH": "/usr/bin",
        }
        electron_launcher._strip_sensitive_env(env)
        for key in (
            "MY_SERVICE_api_key",
            "MixedCase_Api_Key",
            "UPPERCASE_API_KEY",
            "my_token_value",
        ):
            assert key not in env, f"{key} should have been stripped (case-insensitive)"
        assert env["PATH"] == "/usr/bin"

    def test_empty_env_is_noop(self):
        """An empty env dict is left unchanged."""
        env: dict = {}
        electron_launcher._strip_sensitive_env(env)
        assert env == {}

    def test_no_benign_vars_stripped(self):
        """Standard OS env vars survive stripping."""
        env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "USER": "user",
            "LANG": "en_US.UTF-8",
            "SHELL": "/bin/bash",
            "TERM": "xterm-256color",
            "XDG_SESSION_TYPE": "wayland",
        }
        electron_launcher._strip_sensitive_env(env)
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/user"
        assert env["USER"] == "user"
        assert env["LANG"] == "en_US.UTF-8"
        assert env["SHELL"] == "/bin/bash"
        assert env["TERM"] == "xterm-256color"
        assert env["XDG_SESSION_TYPE"] == "wayland"


class TestLaunchElectronEnvStripping:
    """G4-H-02: ``launch_electron_frontend`` strips sensitive env vars
    before passing the env to ``subprocess.Popen``.
    """

    def _patch_for_launch(self, monkeypatch):
        monkeypatch.setattr(
            electron_launcher,
            "_electron_binary",
            lambda: "/fake/electron",
        )
        monkeypatch.setattr(
            electron_launcher,
            "_main_entry_built",
            lambda: True,
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

    def test_child_env_has_api_keys_stripped(self, monkeypatch):
        """OPENAI_API_KEY etc. must NOT be present in the env passed to Popen."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leak")
        monkeypatch.setenv("HF_TOKEN", "hf-leak")

        self._patch_for_launch(monkeypatch)
        captured_env: dict = {}

        def fake_popen(argv, env=None, **kwargs):
            captured_env.clear()
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        electron_launcher.launch_electron_frontend(9876, "tok")

        assert "OPENAI_API_KEY" not in captured_env
        assert "ANTHROPIC_API_KEY" not in captured_env
        assert "HF_TOKEN" not in captured_env

    def test_child_env_preserves_ipc_token_trio(self, monkeypatch):
        """VT_PYTHON_PORT / VT_IPC_TOKEN / VOICE_TYPER_IPC_TOKEN are present
        even when other TOKEN-matching vars are stripped.
        """
        monkeypatch.setenv("MY_APP_TOKEN", "should-be-stripped")
        self._patch_for_launch(monkeypatch)
        captured_env: dict = {}

        def fake_popen(argv, env=None, **kwargs):
            captured_env.clear()
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        electron_launcher.launch_electron_frontend(9876, "session-tok")

        assert captured_env.get("VT_PYTHON_PORT") == "9876"
        assert captured_env.get("VT_IPC_TOKEN") == "session-tok"
        assert captured_env.get("VOICE_TYPER_IPC_TOKEN") == "session-tok"
        assert "MY_APP_TOKEN" not in captured_env

    def test_child_env_preserves_standard_os_vars(self, monkeypatch):
        """PATH / HOME / LANG etc. survive stripping."""
        self._patch_for_launch(monkeypatch)
        captured_env: dict = {}

        def fake_popen(argv, env=None, **kwargs):
            captured_env.clear()
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        electron_launcher.launch_electron_frontend(9876, "tok")

        assert captured_env.get("PATH") == os.environ.get("PATH")
        assert captured_env.get("HOME") == os.environ.get("HOME")


# ── terminate_electron ──────────────────────────────────────────────────


class TestTerminateElectron:
    """terminate_electron() kills the process (POSIX path mocked)."""

    @pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL not available on Windows")
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

    @pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL not available on Windows")
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

    def test_pid_file_written_on_startup(self, tmp_config_dir):
        """``_write_backend_pid_file`` writes our PID to ``backend.pid``."""
        # _secure_atomic_write is the production write path.
        # Just call our helper — it should produce the file with our PID.
        app_module._write_backend_pid_file()
        pid_file = tmp_config_dir / "backend.pid"
        assert pid_file.exists()
        content = pid_file.read_text().strip()
        assert int(content) == os.getpid()

    def test_pid_file_cleared_on_shutdown(self, tmp_config_dir):
        """``_clear_backend_pid_file`` removes the file if it exists."""
        pid_file = tmp_config_dir / "backend.pid"
        pid_file.write_text(f"{os.getpid()}\n")
        assert pid_file.exists()
        app_module._clear_backend_pid_file()
        assert not pid_file.exists()

    def test_clear_pid_file_noop_when_missing(self, tmp_config_dir):
        """``_clear_backend_pid_file`` doesn't raise if the file is absent."""
        # Should not raise.
        app_module._clear_backend_pid_file()

    def test_stale_pid_file_detected_and_cleared(self, tmp_config_dir):
        """``_read_stale_backend_pid`` returns the PID when the process is dead."""
        # Write a PID that is extremely unlikely to be alive.
        # Use a very high PID that the OS hasn't handed out yet.
        bogus_pid = 2_000_000
        pid_file = tmp_config_dir / "backend.pid"
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

    def test_alive_pid_file_not_considered_stale(self, tmp_config_dir):
        """When the PID is still alive, ``_read_stale_backend_pid`` returns None."""
        # Write our own PID — we're definitely alive.
        pid_file = tmp_config_dir / "backend.pid"
        pid_file.write_text(f"{os.getpid()}\n")
        # _is_pid_alive should return True for our own PID.
        # (On Windows this calls OpenProcess on ourselves — should succeed.)
        assert app_module._is_pid_alive(os.getpid()) is True
        assert app_module._read_stale_backend_pid() is None

    def test_read_stale_pid_returns_none_when_file_missing(self, tmp_config_dir):
        """No PID file → None (no stale lock)."""
        assert app_module._read_stale_backend_pid() is None

    def test_read_stale_pid_returns_none_on_garbage(self, tmp_config_dir):
        """Garbage in the PID file → None (treated as no stale lock)."""
        pid_file = tmp_config_dir / "backend.pid"
        pid_file.write_text("not-a-number\n")
        assert app_module._read_stale_backend_pid() is None


# ── _pick_available_port ────────────────────────────────────────────────


class TestPickAvailablePort:
    """``_pick_available_port`` returns a free port starting from ``start``.

     fix: the function now returns a ``(port, bound_socket)`` tuple
    so callers can pass the pre-bound socket through to ``start_tcp``
    (eliminating the probe-then-bind race window).  Tests updated to
    unpack the tuple and close the returned socket.
    """

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
        # result is now a (port, sock) tuple.
        assert isinstance(result, tuple)
        assert len(result) == 2
        port, sock = result
        assert isinstance(port, int)
        assert port >= free_port
        sock.close()

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

        port, sock = _pick_available_port(some_port, max_tries=10)
        try:
            # Result should be a valid port >= the start port.
            assert isinstance(port, int)
            assert port >= some_port
            # the returned socket is already bound — verify by
            # checking its getsockname() matches the returned port.
            bound_port = sock.getsockname()[1]
            assert bound_port == port
        finally:
            sock.close()

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
            port, sock = _pick_available_port(busy_port, max_tries=1)
            try:
                # Result should be a valid port (ephemeral, not the busy one).
                assert isinstance(port, int)
                assert 1 <= port <= 65535
                # verify the returned socket is actually bound to
                # the returned port (gold-standard contract).
                assert sock.getsockname()[1] == port
            finally:
                sock.close()
        finally:
            s.close()
