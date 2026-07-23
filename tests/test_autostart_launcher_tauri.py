"""Tests for the Tauri-aware autostart launcher.

These tests pin the contract that ``autostart_launcher.py`` spawns the
Tauri binary (``voice-typer-tauri``) instead of ``electron .`` when a
Tauri install is detected — without breaking the legacy Electron path
used by dev checkouts and pre-cutover installs.

The Tauri cutover removed the Electron ``node_modules/`` tree from
production installs, so the legacy ``electron .`` / ``npm run dev``
paths silently fail in production. The launcher now detects the
Tauri binary at well-known install paths (or via the
``VT_TAURI_BINARY`` env override) and spawns it directly; Tauri's
``tauri-plugin-single-instance`` plugin handles focus / fresh-start
deduplication.
"""

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

# ---------------------------------------------------------------------------
# _tauri_binary()
# ---------------------------------------------------------------------------


class TestTauriBinaryLookup:
    """``_tauri_binary()`` locates the Tauri binary at known install paths."""

    def test_returns_none_when_no_env_and_no_install_paths(self, monkeypatch, tmp_path):
        """With no env override and no binary at any standard install path,
        ``_tauri_binary()`` must return ``None`` (the dev/CI case)."""
        # Strip the env override.
        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        # Redirect $HOME to a tmp dir so the user-local candidates
        # (~/.local/bin/voice-typer-tauri, ~/Applications/...) don't
        # accidentally match an existing file on the test box.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert _tauri_binary() is None

    def test_returns_path_from_env_override(self, monkeypatch, tmp_path):
        """``VT_TAURI_BINARY`` env var short-circuits the install-path scan."""
        fake_bin = tmp_path / "voice-typer-tauri"
        fake_bin.write_text("#!/bin/sh\nexit 0\n")
        fake_bin.chmod(0o755)
        monkeypatch.setenv("VT_TAURI_BINARY", str(fake_bin))
        # HOME redirected so user-local candidates don't shadow the env.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert _tauri_binary() == str(fake_bin)

    def test_returns_none_when_env_path_does_not_exist(self, monkeypatch, tmp_path):
        """A non-existent env path is ignored — fall through to install-path
        scan (which also finds nothing in the test env)."""
        monkeypatch.setenv("VT_TAURI_BINARY", str(tmp_path / "nonexistent"))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert _tauri_binary() is None

    def test_returns_path_from_install_paths_linux(self, monkeypatch, tmp_path):
        """On Linux, the binary at ``/usr/bin/voice-typer-tauri`` is found
        when the env override is unset."""
        if sys.platform != "linux":
            pytest.skip("Linux-only install path test")

        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Patch Path.is_file and os.access so we don't need to create
        # an actual file at /usr/bin/voice-typer-tauri (which would
        # require root). We patch the specific path string instead of
        # the global Path.is_file to keep the test focused.
        real_is_file = Path.is_file

        def patched_is_file(self):
            if str(self) == "/usr/bin/voice-typer-tauri":
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", patched_is_file)
        monkeypatch.setattr(os, "access", lambda p, m: True)

        result = _tauri_binary()
        assert result == "/usr/bin/voice-typer-tauri"

    def test_skips_non_executable_posix_candidate(self, monkeypatch, tmp_path):
        """On POSIX, a non-executable file at an install path is skipped
        — a stale non-executable artifact shouldn't fool the launcher."""
        if sys.platform == "win32":
            pytest.skip("POSIX-only executability check")

        stale = tmp_path / "stale-voice-typer-tauri"
        stale.write_text("not executable")
        stale.chmod(0o644)  # no execute bit
        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        monkeypatch.setenv("VT_TAURI_BINARY", str(stale))
        # The env-override path uses Path.is_file (True) but not
        # os.access — the env override is trusted. So this test
        # instead patches the install-path scan path:
        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Pretend /usr/bin/voice-typer-tauri exists but is not executable.
        real_is_file = Path.is_file

        def patched_is_file(self):
            if str(self) == "/usr/bin/voice-typer-tauri":
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", patched_is_file)
        monkeypatch.setattr(os, "access", lambda p, m: False)

        assert _tauri_binary() is None


# ---------------------------------------------------------------------------
# _is_tauri_mode()
# ---------------------------------------------------------------------------


class TestIsTauriMode:
    """``_is_tauri_mode()`` decides whether to take the Tauri path."""

    def test_env_opt_in_forces_tauri_mode(self, monkeypatch):
        """``VT_TAURI_AUTOSTART=1`` forces Tauri mode regardless of the
        local Electron / Tauri binary state."""
        monkeypatch.setenv("VT_TAURI_AUTOSTART", "1")
        # Even if no Tauri binary is resolvable, the env opt-in wins.
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._electron_binary", lambda: "/fake/electron")
        assert _is_tauri_mode() is True

    def test_no_tauri_binary_returns_false(self, monkeypatch):
        """Without a Tauri binary on disk, Tauri mode is OFF — preserves
        the legacy Electron path in dev/CI environments."""
        monkeypatch.delenv("VT_TAURI_AUTOSTART", raising=False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._electron_binary", lambda: None)
        assert _is_tauri_mode() is False

    def test_tauri_binary_and_no_electron_returns_true(self, monkeypatch):
        """Production Tauri install: Tauri binary found, Electron NOT
        found locally → Tauri mode ON."""
        monkeypatch.delenv("VT_TAURI_AUTOSTART", raising=False)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._electron_binary", lambda: None)
        assert _is_tauri_mode() is True

    def test_tauri_binary_and_electron_present_returns_false(self, monkeypatch):
        """Dev checkout: BOTH Tauri binary and local Electron present →
        prefer Electron so the developer exercises the Electron build."""
        monkeypatch.delenv("VT_TAURI_AUTOSTART", raising=False)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._electron_binary",
            lambda: "/fake/node_modules/electron/dist/electron",
        )
        assert _is_tauri_mode() is False


# ---------------------------------------------------------------------------
# _launch_tauri_app()
# ---------------------------------------------------------------------------


class TestLaunchTauriApp:
    """``_launch_tauri_app()`` spawns the Tauri binary with the right env."""

    def test_spawns_tauri_binary_with_hidden_env(self, monkeypatch):
        """When ``hidden=True``, ``VT_START_HIDDEN=1`` must be set in the
        child env (matches the Electron path's contract)."""
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _launch_tauri_app("/fake/voice-typer-tauri", hidden=True)
        assert result is not None
        assert captured["cmd"] == ["/fake/voice-typer-tauri"]
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
        result = _launch_tauri_app("/fake/voice-typer-tauri", hidden=False)
        assert result is not None
        assert captured["env"].get("VT_START_HIDDEN") is None

    def test_returns_none_on_spawn_failure(self, monkeypatch):
        """A spawn failure (FileNotFoundError, OSError, etc.) is logged
        and ``None`` is returned so the caller can fall back."""

        def boom(cmd, env=None, **kwargs):
            raise FileNotFoundError("binary not found")

        monkeypatch.setattr(subprocess, "Popen", boom)
        result = _launch_tauri_app("/fake/voice-typer-tauri", hidden=False)
        assert result is None


# ---------------------------------------------------------------------------
# _focus_running_app() — Tauri path
# ---------------------------------------------------------------------------


class TestFocusRunningAppTauriPath:
    """``_focus_running_app()`` spawns the Tauri binary with
    ``VT_FOCUS_ONLY=1`` when in Tauri mode (Tauri's single-instance
    plugin handles the focus + second-instance-quit dance)."""

    def test_uses_tauri_path_when_in_tauri_mode(self, monkeypatch):
        """When ``_is_tauri_mode()`` is True, the Tauri binary is spawned
        with ``VT_FOCUS_ONLY=1`` in the env (NOT the Electron lean spawn)."""
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
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
        assert captured["cmd"] == ["/usr/bin/voice-typer-tauri"]
        assert captured["env"].get("VT_FOCUS_ONLY") == "1"

    def test_returns_false_when_tauri_mode_but_no_binary(self, monkeypatch):
        """If ``_is_tauri_mode()`` is True but ``_tauri_binary()`` returns
        None (race: binary was removed between detection and spawn),
        return False so the caller can decide what to do."""
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: None)
        assert _focus_running_app() is False

    def test_returns_false_on_tauri_spawn_failure(self, monkeypatch):
        """If the Tauri focus spawn raises, return False (no exception
        propagation — the caller's launch flow continues)."""
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )

        def boom(cmd, env=None, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(subprocess, "Popen", boom)
        assert _focus_running_app() is False


# ---------------------------------------------------------------------------
# launch() — Tauri fresh-start path
# ---------------------------------------------------------------------------


class TestLaunchTauriFreshStart:
    """``launch()`` spawns the Tauri binary on a fresh start when in
    Tauri mode, instead of falling through to ``electron .`` /
    ``npm run dev``."""

    def test_tauri_mode_spawns_tauri_binary_with_hidden(self, monkeypatch):
        """Fresh start in Tauri mode: spawn the Tauri binary with
        ``VT_START_HIDDEN=1`` when ``--hidden`` is in argv."""
        # Bypass the "already running" check.
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        # Force Tauri mode + provide a binary.
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._setup_logging", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._write_pid_file", lambda lp, cp: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        # Bypass the backend-pid-file probe — the test box may have a
        # stale PID file from a previous test.
        from voice_typer.server import app as _app_mod

        class _FakePidFile:
            def exists(self):
                return False

        monkeypatch.setattr(_app_mod, "_backend_pid_file", lambda: _FakePidFile())

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
        assert captured["cmd"] == ["/usr/bin/voice-typer-tauri"]
        assert captured["env"].get("VT_START_HIDDEN") == "1"

    def test_tauri_mode_falls_back_to_electron_when_spawn_fails(self, monkeypatch):
        """If the Tauri spawn fails, the launcher falls back to the
        legacy Electron path (which itself falls back to ``npm run dev``
        when no Electron binary is present)."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/voice-typer-tauri",
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._setup_logging", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._write_pid_file", lambda lp, cp: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._electron_binary", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._main_entry_built", lambda: False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._build_electron", lambda: False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._client_dir_exists", lambda: True)
        # Force npm run dev to be unavailable so the fallback path
        # returns 1 — proving the fallback was taken.
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._spawn_npm_run_dev",
            lambda hidden=False: None,
        )

        from voice_typer.server import app as _app_mod

        class _FakePidFile:
            def exists(self):
                return False

        monkeypatch.setattr(_app_mod, "_backend_pid_file", lambda: _FakePidFile())

        # Tauri spawn fails (Popen raises).
        def boom(cmd, env=None, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(subprocess, "Popen", boom)
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden"])

        # The Tauri path fails → fallback to Electron → Electron binary
        # is None → fallback to npm run dev → which returns None → ret 1.
        ret = launch()
        assert ret == 1


# ---------------------------------------------------------------------------
# launch() — preserves legacy Electron path when NOT in Tauri mode
# ---------------------------------------------------------------------------


class TestLaunchPreservesElectronPath:
    """When ``_is_tauri_mode()`` is False, the legacy Electron path
    runs unchanged (this is the dev/CI scenario)."""

    def test_electron_path_runs_when_not_tauri_mode(self, monkeypatch):
        """If ``_is_tauri_mode()`` is False, the launcher does NOT
        attempt to spawn a Tauri binary — it proceeds to the Electron
        build-first / npm run dev fallback chain."""
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._is_port_open",
            lambda h, p: False,
        )
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._setup_logging", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._write_pid_file", lambda lp, cp: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._client_dir_exists", lambda: True)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._electron_binary", lambda: None)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._main_entry_built", lambda: False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._build_electron", lambda: False)

        from voice_typer.server import app as _app_mod

        class _FakePidFile:
            def exists(self):
                return False

        monkeypatch.setattr(_app_mod, "_backend_pid_file", lambda: _FakePidFile())

        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            proc = MagicMock()
            proc.pid = 7777
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden"])

        ret = launch()
        assert ret == 0
        # The spawned command is from ``npm run dev`` (POSIX list form),
        # NOT the Tauri binary. ``_npm_command`` resolves the full path
        # via ``shutil.which("npm")`` so the first element is either
        # ``npm`` / ``npm.cmd`` (Windows fallback) or the full path
        # (e.g. ``/usr/bin/npm``).
        npm_bin = captured["cmd"][0]
        assert npm_bin.endswith("npm") or npm_bin.endswith("npm.cmd"), f"expected npm binary, got {npm_bin!r}"
        assert captured["env"].get("VT_START_HIDDEN") == "1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
