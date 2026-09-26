"""Packaged-install autostart command-builder contract."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.server_platform import (
    autostart as autostart_mod,
    autostart_windows as autostart_windows_mod,
    platform_flags,
)

_PACKAGED_TARGET = getattr(autostart_mod, "_packaged_tauri_target", None)

needs_packaged_target = pytest.mark.skipif(
    _PACKAGED_TARGET is None,
    reason="packaged-target helper not present in this code state (parallel production slice)",
)


def _blob(result) -> str:
    """Flatten a ``(binary, [args])`` packaged target into one string."""
    if result is None:
        return ""
    parts: list[str] = []
    if isinstance(result, (tuple, list)):
        for item in result:
            parts.extend(item if isinstance(item, list) else [item])
    else:
        parts.append(result)
    return " ".join(str(p) for p in parts)


def _fake_tauri_binary(tmp_path: Path, name: str = "lausu-tauri") -> Path:
    """Create an executable stand-in for the installed desktop binary."""
    import os as _os

    binary = tmp_path / name
    binary.write_bytes(b"fake tauri binary")
    _os.chmod(binary, 0o755)
    return binary


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows paths import cleanly."""
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
    """Pretend to be on Windows (both ``sys.platform`` and ``SYSTEM``)."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform_flags, "SYSTEM", "win32")
    return fake_winreg


@pytest.fixture
def posix_platform(monkeypatch):
    """Pretend to be on Linux (both ``sys.platform`` and ``SYSTEM``)."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform_flags, "SYSTEM", "linux")


class TestPackagedFallbackQuoting:
    """When an installed binary resolves, the command targets it directly"""

    def test_windows_fallback_is_single_list2cmdline_token(self, win32_platform, monkeypatch, tmp_path):
        """Windows packaged command is one list2cmdline line: binary + flags."""
        binary = _fake_tauri_binary(tmp_path, name="lausu-tauri.exe")
        monkeypatch.setattr(autostart_mod, "_prefer_pythonw", lambda p: str(tmp_path / "missing-pythonw.exe"))
        monkeypatch.setattr(autostart_mod, "_probe_system_python", lambda name: None)
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        cmd = autostart_mod._autostart_command()
        assert cmd == subprocess.list2cmdline([str(binary), "--hidden", "--delay", "3"])
        assert "\\\\" not in cmd.split("--hidden")[0]
        assert "--hidden" in cmd and "--delay" in cmd

    def test_posix_fallback_uses_desktop_quote(self, posix_platform, monkeypatch, tmp_path):
        """macOS/Linux packaged command is the quoted binary + flags."""
        binary = _fake_tauri_binary(tmp_path)
        monkeypatch.setattr(autostart_mod, "_probe_system_python", lambda name: None)
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        # Force the "no interpreter" branch regardless of the real venv.
        monkeypatch.setattr(autostart_mod, "_prefer_pythonw", lambda p: str(tmp_path / "missing.exe"))
        monkeypatch.setattr(sys, "executable", str(tmp_path / "missing-python"))
        cmd = autostart_mod._autostart_command()
        assert cmd.startswith(autostart_mod._desktop_quote(str(binary)))
        assert "--hidden" in cmd

    def test_windows_backslash_paths_stay_literal(self, win32_platform, monkeypatch, tmp_path):
        """A spaced Windows binary path is quoted once, backslashes intact."""
        win_path = r"C:\Program Files\Lausu\lausu-tauri.exe"
        quoted = subprocess.list2cmdline([win_path])
        assert "\\\\" not in quoted
        assert quoted.startswith('"') and quoted.endswith('"')
        assert autostart_mod._desktop_quote(win_path).count("\\\\") >= 1


class TestTaskXmlPackagedShape:
    """The Task XML splits a packaged (binary + flags) resolver result"""

    def test_command_is_binary_and_arguments_carry_hidden(self, win32_platform, monkeypatch, tmp_path):
        binary = _fake_tauri_binary(tmp_path, name="lausu-tauri.exe")
        monkeypatch.setattr(
            autostart_windows_mod,
            "_app_autostart_command_and_args",
            lambda: (str(binary), "--hidden --delay 3"),
        )
        xml = autostart_windows_mod._build_app_autostart_task_xml()
        assert "cmd.exe" not in xml
        assert "<LogonTrigger>" in xml
        assert f"<Command>{binary}</Command>" in xml
        assert "--hidden" in xml
        assert "--delay" in xml

    def test_run_key_value_has_no_doubled_backslashes(self, win32_platform, monkeypatch):
        """A packaged Run-key value is one list2cmdline token: backslashes"""
        raw = r"C:\Program Files\Lausu\lausu-tauri.exe"
        value = subprocess.list2cmdline([raw, "--hidden", "--delay", "3"])
        assert "\\\\" not in value

        assert "\\\\" not in value.split("--hidden")[0]


class TestDevModeUnchanged:
    """A dev checkout (launcher script present, interpreter alive) keeps"""

    def test_dev_command_uses_launcher_with_hidden(self, posix_platform, monkeypatch):
        # Pure-dev machine: no installed binary, so the launcher path stays.
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: None)
        monkeypatch.setattr(autostart_mod, "_probe_system_python", lambda name: None)
        monkeypatch.setattr(sys, "prefix", sys.base_prefix)
        cmd = autostart_mod._autostart_command()
        assert "autostart_launcher" in cmd
        assert "--hidden" in cmd
        assert "--delay" in cmd

    def test_dev_runkey_command_validates_clean(self, win32_platform, monkeypatch, tmp_path):
        """A dev python+launcher command pointing at live files is valid"""
        from voice_typer.server.server_platform.autostart_windows import (
            _validate_runkey_command,
        )

        # Pure-dev machine: no installed binary, so no supersede migration.
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: None)
        launcher = tmp_path / "autostart_launcher.py"
        launcher.write_text("# launcher")
        interpreter = tmp_path / "pythonw.exe"
        interpreter.write_bytes(b"x")
        existing = {str(interpreter), str(launcher)}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)
        value = f'"{interpreter}" "{launcher}" --hidden --delay 3'
        assert _validate_runkey_command(value) is True


class TestMissingTargetIsStale:
    """Commands whose program no longer exists validate stale on every"""

    def test_runkey_missing_binary_is_stale(self, monkeypatch):
        from voice_typer.server.server_platform.autostart_windows import (
            _validate_runkey_command,
        )

        monkeypatch.setattr(Path, "exists", lambda self: False)
        assert _validate_runkey_command(r'"C:\Deleted\lausu-tauri.exe" --hidden') is False
        assert _validate_runkey_command(r'"C:\Deleted\pythonw.exe" "C:\app\launcher.py" --hidden') is False

    def test_plist_missing_program_is_stale(self, tmp_path):
        from voice_typer.server.server_platform.autostart_macos import (
            _plist_program_arguments_exist,
        )

        plist = tmp_path / "com.Lausu.plist"
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<plist version="1.0"><dict>'
            "<key>Label</key><string>com.Lausu</string>"
            "<key>ProgramArguments</key><array>"
            "<string>/nonexistent/lausu-tauri</string>"
            "<string>--hidden</string>"
            "</array></dict></plist>",
            encoding="utf-8",
        )
        assert _plist_program_arguments_exist(plist) is False

    def test_desktop_missing_program_is_stale(self, tmp_path):
        from voice_typer.server.server_platform.autostart_linux import (
            _desktop_exec_path_exists,
        )

        desktop = tmp_path / "lausu.desktop"
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=x\nExec=/nonexistent/lausu-tauri --hidden\n",
            encoding="utf-8",
        )
        assert _desktop_exec_path_exists(desktop) is False


@needs_packaged_target
class TestPackagedTargetHelper:
    """The packaged-target helper picks the direct-binary registration"""

    def test_frozen_install_resolves_binary_with_hidden(self, monkeypatch, tmp_path):
        binary = _fake_tauri_binary(tmp_path)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: str(binary))
        result = _PACKAGED_TARGET()
        blob = _blob(result)
        assert str(binary) in blob
        assert "--hidden" in blob

    def test_missing_launcher_resolves_binary(self, monkeypatch, tmp_path):
        binary = _fake_tauri_binary(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: str(binary))
        real_launcher = Path(autostart_mod.__file__).resolve().parent.parent / "autostart_launcher.py"
        monkeypatch.setattr(Path, "exists", lambda self: str(self) != str(real_launcher))
        monkeypatch.setattr(Path, "is_file", lambda self: str(self) != str(real_launcher))
        result = _PACKAGED_TARGET()
        assert result is not None
        blob = _blob(result)
        assert str(binary) in blob

    def test_dev_checkout_does_not_resolve_packaged(self, monkeypatch):
        # Pure-dev machine: no installed binary anywhere.
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: None)
        monkeypatch.delattr(sys, "frozen", raising=False)
        result = _PACKAGED_TARGET()
        blob = _blob(result)
        assert result is None or "autostart_launcher" in blob

    def test_dev_interpreter_with_installed_binary_resolves_direct(self, monkeypatch, tmp_path):
        """Dev interpreter + packaged install → direct-binary (logon starts release)."""
        binary = _fake_tauri_binary(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        result = _PACKAGED_TARGET()
        assert result is not None
        binary_out, args = result
        assert binary_out == str(binary)
        assert "--hidden" in args and "--delay" in args


@needs_packaged_target
class TestLauncherSupersededByInstalledBinary:
    """A live python-launcher entry migrates once a packaged install exists."""

    def test_runkey_launcher_entry_superseded(self, win32_platform, monkeypatch, tmp_path):
        from voice_typer.server.server_platform.autostart_windows import (
            _validate_runkey_command,
        )

        launcher = tmp_path / "autostart_launcher.py"
        launcher.write_text("# launcher")
        interpreter = tmp_path / "pythonw.exe"
        interpreter.write_bytes(b"x")
        binary = _fake_tauri_binary(tmp_path, name="lausu-tauri.exe")
        monkeypatch.setattr(Path, "exists", lambda self: True)
        value = f'"{interpreter}" "{launcher}" --hidden --delay 3'
        # Installed binary present → superseded (stale).
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        assert _validate_runkey_command(value) is False
        # Pure-dev machine → still valid.
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: None)
        assert _validate_runkey_command(value) is True

    def test_plist_launcher_entry_superseded(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform.autostart_macos import (
            _plist_program_arguments_exist,
        )

        launcher = tmp_path / "autostart_launcher.py"
        launcher.write_text("# launcher")
        interpreter = tmp_path / "python3"
        interpreter.write_bytes(b"x")
        binary = _fake_tauri_binary(tmp_path)
        plist = tmp_path / "com.Lausu.plist"
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<plist version="1.0"><dict>'
            "<key>Label</key><string>com.Lausu</string>"
            "<key>ProgramArguments</key><array>"
            f"<string>{interpreter}</string>"
            f"<string>{launcher}</string>"
            "</array></dict></plist>",
            encoding="utf-8",
        )
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        assert _plist_program_arguments_exist(plist) is False
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: None)
        assert _plist_program_arguments_exist(plist) is True

    def test_desktop_launcher_entry_superseded(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform.autostart_linux import (
            _desktop_exec_path_exists,
        )

        launcher = tmp_path / "autostart_launcher.py"
        launcher.write_text("# launcher")
        interpreter = tmp_path / "python3"
        interpreter.write_bytes(b"x")
        binary = _fake_tauri_binary(tmp_path)
        desktop = tmp_path / "lausu.desktop"
        desktop.write_text(
            f'[Desktop Entry]\nType=Application\nName=x\nExec="{interpreter}" "{launcher}" --hidden --delay 3\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        assert _desktop_exec_path_exists(desktop) is False
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: None)
        assert _desktop_exec_path_exists(desktop) is True

    def test_direct_binary_entry_not_superseded(self, win32_platform, monkeypatch, tmp_path):
        """Direct-binary entries validate on their own merits, never migrate."""
        from voice_typer.server.server_platform.autostart_windows import (
            _validate_runkey_command,
        )

        binary = _fake_tauri_binary(tmp_path, name="lausu-tauri.exe")
        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(autostart_mod, "_resolve_tauri_binary_for_autostart", lambda: str(binary))
        assert _validate_runkey_command(f'"{binary}" --hidden --delay 3') is True


@needs_packaged_target
class TestPreviousGenerationCommandsAreStale:
    """Previous-generation host commands validate stale even when the"""

    def test_electron_named_binary_is_stale(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform.autostart_windows import (
            _validate_runkey_command,
        )

        electron = tmp_path / "electron.exe"
        electron.write_bytes(b"x")
        assert _validate_runkey_command(f'"{electron}" --hidden') is False

    def test_module_invocation_is_stale(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform.autostart_windows import (
            _validate_runkey_command,
        )

        interpreter = tmp_path / "pythonw.exe"
        interpreter.write_bytes(b"x")
        existing = {str(interpreter)}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)
        assert _validate_runkey_command(f'"{interpreter}" -m voice_typer --hidden') is False

    def test_phantom_launcher_command_is_stale(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform.autostart_windows import (
            _validate_runkey_command,
        )

        interpreter = tmp_path / "pythonw.exe"
        interpreter.write_bytes(b"x")
        existing = {str(interpreter)}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in existing)
        phantom = tmp_path / "autostart_launcher.py"
        assert not phantom.exists()
        value = f'"{interpreter}" "{phantom}" --hidden'
        assert _validate_runkey_command(value) is False


@needs_packaged_target
class TestMacosPlistPackagedOutput:
    """End-to-end macOS registrar output for a packaged install."""

    def test_program_arguments_point_at_binary_with_hidden(self, monkeypatch, tmp_path):
        import plistlib
        import subprocess as _subprocess

        from voice_typer.server.server_platform import autostart_macos as macos_mod

        binary = _fake_tauri_binary(tmp_path)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(platform_flags, "SYSTEM", "darwin")
        monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: str(binary))
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = b""
        monkeypatch.setattr(_subprocess, "run", lambda *a, **k: completed)
        from voice_typer.server import _paths

        monkeypatch.setattr(_paths, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(_paths, "autostart_log", lambda: tmp_path / "autostart.log")

        assert macos_mod._enable_autostart_macos() is True
        with (tmp_path / "com.Lausu.plist").open("rb") as fh:
            data = plistlib.load(fh)
        args = data["ProgramArguments"]
        assert args[0] == str(binary)
        assert Path(args[0]).exists()
        assert "--hidden" in args


@needs_packaged_target
class TestLinuxDesktopPackagedOutput:
    """End-to-end Linux registrar output for a packaged install."""

    def test_exec_starts_with_binary_and_terminal_false(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform import autostart_linux as linux_mod

        binary = _fake_tauri_binary(tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(platform_flags, "SYSTEM", "linux")
        monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("voice_typer.server.autostart_launcher._tauri_binary", lambda: str(binary))

        assert linux_mod._enable_autostart_linux() is True
        content = (tmp_path / "lausu.desktop").read_text(encoding="utf-8")
        exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))[len("Exec=") :]
        assert exec_line.startswith(autostart_mod._desktop_quote(str(binary)))
        assert "--hidden" in exec_line
        assert "Terminal=false" in content
