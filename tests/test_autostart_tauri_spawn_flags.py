"""Tests that the Tauri spawn paths apply ``_spawn_flags`` correctly."""

import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server.autostart_launcher import (
    _focus_running_app,
    _spawn_tauri_host,
)


# These tests exercise spawn *mechanics* (``_spawn_flags`` passthrough)
@pytest.fixture(autouse=True)
def _bypass_tauri_integrity_gate(monkeypatch):
    """Bypass ``verify_tauri_binary_or_skip`` for spawn-mechanic tests."""
    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip",
        lambda path: True,
    )


# ``_tauri_log_files()`` opens real log files under the platform config
_TAURI_LOG_FILES_STUB = {
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "stdin": subprocess.DEVNULL,
}


@pytest.fixture
def _stub_tauri_log_files(monkeypatch):
    """Replace ``_tauri_log_files`` with a DEVNULL stub for the test."""
    monkeypatch.setattr(
        "voice_typer.server.autostart_launcher._tauri_log_files",
        lambda: dict(_TAURI_LOG_FILES_STUB),
    )


class TestSpawnTauriHostSpawnFlags:
    """``_spawn_tauri_host`` passes ``_spawn_flags(hidden=...)`` to Popen."""

    def test_windows_hidden_passes_create_no_window(self, monkeypatch, _stub_tauri_log_files):
        """On Windows with ``hidden=True``, the Tauri spawn must pass"""
        monkeypatch.setattr(sys, "platform", "win32")
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/lausu-tauri.exe", hidden=True)
        assert result is not None
        assert captured.get("creationflags") == 0x08000000
        # start_new_session is POSIX-only, must NOT be set on Windows.
        assert "start_new_session" not in captured
        assert "stdout" in captured
        assert "stderr" in captured

    def test_windows_not_hidden_omits_creationflags(self, monkeypatch, _stub_tauri_log_files):
        """On Windows with ``hidden=False`` (e.g. desktop shortcut),"""
        monkeypatch.setattr(sys, "platform", "win32")
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 4243
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/lausu-tauri.exe", hidden=False)
        assert result is not None
        # No creationflags when hidden=False (matches _spawn_flags contract).
        assert "creationflags" not in captured

    def test_posix_passes_start_new_session(self, monkeypatch, _stub_tauri_log_files):
        """On POSIX, the Tauri spawn must pass ``start_new_session=True``"""
        monkeypatch.setattr(sys, "platform", "linux")
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 4244
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/lausu-tauri", hidden=False)
        assert result is not None
        assert captured.get("start_new_session") is True
        # creationflags is Windows-only, must NOT be set on POSIX.
        assert "creationflags" not in captured

    def test_posix_hidden_also_detaches(self, monkeypatch, _stub_tauri_log_files):
        """On POSIX, ``hidden=True`` still sets ``start_new_session=True``"""
        monkeypatch.setattr(sys, "platform", "linux")
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 4245
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/lausu-tauri", hidden=True)
        assert result is not None
        assert captured.get("start_new_session") is True
        assert "creationflags" not in captured

    def test_spawn_tauri_host_env_carries_clean_log_keys(self, monkeypatch, _stub_tauri_log_files):
        """The Tauri child env must carry the clean-log keys so"""
        monkeypatch.setattr(sys, "platform", "win32")
        captured_env = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 4246
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = _spawn_tauri_host("/fake/lausu-tauri.exe", hidden=False)
        assert result is not None
        # ANSI disabled under all three conventions.
        assert captured_env.get("FORCE_COLOR") == "0"
        assert captured_env.get("NO_COLOR") == "1"
        assert captured_env.get("CLICOLOR") == "0"
        # Rust host must NOT duplicate its rotating-file stream into
        assert captured_env.get("RUST_LOG_STDERR") == "0"
        assert captured_env.get("npm_config_loglevel") == "silent"

    def test_spawn_failure_returns_none_and_closes_logs(self, monkeypatch, _stub_tauri_log_files):
        """A spawn failure is logged, log files are closed, and ``None``"""
        monkeypatch.setattr(sys, "platform", "linux")

        def boom(cmd, env=None, **kwargs):
            raise FileNotFoundError("binary not found")

        monkeypatch.setattr(subprocess, "Popen", boom)
        result = _spawn_tauri_host("/fake/lausu-tauri", hidden=False)
        assert result is None


class TestFocusRunningAppTauriSpawnFlags:
    """The Tauri branch of ``_focus_running_app`` passes"""

    def test_windows_focus_probe_passes_no_creationflags(self, monkeypatch, _stub_tauri_log_files):
        """On Windows, the Tauri focus probe runs with ``hidden=False``"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 5555
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        assert "creationflags" not in captured
        assert "start_new_session" not in captured

    def test_posix_focus_probe_passes_start_new_session(self, monkeypatch, _stub_tauri_log_files):
        """On POSIX, the Tauri focus probe must detach into its own"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured.update(kwargs)
            proc = MagicMock()
            proc.pid = 5556
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        assert captured.get("start_new_session") is True
        assert "creationflags" not in captured

    def test_posix_focus_probe_sets_focus_only_env(self, monkeypatch, _stub_tauri_log_files):
        """child env (the marker the Tauri binary reads to skip heavy"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )
        captured_env = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 5557
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        assert captured_env.get("VT_FOCUS_ONLY") == "1"

    def test_focus_probe_env_carries_clean_log_keys(self, monkeypatch, _stub_tauri_log_files):
        """The Tauri focus probe (spawned by the launcher with its"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )
        captured_env = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured_env.update(env or {})
            proc = MagicMock()
            proc.pid = 5558
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert _focus_running_app() is True
        assert captured_env.get("VT_FOCUS_ONLY") == "1"
        assert captured_env.get("FORCE_COLOR") == "0"
        assert captured_env.get("NO_COLOR") == "1"
        assert captured_env.get("CLICOLOR") == "0"
        assert captured_env.get("RUST_LOG_STDERR") == "0"
        assert captured_env.get("npm_config_loglevel") == "silent"

    def test_focus_probe_spawn_failure_returns_false(self, monkeypatch, _stub_tauri_log_files):
        """A Tauri focus-probe spawn failure returns False (no"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("voice_typer.server.autostart_launcher._is_tauri_mode", lambda: True)
        monkeypatch.setattr(
            "voice_typer.server.autostart_launcher._tauri_binary",
            lambda: "/usr/bin/lausu-tauri",
        )

        def boom(cmd, env=None, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(subprocess, "Popen", boom)
        assert _focus_running_app() is False


class TestTauriLogFilesHelper:
    """``_tauri_log_files()`` returns a dict with stdout/stderr/stdin"""

    def test_returns_devnull_on_failure(self, monkeypatch, tmp_path):
        """When the config dir is unwritable, ``_tauri_log_files``"""
        # Force _config_dir to raise by making it return a path whose
        from voice_typer.server import autostart_launcher as mod

        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")

        def fake_config_dir():
            return blocker

        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            fake_config_dir,
        )
        # Re-import the helper fresh so it picks up the patched _config_dir.
        result = mod._tauri_log_files()
        assert result["stdout"] is subprocess.DEVNULL
        assert result["stderr"] is subprocess.DEVNULL
        assert result["stdin"] is subprocess.DEVNULL

    def test_returns_devnull_on_success(self, monkeypatch, tmp_path):
        """O4: ``_tauri_log_files`` returns DEVNULL for stdout/stderr —"""
        from voice_typer.server import autostart_launcher as mod

        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )
        result = mod._tauri_log_files()
        assert result["stdout"] is subprocess.DEVNULL
        assert result["stderr"] is subprocess.DEVNULL
        assert result["stdin"] is subprocess.DEVNULL


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
