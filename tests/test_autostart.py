"""Tests for the Windows autostart robustness fixes (EY-319 / TX-46).

Covers:
  - ``_autostart_command()`` validates the python_bin path exists and
    falls back to the Tauri binary when no Python interpreter is
    available.
  - ``_is_app_autostart_runkey_registered()`` returns False when the
    stored command points at a nonexistent file (stale entry detection).
  - ``_is_app_autostart_runkey_registered()`` cleans up stale entries.
  - ``_is_app_autostart_task_registered()`` returns False when the
    task's ``<Command>`` path doesn't exist (stale task detection).
  - ``_extract_command_from_task_xml()`` parses the Task Scheduler XML.
  - ``_validate_runkey_command()`` validates a Run-key command line.
  - Windows Startup-folder ``.bat`` fallback:
      - ``_register_app_autostart_startup()`` writes the .bat.
      - ``_unregister_app_autostart_startup()`` deletes the .bat.
      - ``_is_app_autostart_startup_registered()`` validates the .bat.
  - ``_enable_autostart_windows()`` falls back to the Startup folder.
  - ``_disable_autostart_windows()`` removes all three mechanisms.
  - ``_is_autostart_windows()`` checks all three mechanisms.

Tests use ``unittest.mock.patch`` for ``winreg``, ``shutil.which``,
``os.path.exists``, ``subprocess.run`` etc. so they run on the Linux
test host. Windows-host validation is documented as
"VALIDATE ON WINDOWS HOST" with exact commands.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures: fake winreg + win32 platform
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly."""
    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = 0x80000001
    fake.KEY_SET_VALUE = 0x0002
    fake.KEY_READ = 0x20019
    fake.KEY_ALL_ACCESS = 0xF003F
    fake.REG_SZ = 1
    fake.OpenKey = MagicMock(return_value=MagicMock())
    fake.SetValueEx = MagicMock()
    fake.QueryValueEx = MagicMock(return_value=("cmd", 1))
    fake.DeleteValue = MagicMock()
    fake.CloseKey = MagicMock()
    fake.EnumValue = MagicMock(side_effect=OSError("no more values"))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake


@pytest.fixture
def win32_platform(monkeypatch, fake_winreg):
    """Pretend we're on Windows for the duration of the test."""
    monkeypatch.setattr(sys, "platform", "win32")
    from voice_typer.server import server_platform

    monkeypatch.setattr(server_platform, "SYSTEM", "win32")
    return server_platform


# ---------------------------------------------------------------------------
# _validate_runkey_command
# ---------------------------------------------------------------------------


class TestValidateRunkeyCommand:
    """``_validate_runkey_command`` validates a Run-key command line."""

    def test_quoted_existing_path_is_valid(self, monkeypatch):
        from voice_typer.server.server_platform.autostart_windows import _validate_runkey_command

        existing = {r"C:\Python\pythonw.exe"}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)
        value = r'"C:\Python\pythonw.exe" "C:\app\launcher.py" --hidden --delay 15'
        assert _validate_runkey_command(value) is True

    def test_quoted_nonexistent_path_is_invalid(self, monkeypatch):
        from voice_typer.server.server_platform.autostart_windows import _validate_runkey_command

        monkeypatch.setattr(Path, "exists", lambda self: False)
        value = r'"C:\Deleted\pythonw.exe" "C:\app\launcher.py" --hidden --delay 15'
        assert _validate_runkey_command(value) is False

    def test_unquoted_single_token_existing_is_valid(self, monkeypatch):
        from voice_typer.server.server_platform.autostart_windows import _validate_runkey_command

        existing = {r"C:\Python\pythonw.exe"}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)
        value = r"C:\Python\pythonw.exe"
        assert _validate_runkey_command(value) is True

    def test_unquoted_single_token_nonexistent_is_invalid(self, monkeypatch):
        from voice_typer.server.server_platform.autostart_windows import _validate_runkey_command

        monkeypatch.setattr(Path, "exists", lambda self: False)
        value = r"C:\Deleted\pythonw.exe"
        assert _validate_runkey_command(value) is False

    def test_unquoted_spaced_path_is_preserved(self, monkeypatch):
        """Ambiguous unquoted spaced paths are preserved (CONSERVATIVE-DELETE)."""
        from voice_typer.server.server_platform.autostart_windows import _validate_runkey_command

        monkeypatch.setattr(Path, "exists", lambda self: False)
        value = r"C:\Program Files\VoiceTyper\app.exe --delay 15"
        # Ambiguous — can't determine the full exe path, so preserve.
        assert _validate_runkey_command(value) is True

    def test_empty_value_is_valid(self):
        """Empty/None values are treated as valid (caller checks truthy)."""
        from voice_typer.server.server_platform.autostart_windows import _validate_runkey_command

        assert _validate_runkey_command("") is True
        assert _validate_runkey_command(None) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _extract_command_from_task_xml
# ---------------------------------------------------------------------------


class TestExtractCommandFromTaskXml:
    """``_extract_command_from_task_xml`` parses Task Scheduler XML."""

    def test_extracts_command_from_namespaced_xml(self):
        from voice_typer.server.server_platform.autostart_windows import _extract_command_from_task_xml

        xml = (
            '<?xml version="1.0"?>'
            '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            "<Actions><Exec>"
            "<Command>C:\\Python\\pythonw.exe</Command>"
            "<Arguments>-m voice_typer.server.prewarm</Arguments>"
            "</Exec></Actions>"
            "</Task>"
        )
        assert _extract_command_from_task_xml(xml) == r"C:\Python\pythonw.exe"

    def test_returns_none_for_empty_string(self):
        from voice_typer.server.server_platform.autostart_windows import _extract_command_from_task_xml

        assert _extract_command_from_task_xml("") is None
        assert _extract_command_from_task_xml(None) is None  # type: ignore[arg-type]

    def test_returns_none_for_malformed_xml(self):
        from voice_typer.server.server_platform.autostart_windows import _extract_command_from_task_xml

        assert _extract_command_from_task_xml("<not valid xml") is None

    def test_returns_none_when_no_command_element(self):
        from voice_typer.server.server_platform.autostart_windows import _extract_command_from_task_xml

        xml = "<Task><Triggers><LogonTrigger/></Triggers></Task>"
        assert _extract_command_from_task_xml(xml) is None


# ---------------------------------------------------------------------------
# _is_app_autostart_runkey_registered (stale entry detection)
# ---------------------------------------------------------------------------


class TestIsAppAutostartRunkeyRegisteredStaleDetection:
    """``_is_app_autostart_runkey_registered`` detects stale entries."""

    def test_returns_false_when_command_path_does_not_exist(self, monkeypatch, fake_winreg, win32_platform):
        """If the Run-key value exists but the exe path doesn't exist on
        disk, the entry is stale — return False."""
        from voice_typer.server.server_platform import (
            _is_app_autostart_runkey_registered,
        )

        stale_cmd = r'"C:\Deleted\pythonw.exe" "C:\app\launcher.py" --hidden'
        fake_winreg.QueryValueEx = MagicMock(return_value=(stale_cmd, 1))
        # The exe path does NOT exist.
        monkeypatch.setattr(Path, "exists", lambda self: False)
        # _cleanup_stale_runkey_entry opens the key with KEY_SET_VALUE.
        fake_winreg.OpenKey = MagicMock(return_value=MagicMock())

        result = _is_app_autostart_runkey_registered()
        assert result is False

    def test_returns_true_when_command_path_exists(self, monkeypatch, fake_winreg, win32_platform):
        """If the Run-key value exists AND the exe path exists, return True."""
        from voice_typer.server.server_platform import _is_app_autostart_runkey_registered

        valid_cmd = r'"C:\Python\pythonw.exe" "C:\app\launcher.py" --hidden'
        fake_winreg.QueryValueEx = MagicMock(return_value=(valid_cmd, 1))
        # The exe path EXISTS.
        existing = {r"C:\Python\pythonw.exe"}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)

        result = _is_app_autostart_runkey_registered()
        assert result is True

    def test_cleans_up_stale_entry(self, monkeypatch, fake_winreg, win32_platform):
        """When a stale entry is detected, it should be deleted (cleanup)."""
        from voice_typer.server.server_platform import _is_app_autostart_runkey_registered

        stale_cmd = r'"C:\Deleted\pythonw.exe" "C:\app\launcher.py" --hidden'
        fake_winreg.QueryValueEx = MagicMock(return_value=(stale_cmd, 1))
        monkeypatch.setattr(Path, "exists", lambda self: False)

        _is_app_autostart_runkey_registered()
        # DeleteValue should have been called to clean up the stale entry.
        fake_winreg.DeleteValue.assert_called_once()

    def test_returns_false_when_value_not_found(self, monkeypatch, fake_winreg, win32_platform):
        """If the Run-key value doesn't exist (FileNotFoundError), return False."""
        from voice_typer.server.server_platform import _is_app_autostart_runkey_registered

        fake_winreg.QueryValueEx = MagicMock(side_effect=FileNotFoundError("not found"))

        result = _is_app_autostart_runkey_registered()
        assert result is False


# ---------------------------------------------------------------------------
# _is_app_autostart_task_registered (stale task detection)
# ---------------------------------------------------------------------------


class TestIsAppAutostartTaskRegisteredStaleDetection:
    """``_is_app_autostart_task_registered`` detects stale tasks."""

    def test_returns_false_when_command_path_does_not_exist(self, monkeypatch, fake_winreg, win32_platform):
        """If the task exists but its <Command> path doesn't exist on disk,
        the task is stale — return False."""
        from voice_typer.server import task_scheduler
        from voice_typer.server.server_platform import _is_app_autostart_task_registered

        monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)
        xml_output = (
            '<?xml version="1.0"?>'
            '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            "<Actions><Exec>"
            "<Command>C:\\Deleted\\pythonw.exe</Command>"
            "</Exec></Actions>"
            "</Task>"
        )
        monkeypatch.setattr(task_scheduler, "_schtasks", lambda *a, **kw: (0, xml_output))
        # The command path does NOT exist.
        monkeypatch.setattr(Path, "exists", lambda self: False)

        result = _is_app_autostart_task_registered()
        assert result is False

    def test_returns_true_when_command_path_exists(self, monkeypatch, fake_winreg, win32_platform):
        """If the task exists AND its <Command> path exists, return True."""
        from voice_typer.server import task_scheduler
        from voice_typer.server.server_platform import _is_app_autostart_task_registered

        monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)
        xml_output = (
            '<?xml version="1.0"?>'
            '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            "<Actions><Exec>"
            "<Command>C:\\Python\\pythonw.exe</Command>"
            "</Exec></Actions>"
            "</Task>"
        )
        monkeypatch.setattr(task_scheduler, "_schtasks", lambda *a, **kw: (0, xml_output))
        existing = {r"C:\Python\pythonw.exe"}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)

        result = _is_app_autostart_task_registered()
        assert result is True

    def test_returns_false_when_schtasks_query_fails(self, monkeypatch, fake_winreg, win32_platform):
        """If schtasks /Query returns non-zero, the task doesn't exist."""
        from voice_typer.server import task_scheduler
        from voice_typer.server.server_platform import _is_app_autostart_task_registered

        monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)
        monkeypatch.setattr(task_scheduler, "_schtasks", lambda *a, **kw: (1, "not found"))

        result = _is_app_autostart_task_registered()
        assert result is False


# ---------------------------------------------------------------------------
# Startup-folder .bat fallback
# ---------------------------------------------------------------------------


class TestStartupFolderBatFallback:
    """The Windows Startup-folder .bat tertiary fallback."""

    def test_register_writes_bat_file(self, monkeypatch, fake_winreg, win32_platform, tmp_path):
        """``_register_app_autostart_startup`` writes a .bat to the Startup folder."""
        from voice_typer.server import server_platform

        # Redirect get_autostart_dir to tmp_path.
        monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: tmp_path)
        # Stub _autostart_command to return a known command.
        monkeypatch.setattr(
            server_platform,
            "_autostart_command",
            lambda: r'"C:\Python\pythonw.exe" "C:\app\launcher.py" --hidden --delay 15',
        )

        result = server_platform._register_app_autostart_startup()
        assert result is True
        # The .bat file should exist.
        bat_files = list(tmp_path.glob("VoiceTyper_*.bat"))
        assert len(bat_files) == 1
        content = bat_files[0].read_text()
        assert "VT_START_HIDDEN=1" in content
        assert "start" in content
        assert "pythonw.exe" in content

    def test_register_returns_false_on_non_windows(self, monkeypatch, tmp_path):
        """On non-Windows, the Startup-folder fallback returns False."""
        from voice_typer.server import server_platform

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(server_platform, "SYSTEM", "linux")
        monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: tmp_path)
        result = server_platform._register_app_autostart_startup()
        assert result is False

    def test_unregister_removes_bat_file(self, monkeypatch, fake_winreg, win32_platform, tmp_path):
        """``_unregister_app_autostart_startup`` deletes the .bat file."""
        from voice_typer.server import server_platform

        monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: tmp_path)
        # Use the real hash-based name (don't patch _startup_bat_name —
        # it's called directly by _startup_bat_path, not via _pkg).
        bat_name = server_platform._startup_bat_name()
        bat_path = tmp_path / bat_name
        bat_path.write_text("@echo off\r\n")
        assert bat_path.exists()

        result = server_platform._unregister_app_autostart_startup()
        assert result is True
        assert not bat_path.exists()

    def test_unregister_returns_true_when_file_absent(self, monkeypatch, fake_winreg, win32_platform, tmp_path):
        """``_unregister_app_autostart_startup`` is idempotent (returns True when absent)."""
        from voice_typer.server import server_platform

        monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: tmp_path)
        # No .bat file created — should return True (idempotent).
        result = server_platform._unregister_app_autostart_startup()
        assert result is True

    def test_is_registered_returns_false_when_bat_absent(self, monkeypatch, fake_winreg, win32_platform, tmp_path):
        """``_is_app_autostart_startup_registered`` returns False when no .bat exists."""
        from voice_typer.server import server_platform

        monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: tmp_path)
        result = server_platform._is_app_autostart_startup_registered()
        assert result is False

    def test_is_registered_returns_true_when_bat_valid(self, monkeypatch, fake_winreg, win32_platform, tmp_path):
        """``_is_app_autostart_startup_registered`` returns True when the .bat
        exists and its target command path exists."""
        from voice_typer.server import server_platform

        monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: tmp_path)
        bat_name = server_platform._startup_bat_name()
        bat_path = tmp_path / bat_name
        bat_path.write_text(
            "@echo off\r\n"
            "set VT_START_HIDDEN=1\r\n"
            'start "" /B "C:\\Python\\pythonw.exe" "C:\\app\\launcher.py" --hidden\r\n'
        )
        # Patch Path.exists to return True for the .bat file AND the exe path.
        # The .bat file is a real file on disk; the exe path is fictional.
        existing = {str(bat_path), r"C:\Python\pythonw.exe"}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)

        result = server_platform._is_app_autostart_startup_registered()
        assert result is True

    def test_is_registered_returns_false_when_bat_stale(self, monkeypatch, fake_winreg, win32_platform, tmp_path):
        """``_is_app_autostart_startup_registered`` returns False and cleans up
        when the .bat's target command path doesn't exist."""
        from voice_typer.server import server_platform

        monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: tmp_path)
        bat_name = server_platform._startup_bat_name()
        bat_path = tmp_path / bat_name
        bat_path.write_text(
            "@echo off\r\n"
            "set VT_START_HIDDEN=1\r\n"
            'start "" /B "C:\\Deleted\\pythonw.exe" "C:\\app\\launcher.py" --hidden\r\n'
        )
        # Don't patch Path.exists — the .bat file is a real file on disk
        # (exists returns True), and the exe path C:\Deleted\pythonw.exe
        # doesn't exist on Linux (exists returns False). The validation
        # correctly detects the stale target and cleans up.
        result = server_platform._is_app_autostart_startup_registered()
        assert result is False
        # The stale .bat should have been deleted.
        assert not bat_path.exists()


# ---------------------------------------------------------------------------
# _enable_autostart_windows / _disable_autostart_windows / _is_autostart_windows
# (three-mechanism integration)
# ---------------------------------------------------------------------------


class TestThreeMechanismIntegration:
    """The enable/disable/is_enabled functions handle all three mechanisms."""

    def test_enable_falls_back_to_startup_folder(self, monkeypatch, fake_winreg, win32_platform, tmp_path):
        """When Run key AND Task Scheduler both fail, the Startup-folder .bat
        is tried as a tertiary fallback."""
        from voice_typer.server import server_platform

        monkeypatch.setattr(server_platform, "_register_app_autostart_runkey", lambda: False)
        monkeypatch.setattr(server_platform, "_register_app_autostart_task", lambda: False)
        monkeypatch.setattr(server_platform, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(
            server_platform,
            "_autostart_command",
            lambda: r'"C:\Python\pythonw.exe" "C:\app\launcher.py" --hidden',
        )

        result = server_platform._enable_autostart_windows()
        assert result is True
        # The .bat file should exist.
        bat_files = list(tmp_path.glob("VoiceTyper_*.bat"))
        assert len(bat_files) == 1

    def test_enable_runkey_cleans_up_startup_bat(self, monkeypatch, fake_winreg, win32_platform, tmp_path):
        """When the Run key succeeds, the Startup-folder .bat is cleaned up."""
        from voice_typer.server import server_platform

        monkeypatch.setattr(server_platform, "_register_app_autostart_runkey", lambda: True)
        monkeypatch.setattr(server_platform, "_unregister_app_autostart_task", lambda: True)
        # Track if _unregister_app_autostart_startup is called.
        startup_unregistered = []
        monkeypatch.setattr(
            server_platform,
            "_unregister_app_autostart_startup",
            lambda: startup_unregistered.append(True) or True,
        )

        result = server_platform._enable_autostart_windows()
        assert result is True
        assert len(startup_unregistered) == 1, "Startup .bat should be cleaned up when Run key succeeds"

    def test_disable_removes_all_three_mechanisms(self, monkeypatch, fake_winreg, win32_platform):
        """``_disable_autostart_windows`` removes from Task Scheduler, Run key,
        AND Startup folder."""
        from voice_typer.server import server_platform

        task_removed = []
        runkey_removed = []
        startup_removed = []
        monkeypatch.setattr(
            server_platform,
            "_unregister_app_autostart_task",
            lambda: task_removed.append(True) or True,
        )
        monkeypatch.setattr(
            server_platform,
            "_unregister_app_autostart_runkey",
            lambda: runkey_removed.append(True) or True,
        )
        monkeypatch.setattr(
            server_platform,
            "_unregister_app_autostart_startup",
            lambda: startup_removed.append(True) or True,
        )

        result = server_platform._disable_autostart_windows()
        assert result is True
        assert len(task_removed) == 1
        assert len(runkey_removed) == 1
        assert len(startup_removed) == 1

    def test_is_autostart_windows_checks_all_three(self, monkeypatch, fake_winreg, win32_platform):
        """``_is_autostart_windows`` returns True if ANY of the three mechanisms
        is registered."""
        from voice_typer.server import server_platform

        # Only Startup folder.
        monkeypatch.setattr(server_platform, "_is_app_autostart_task_registered", lambda: False)
        monkeypatch.setattr(server_platform, "_is_app_autostart_runkey_registered", lambda: False)
        monkeypatch.setattr(server_platform, "_is_app_autostart_startup_registered", lambda: True)
        assert server_platform._is_autostart_windows() is True

        # None.
        monkeypatch.setattr(server_platform, "_is_app_autostart_task_registered", lambda: False)
        monkeypatch.setattr(server_platform, "_is_app_autostart_runkey_registered", lambda: False)
        monkeypatch.setattr(server_platform, "_is_app_autostart_startup_registered", lambda: False)
        assert server_platform._is_autostart_windows() is False


# ---------------------------------------------------------------------------
# _autostart_command (validation + Tauri binary fallback)
# ---------------------------------------------------------------------------


class TestAutostartCommandValidation:
    """``_autostart_command`` validates the python path and falls back."""

    def test_returns_python_command_when_python_exists(self, monkeypatch):
        """When sys.executable exists, the command uses the Python path."""
        from voice_typer.server.server_platform import _autostart_command

        # Ensure sys.executable is treated as existing.
        monkeypatch.setattr(Path, "exists", lambda self: str(self) == sys.executable)
        cmd = _autostart_command()
        assert "autostart_launcher.py" in cmd
        assert "--hidden" in cmd

    def test_falls_back_to_tauri_binary_when_python_missing(self, monkeypatch, tmp_path):
        """When the resolved Python path doesn't exist AND a Tauri binary is
        found, the autostart command is the Tauri binary path."""
        from voice_typer.server import server_platform

        # Mock _system_python_can_import_launcher to return False so the
        # venv-swap doesn't replace sys.executable with the venv python
        # (which exists on disk and would pass validation).
        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._system_python_can_import_launcher",
            lambda python: False,
        )
        # Make Path.exists return False for ALL python paths so the
        # validation triggers the Tauri binary fallback.
        real_exists = Path.exists

        def fake_exists(self):
            s = str(self)
            if s == sys.executable:
                return False
            if "python" in s.lower() and "voice-typer-tauri" not in s.lower():
                return False
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)
        # Force the Tauri binary to be found at a tmp path.
        fake_tauri = tmp_path / "voice-typer-tauri"
        fake_tauri.write_text("#!/bin/sh\nexit 0\n")
        fake_tauri.chmod(0o755)
        monkeypatch.setenv("VT_TAURI_BINARY", str(fake_tauri))

        cmd = server_platform._autostart_command()
        # The command should be the Tauri binary path (quoted).
        assert str(fake_tauri) in cmd
        # No --hidden or --delay args (Tauri binary takes no CLI args).
        assert "--hidden" not in cmd

    def test_logs_warning_when_python_missing(self, monkeypatch, tmp_path, caplog):
        """When the Python path doesn't exist, a warning is logged."""
        import logging

        from voice_typer.server import server_platform

        # Mock _system_python_can_import_launcher to return False.
        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._system_python_can_import_launcher",
            lambda python: False,
        )
        real_exists = Path.exists

        def fake_exists(self):
            s = str(self)
            if s == sys.executable:
                return False
            if "python" in s.lower() and "voice-typer-tauri" not in s.lower():
                return False
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)
        # No Tauri binary either.
        monkeypatch.delenv("VT_TAURI_BINARY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.server_platform.autostart"):
            server_platform._autostart_command()
        # A warning should be logged about the missing Python interpreter.
        assert any(
            "does not exist" in record.message.lower() or "no python interpreter" in record.message.lower()
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# _app_autostart_command_and_args (validation + Tauri binary fallback)
# ---------------------------------------------------------------------------


class TestAppAutostartCommandAndArgsValidation:
    """``_app_autostart_command_and_args`` validates and falls back."""

    def test_falls_back_to_tauri_binary_when_python_missing(self, monkeypatch, tmp_path):
        """When the resolved Python path doesn't exist AND a Tauri binary is
        found, returns (tauri_binary, "")."""
        from voice_typer.server.server_platform import _app_autostart_command_and_args

        # Mock _system_python_can_import_launcher to return False so the
        # venv-swap doesn't replace sys.executable.
        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._system_python_can_import_launcher",
            lambda python: False,
        )
        real_exists = Path.exists

        def fake_exists(self):
            s = str(self)
            if "python" in s.lower() and "voice-typer-tauri" not in s.lower():
                return False
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)
        fake_tauri = tmp_path / "voice-typer-tauri.exe"
        fake_tauri.write_text("")
        monkeypatch.setenv("VT_TAURI_BINARY", str(fake_tauri))

        python_bin, args = _app_autostart_command_and_args()
        assert python_bin == str(fake_tauri)
        assert args == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
