"""End-to-end logon simulation for packaged autostart (no real reboot)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.server_platform import (
    autostart as autostart_mod,
    platform_flags,
)

RESULT_OK_RE = re.compile(r"RESULT success exit=0 \d+(m \d+)?\.\ds$")


def _app(autostart: bool):
    return SimpleNamespace(config=SimpleNamespace(autostart=autostart))


def _fake_binary(tmp_path: Path, name: str) -> Path:
    import os as _os

    binary = tmp_path / name
    binary.write_bytes(b"fake desktop binary")
    _os.chmod(binary, 0o755)
    return binary


@pytest.fixture
def win32_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform_flags, "SYSTEM", "win32")


@pytest.fixture
def _launcher_noops(monkeypatch):
    """Neutralize launcher side effects (logging setup, pid file)."""
    import voice_typer.server.autostart_launcher as launcher

    monkeypatch.setattr(launcher, "_setup_logging", lambda: None)
    monkeypatch.setattr(launcher, "_write_pid_file", lambda *a: None)


@pytest.fixture
def _no_backend_pid(monkeypatch):
    """The backend PID-file probe sees no live backend."""
    from voice_typer.server import backend_pid as backend_pid_mod

    class _Missing:
        def exists(self):
            return False

    monkeypatch.setattr(backend_pid_mod, "_backend_pid_file", lambda: _Missing())


class TestStaleEntryMigratesOnLogon:
    """A stale previous-generation entry reports disabled, so the startup"""

    def test_stale_command_validates_disabled(self, monkeypatch):
        from voice_typer.server.server_platform.autostart_windows import (
            _validate_runkey_command,
        )

        monkeypatch.setattr(Path, "exists", lambda self: False)
        stale = r'"C:\Deleted\host.exe" "C:\Deleted\launcher.py" --hidden --delay 3'
        assert _validate_runkey_command(stale) is False

    def test_sync_reregisters_and_entry_targets_binary(self, win32_platform, monkeypatch, tmp_path):
        import voice_typer.server.autostart_launcher as launcher
        from voice_typer.server import startup_tasks

        binary = _fake_binary(tmp_path, "voice-typer-tauri.exe")
        # Packaged conditions: no usable interpreter, binary resolvable.
        monkeypatch.setattr(autostart_mod, "_prefer_pythonw", lambda p: str(tmp_path / "missing-pythonw.exe"))
        monkeypatch.setattr(autostart_mod, "_probe_system_python", lambda name: None)
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        monkeypatch.setattr(launcher, "_tauri_binary", lambda: str(binary))

        # The stale entry reads as disabled...
        monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
        # ...so sync enables, and the "written entry" captures the real
        written = {}

        def _fake_enable():
            written["entry"] = autostart_mod._autostart_command()
            return True

        monkeypatch.setattr(autostart_mod, "enable_autostart", _fake_enable)
        monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)

        result = startup_tasks.sync_autostart(_app(True))

        assert result["registered"] is True
        assert result["actual_post_sync"] is True
        assert str(binary) in written["entry"]
        assert "\\\\" not in written["entry"]

    def test_live_launcher_entry_migrates_to_direct_binary(self, win32_platform, monkeypatch, tmp_path):
        """Old python-launcher entry + installed binary → sync migrates it.

        The previous-generation entry points at live files (validates
        True on a pure-dev machine), but with a packaged install present
        the validator reports superseded-stale, so the SAME sync cycle
        re-registers a direct-binary entry. This is the Electron→Tauri
        upgrader path: no manual toggle needed.
        """
        import types
        from unittest.mock import MagicMock

        import voice_typer.server.autostart_launcher as launcher
        from voice_typer.server import startup_tasks
        from voice_typer.server.server_platform import (
            autostart_windows as autostart_windows_mod,
        )

        binary = _fake_binary(tmp_path, "voice-typer-tauri.exe")
        launcher_py = tmp_path / "autostart_launcher.py"
        launcher_py.write_text("# launcher")
        venv_python = tmp_path / "pythonw.exe"
        venv_python.write_bytes(b"x")
        old_value = f'"{venv_python}" "{launcher_py}" --hidden --delay 3'

        fake_winreg = types.ModuleType("winreg")
        fake_winreg.HKEY_CURRENT_USER = 0x80000001
        fake_winreg.KEY_SET_VALUE = 0x0002
        fake_winreg.KEY_READ = 0x20019
        fake_winreg.OpenKey = MagicMock(return_value=MagicMock())
        fake_winreg.QueryValueEx = MagicMock(return_value=(old_value, 1))
        fake_winreg.DeleteValue = MagicMock()
        fake_winreg.CloseKey = MagicMock()
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

        monkeypatch.setattr(autostart_windows_mod, "_is_app_autostart_task_registered", lambda: False)
        monkeypatch.setattr(autostart_windows_mod, "_is_app_autostart_startup_registered", lambda: False)
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        monkeypatch.setattr(launcher, "_tauri_binary", lambda: str(binary))
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        # Real validator sees the live-but-superseded entry → disabled.
        assert autostart_mod.is_autostart_enabled() is False

        written = {}
        monkeypatch.setattr(
            autostart_windows_mod,
            "_register_app_autostart_task",
            lambda: (written.setdefault("task", True), True)[1],
        )

        result = startup_tasks.sync_autostart(_app(True))

        assert result["registered"] is True
        assert result["actual_post_sync"] is True
        assert written.get("task") is True


class TestLauncherLogonHiddenSpawn:
    """``main()`` with ``--hidden`` spawns the desktop binary hidden and"""

    def test_hidden_env_and_result_success_line(self, _launcher_noops, _no_backend_pid, monkeypatch, tmp_path, caplog):
        import voice_typer.server.autostart_launcher as launcher

        binary = _fake_binary(tmp_path, "voice-typer-tauri")
        monkeypatch.setattr(launcher, "_is_tauri_mode", lambda: True)
        monkeypatch.setattr(launcher, "_tauri_binary", lambda: str(binary))
        monkeypatch.setattr(launcher, "verify_tauri_binary_or_skip", lambda path: True)
        monkeypatch.setattr(launcher, "_is_port_open", lambda h, p: False)
        monkeypatch.setattr(launcher, "_read_ipc_port_from_pid_file", lambda: None)
        monkeypatch.setattr(launcher, "_wait_for_ipc_ready", lambda *a, **k: True)

        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = dict(env or {})
            proc = MagicMock()
            proc.pid = 4242
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden"])

        with caplog.at_level(logging.INFO, logger="voice_typer.server.autostart_launcher"):
            rc = launcher.main()

        assert rc == 0
        assert captured["cmd"] == [str(binary)]
        assert captured["env"].get("VT_START_HIDDEN") == "1"
        messages = [r.getMessage() for r in caplog.records]
        assert any("[AUTOSTART] launcher starting (pid=" in m for m in messages)
        assert any(RESULT_OK_RE.search(m) for m in messages), messages

    def test_logger_uses_dotted_name(self):
        import voice_typer.server.autostart_launcher as launcher

        assert launcher.log.name == "voice_typer.server.autostart_launcher"

    def test_unhandled_exception_records_failure_line(self, _launcher_noops, monkeypatch, caplog):
        import voice_typer.server.autostart_launcher as launcher

        def _boom():
            raise RuntimeError("logon storm")

        monkeypatch.setattr(launcher, "launch", _boom)
        with caplog.at_level(logging.INFO, logger="voice_typer.server.autostart_launcher"):
            rc = launcher.main()
        assert rc == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("RESULT failure unhandled-exception" in m for m in messages)


class TestManifestGateEndToEnd:
    """The integrity gate refuses an empty per-arch hash and passes a"""

    def _manifest(self, tmp_path, binary_name, sha):
        from voice_typer.server.autostart_launcher import _tauri_manifest_key

        manifest = {
            "version": 1,
            "binaries": {
                binary_name: {
                    "sha256": {_tauri_manifest_key(): sha},
                    "_platforms": [],
                    "_install_paths": [],
                }
            },
        }
        path = tmp_path / "tauri-binaries.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return str(path)

    def test_empty_hash_refuses(self, monkeypatch, tmp_path):
        from voice_typer.server.autostart_launcher import verify_tauri_binary_or_skip

        binary = _fake_binary(tmp_path, "voice-typer-tauri")
        monkeypatch.setenv("VT_TAURI_MANIFEST", self._manifest(tmp_path, "voice-typer-tauri", ""))
        assert verify_tauri_binary_or_skip(binary) is False

    def test_populated_hash_passes(self, monkeypatch, tmp_path):
        from voice_typer.server.autostart_launcher import verify_tauri_binary_or_skip

        binary = _fake_binary(tmp_path, "voice-typer-tauri")
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        monkeypatch.setenv("VT_TAURI_MANIFEST", self._manifest(tmp_path, "voice-typer-tauri", sha))
        assert verify_tauri_binary_or_skip(binary) is True


class TestLogonIdempotentWhenBackendRunning:
    """A second logon while the backend lives focuses it; nothing is"""

    def test_focus_path_spawns_nothing(self, _launcher_noops, _no_backend_pid, monkeypatch):
        import voice_typer.server.autostart_launcher as launcher

        monkeypatch.setattr(launcher, "_is_port_open", lambda h, p: True)
        focused = []
        monkeypatch.setattr(launcher, "_focus_running_app", lambda: focused.append(1) or True)

        def _must_not_spawn(*a, **k):
            raise AssertionError("no spawn expected on the focus path")

        monkeypatch.setattr(launcher, "_spawn_tauri_host", _must_not_spawn)
        monkeypatch.setattr(sys, "argv", ["autostart_launcher.py", "--hidden"])

        assert launcher.launch() == 0
        assert focused == [1]
