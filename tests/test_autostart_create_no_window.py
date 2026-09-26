"""— regression tests for ``CREATE_NO_WINDOW`` on Windows."""

from __future__ import annotations

import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest


class TestSystemPythonProbeCreateNoWindow:
    """``_system_python_can_import_launcher`` passes"""

    def test_windows_passes_create_no_window(self, monkeypatch):
        """On Windows, the probe must pass ``creationflags=0x08000000``"""
        monkeypatch.setattr(
            "voice_typer.server.platform_utils.sys.platform",
            "win32",
        )

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            captured["cmd"] = list(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        from voice_typer.server.server_platform.autostart import (
            _system_python_can_import_launcher,
        )

        result = _system_python_can_import_launcher("python.exe")
        assert result is True, "probe should return True when rc=0"
        assert "creationflags" in captured, (
            "on Windows the probe MUST pass creationflags to "
            "subprocess.run so python.exe doesn't flash a console window"
        )
        assert captured["creationflags"] == 0x08000000, (
            f"creationflags must be CREATE_NO_WINDOW (0x08000000); got {captured['creationflags']:#x}"
        )

    def test_linux_omits_creationflags(self, monkeypatch):
        """On Linux, the probe must NOT pass ``creationflags``, it's"""
        monkeypatch.setattr(
            "voice_typer.server.platform_utils.sys.platform",
            "linux",
        )

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            r = MagicMock()
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        from voice_typer.server.server_platform.autostart import (
            _system_python_can_import_launcher,
        )

        result = _system_python_can_import_launcher("python3")
        assert result is True
        assert "creationflags" not in captured, (
            "on Linux the probe must NOT set creationflags (POSIX subprocess.run doesn't accept it)"
        )

    def test_macos_omits_creationflags(self, monkeypatch):
        """On macOS, the probe must NOT pass ``creationflags`` either —"""
        monkeypatch.setattr(
            "voice_typer.server.platform_utils.sys.platform",
            "darwin",
        )

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            r = MagicMock()
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        from voice_typer.server.server_platform.autostart import (
            _system_python_can_import_launcher,
        )

        result = _system_python_can_import_launcher("python3")
        assert result is True
        assert "creationflags" not in captured, (
            "on macOS the probe must NOT set creationflags (POSIX subprocess.run doesn't accept it)"
        )


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import"""
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
    from voice_typer.server.server_platform import platform_flags

    monkeypatch.setattr(platform_flags, "SYSTEM", "win32")
    return server_platform


class TestUnregisterAllTasksCreateNoWindow:
    """``_unregister_all_lausu_tasks`` passes"""

    def test_powershell_call_includes_create_no_window(self, monkeypatch, fake_winreg, win32_platform):
        """The PowerShell subprocess.run call MUST pass"""
        from voice_typer.server.server_platform import autostart_windows

        # Stub task_scheduler.is_supported() → True so the function
        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: True
        monkeypatch.setitem(sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler)
        # ``raising=False`` so the patch succeeds whether or not a prior
        monkeypatch.setattr(
            "voice_typer.server.task_scheduler",
            fake_task_scheduler,
            raising=False,
        )

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            captured["cmd"] = list(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = "LausuAutostart_aaaaaaaa\ncom.Lausu.prewarm\ncom.Lausu.autostart_bbbbbbbb\n"
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        deleted = autostart_windows._unregister_all_lausu_tasks()
        assert sorted(deleted) == [
            "LausuAutostart_aaaaaaaa",
            "com.Lausu.autostart_bbbbbbbb",
            "com.Lausu.prewarm",
        ], f"expected the stubbed task names in the deleted list; got {deleted}"
        assert "creationflags" in captured, (
            "_unregister_all_lausu_tasks must pass creationflags "
            "to subprocess.run so powershell.exe doesn't flash a console "
            "during uninstall"
        )
        assert captured["creationflags"] == 0x08000000, (
            f"creationflags must be CREATE_NO_WINDOW (0x08000000); got {captured['creationflags']:#x}"
        )
        # Sanity: the call IS a powershell.exe invocation.
        assert captured["cmd"][0] == "powershell.exe", f"expected powershell.exe as argv[0]; got {captured['cmd'][0]}"


class TestIsWaylandSessionLinuxOnly:
    """``is_wayland_session`` must return False on macOS, Wayland"""

    def test_returns_false_on_macos_even_with_wayland_env(self, monkeypatch):
        """Setting ``WAYLAND_DISPLAY`` on macOS must NOT cause"""
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils.sys, "platform", "darwin")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        assert platform_utils.is_wayland_session() is False, (
            "is_wayland_session must return False on macOS even "
            "when WAYLAND_DISPLAY + XDG_SESSION_TYPE=wayland are set, "
            "macOS uses Quartz/Aqua, not Wayland"
        )

    def test_returns_false_on_windows(self, monkeypatch):
        """Windows can never be Wayland, same as pre-fix."""
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils.sys, "platform", "win32")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert platform_utils.is_wayland_session() is False

    def test_returns_true_on_linux_with_wayland_env(self, monkeypatch):
        """Sanity: on Linux with ``WAYLAND_DISPLAY`` set, the function"""
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils.sys, "platform", "linux")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert platform_utils.is_wayland_session() is True

    def test_returns_true_on_linux_with_xdg_session_type(self, monkeypatch):
        """Sanity: on Linux with ``XDG_SESSION_TYPE=wayland`` (and no"""
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils.sys, "platform", "linux")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        assert platform_utils.is_wayland_session() is True
