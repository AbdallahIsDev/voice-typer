"""Tests for the Tauri-aware autostart launcher."""

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.autostart_launcher import (
    _focus_running_app,
    _is_tauri_mode,
    _launch_tauri_app,
    _tauri_binary,
    launch,
)


# These tests exercise spawn *mechanics* (env, flags, fallback order)
@pytest.fixture(autouse=True)
def _bypass_tauri_integrity_gate(monkeypatch):
    """Bypass ``verify_tauri_binary_or_skip`` for spawn-mechanic tests."""
    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
        lambda path: True,
    )


class TestTauriBinaryLookup:
    """``_tauri_binary()`` locates the Tauri binary at known install paths."""

    def test_returns_none_when_no_env_and_no_install_paths(self, monkeypatch, tmp_path):
        """With no env override and no binary at any standard install path,"""
        # Strip the env override.
        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        # Redirect $HOME to a tmp dir so the user-local candidates
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert _tauri_binary() is None

    def test_returns_path_from_env_override(self, monkeypatch, tmp_path):
        """``VT_TAURI_BINARY`` env var short-circuits the install-path scan."""
        fake_bin = tmp_path / "lausu-tauri"
        fake_bin.write_text("#!/bin/sh\nexit 0\n")
        fake_bin.chmod(0o755)
        monkeypatch.setenv("VT_TAURI_BINARY", str(fake_bin))
        # HOME redirected so user-local candidates don't shadow the env.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert _tauri_binary() == str(fake_bin)

    def test_returns_none_when_env_path_does_not_exist(self, monkeypatch, tmp_path):
        """A non-existent env path is ignored, fall through to install-path"""
        monkeypatch.setenv("VT_TAURI_BINARY", str(tmp_path / "nonexistent"))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert _tauri_binary() is None

    @pytest.mark.skipif(
        sys.platform != "linux",
        reason="Linux-only: verifies the /usr/bin/lausu-tauri install-path scan (POSIX-specific)",
    )
    def test_returns_path_from_install_paths_linux(self, monkeypatch, tmp_path):
        """On Linux, the binary at ``/usr/bin/lausu-tauri`` is found"""
        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Patch Path.is_file and os.access so we don't need to create
        real_is_file = Path.is_file

        def patched_is_file(self):
            if str(self) == "/usr/bin/lausu-tauri":
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", patched_is_file)
        monkeypatch.setattr(os, "access", lambda p, m: True)

        result = _tauri_binary()
        assert result == "/usr/bin/lausu-tauri"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only: Windows has no executable bit, the os.access(X_OK) check has no equivalent on Win32",
    )
    def test_skips_non_executable_posix_candidate(self, monkeypatch, tmp_path):
        """On POSIX, a non-executable file at an install path is skipped"""
        stale = tmp_path / "stale-lausu-tauri"
        stale.write_text("not executable")
        stale.chmod(0o644)  # no execute bit
        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        monkeypatch.setenv("VT_TAURI_BINARY", str(stale))
        # The env-override path uses Path.is_file (True) but not
        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Pretend /usr/bin/lausu-tauri exists but is not executable.
        real_is_file = Path.is_file

        def patched_is_file(self):
            if str(self) == "/usr/bin/lausu-tauri":
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", patched_is_file)
        monkeypatch.setattr(os, "access", lambda p, m: False)

        assert _tauri_binary() is None


class TestIsTauriMode:
    """``_is_tauri_mode()`` decides whether to take the Tauri path."""

    def test_env_opt_in_forces_tauri_mode(self, monkeypatch):
        """``VT_TAURI_AUTOSTART=1`` forces Tauri mode regardless of the"""
        monkeypatch.setenv("VT_TAURI_AUTOSTART", "1")
        # Even if no Tauri binary is resolvable, the env opt-in wins.
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: None)
        assert _is_tauri_mode() is True

    def test_no_tauri_binary_returns_false(self, monkeypatch):
        """Without a Tauri binary on disk (and no env/executable signal),"""
        monkeypatch.delenv("VT_TAURI_AUTOSTART", raising=False)
        monkeypatch.delenv("VOICE_TYPER_TAURI", raising=False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: None)
        # Ensure sys.executable is not the Tauri host.
        monkeypatch.setattr(sys, "executable", "C:/Python/python.exe")
        assert _is_tauri_mode() is False

    def test_tauri_binary_returns_true(self, monkeypatch):
        """A Tauri binary found at an install path is sufficient (no"""
        monkeypatch.delenv("VT_TAURI_AUTOSTART", raising=False)
        monkeypatch.delenv("VOICE_TYPER_TAURI", raising=False)
        monkeypatch.setattr(sys, "executable", "C:/Python/python.exe")
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )
        assert _is_tauri_mode() is True

    def test_tauri_sidecar_executable_returns_true(self, monkeypatch):
        """When sys.executable is the Tauri host binary, mode is ON."""
        monkeypatch.delenv("VT_TAURI_AUTOSTART", raising=False)
        monkeypatch.delenv("VOICE_TYPER_TAURI", raising=False)
        monkeypatch.setattr(sys, "executable", "/opt/Lausu/lausu-tauri")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: None)
        assert _is_tauri_mode() is True


class TestLaunchTauriApp:
    """``_launch_tauri_app()`` spawns the Tauri binary with the right env."""

    def test_spawns_tauri_binary_with_hidden_env(self, monkeypatch):
        """When ``hidden=True``, ``VT_START_HIDDEN=1`` must be set in the"""
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _launch_tauri_app("/fake/lausu-tauri", hidden=True)
        assert result is not None
        assert captured["cmd"] == ["/fake/lausu-tauri"]
        assert captured["env"].get("VT_START_HIDDEN") == "1"

    def test_spawns_tauri_binary_without_hidden_env(self, monkeypatch):
        """When ``hidden=False``, ``VT_START_HIDDEN`` must NOT be set."""
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _launch_tauri_app("/fake/lausu-tauri", hidden=False)
        assert result is not None
        assert captured["env"].get("VT_START_HIDDEN") is None

    def test_returns_none_on_spawn_failure(self, monkeypatch):
        """A spawn failure (FileNotFoundError, OSError, etc.) is logged"""

        def boom(cmd, env=None, **kwargs):
            raise FileNotFoundError("binary not found")

        monkeypatch.setattr(subprocess, "Popen", boom)
        result = _launch_tauri_app("/fake/lausu-tauri", hidden=False)
        assert result is None


class TestFocusRunningAppTauriPath:
    """``VT_FOCUS_ONLY=1`` when in Tauri mode (Tauri's single-instance"""

    def test_uses_tauri_path_when_in_tauri_mode(self, monkeypatch):
        """When ``_is_tauri_mode()`` is True, the Tauri binary is spawned"""
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            proc = MagicMock()
            proc.pid = 5555
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        assert captured["cmd"] == ["/usr/bin/lausu-tauri"]
        assert captured["env"].get("VT_FOCUS_ONLY") == "1"

    def test_returns_false_when_tauri_mode_but_no_binary(self, monkeypatch):
        """If ``_is_tauri_mode()`` is True but ``_tauri_binary()`` returns"""
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: None)
        assert _focus_running_app() is False

    def test_returns_false_on_tauri_spawn_failure(self, monkeypatch):
        """If the Tauri focus spawn raises, return False (no exception"""
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )

        def boom(cmd, env=None, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(subprocess, "Popen", boom)
        assert _focus_running_app() is False


class TestLaunchTauriFreshStart:
    """Tauri mode, instead of falling through to the legacy host launch /"""

    def test_tauri_mode_spawns_tauri_binary_with_hidden(self, monkeypatch):
        """Fresh start in Tauri mode: spawn the Tauri binary with"""
        # Bypass the "already running" check.
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        # Force Tauri mode + provide a binary.
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._setup_logging", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._write_pid_file", lambda lp, cp: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        from voice_typer.server import backend_pid as _backend_pid_mod

        class _FakePidFile:
            def exists(self):
                return False

        monkeypatch.setattr(_backend_pid_mod, "_backend_pid_file", lambda: _FakePidFile())

        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            proc = MagicMock()
            proc.pid = 9999
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden"])

        ret = launch()
        assert ret == 0
        assert captured["cmd"] == ["/usr/bin/lausu-tauri"]
        assert captured["env"].get("VT_START_HIDDEN") == "1"

    def test_tauri_mode_exits_one_when_spawn_fails(self, monkeypatch):
        """If the Tauri spawn fails, the launcher exits 1. There is no"""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._setup_logging", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._write_pid_file", lambda lp, cp: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        # Patch the OWNING module (the runtime-neutral PID-file leaf)
        from voice_typer.server import backend_pid as _backend_pid_mod

        class _FakePidFile:
            def exists(self):
                return False

        monkeypatch.setattr(_backend_pid_mod, "_backend_pid_file", lambda: _FakePidFile())

        # Tauri spawn fails (Popen raises).
        def boom(cmd, env=None, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(subprocess, "Popen", boom)
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden"])

        ret = launch()
        assert ret == 1


class TestLaunchWithoutTauriModeExitsOne:
    """When ``_is_tauri_mode()`` is False and no Tauri binary is"""

    def test_no_tauri_mode_exits_one(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._setup_logging", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._write_pid_file", lambda lp, cp: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        from voice_typer.server import backend_pid as _backend_pid_mod

        class _FakePidFile:
            def exists(self):
                return False

        monkeypatch.setattr(_backend_pid_mod, "_backend_pid_file", lambda: _FakePidFile())
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden"])

        ret = launch()
        assert ret == 1


class TestLaunchAlreadyRunningFocus:
    """existing instance and returns WITHOUT a fixed pre-exit sleep."""

    def test_focus_path_returns_without_sleep(self, monkeypatch):
        pid_file = (
            Path("/tmp/vt-test-nonexistent-pid-file") if os.name != "nt" else Path("C:/nonexistent/vt-test-pid-file")
        )
        monkeypatch.setattr(
            "voice_typer.server.single_instance._backend_pid_file",
            lambda: pid_file,
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: True,  # backend "already running"
        )
        focused = []
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._focus_running_app",
            lambda: focused.append(1) or True,
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._setup_logging", lambda: None)

        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py"])
        ret = launch()

        assert ret == 0
        assert focused, "focus probe must be spawned when the backend is already running"
        assert sleeps == [], (
            "the already-running focus path must NOT sleep before exiting "
            f"(removed 0.5s login delay); got time.sleep calls: {sleeps!r}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
