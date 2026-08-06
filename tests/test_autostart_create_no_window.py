""" — regression tests for ``CREATE_NO_WINDOW`` on Windows.

Both ``_system_python_can_import_launcher`` (in
:mod:`voice_typer.server.server_platform.autostart`) and
``_unregister_all_voicetyper_tasks`` (in
:mod:`voice_typer.server.server_platform.autostart_windows`) spawn a
``python.exe`` / ``powershell.exe`` subprocess via ``subprocess.run``.
Pre-fix , neither call set ``creationflags``, so on
Windows the spawned child would briefly flash a console window:

  * the system-python import probe flashes ``python.exe`` for
    ~50 ms during ``_enable_autostart_macos`` / ``_enable_autostart_linux``
    when running inside a venv (the probe is invoked from
    ``_enable_autostart_*`` via
    ``from voice_typer.server.server_platform.autostart import
    _system_python_can_import_launcher``). On Windows the flashing
    window is visible to the user.
  * the PowerShell sweep at uninstall time flashes
    ``powershell.exe`` for up to ~60 s while the sweep runs. The sweep
    is launched from a UI-driven uninstall flow where a flashing
    console looks broken.

Post-fix: both calls pass ``creationflags=CREATE_NO_WINDOW``
(``0x08000000``) on Windows so the subprocess runs without a console
window. The flag is guarded by ``is_windows()``  — on macOS /
Linux ``creationflags`` is NOT a valid ``subprocess.run`` kwarg and
``subprocess`` raises ``ValueError`` if it's set.

These tests run on any platform — they mock ``sys.platform`` and
``subprocess.run`` so the ``creationflags`` kwarg is asserted without
needing a real Windows host. VALIDATE ON WINDOWS HOST.
"""

from __future__ import annotations

import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# _system_python_can_import_launcher — CREATE_NO_WINDOW
# ---------------------------------------------------------------------------


class TestSystemPythonProbeCreateNoWindow:
    """``_system_python_can_import_launcher`` passes
    ``creationflags=CREATE_NO_WINDOW`` to ``subprocess.run`` on Windows
    so the ``python.exe`` probe doesn't flash a console."""

    def test_windows_passes_create_no_window(self, monkeypatch):
        """On Windows, the probe must pass ``creationflags=0x08000000``
        (CREATE_NO_WINDOW) to ``subprocess.run``."""
        # Force is_windows() → True. The probe reads ``is_windows`` from
        # ``voice_typer.server.platform_utils`` (imported at the top of
        # ``autostart.py``), and ``is_windows`` reads ``sys.platform``
        # from the same module — so patching ``sys.platform`` on the
        # platform_utils module is the canonical way to flip the result.
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
            "creationflags must be CREATE_NO_WINDOW (0x08000000); "
            f"got {captured['creationflags']:#x}"
        )

    def test_linux_omits_creationflags(self, monkeypatch):
        """On Linux, the probe must NOT pass ``creationflags`` — it's
        not a valid ``subprocess.run`` kwarg on POSIX and raises
        ``ValueError`` if set."""
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
            "on Linux the probe must NOT set creationflags "
            "(POSIX subprocess.run doesn't accept it)"
        )

    def test_macos_omits_creationflags(self, monkeypatch):
        """On macOS, the probe must NOT pass ``creationflags`` either —
        same POSIX rule as Linux."""
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
            "on macOS the probe must NOT set creationflags "
            "(POSIX subprocess.run doesn't accept it)"
        )


# ---------------------------------------------------------------------------
# _unregister_all_voicetyper_tasks — CREATE_NO_WINDOW
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import
    cleanly. Mirrors the fixture in ``tests/test_uninstall_windows.py``.
    """
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


class TestUnregisterAllTasksCreateNoWindow:
    """``_unregister_all_voicetyper_tasks`` passes
    ``creationflags=CREATE_NO_WINDOW`` to ``subprocess.run`` so the
    ``powershell.exe`` sweep doesn't flash a console during uninstall."""

    def test_powershell_call_includes_create_no_window(
        self, monkeypatch, fake_winreg, win32_platform
    ):
        """The PowerShell subprocess.run call MUST pass
        ``creationflags=0x08000000`` (CREATE_NO_WINDOW) so the uninstall
        sweep doesn't flash a console window at the user."""
        from voice_typer.server.server_platform import autostart_windows

        # Stub task_scheduler.is_supported() → True so the function
        # proceeds to the PowerShell call.
        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: True
        monkeypatch.setitem(
            sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler
        )

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            captured["cmd"] = list(cmd)
            r = MagicMock()
            r.returncode = 0
            # Match the production-code parsing: lines starting with
            # "VoiceTyperAutostart" are returned as deleted task names.
            r.stdout = "VoiceTyperAutostart_aaaaaaaa\n"
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        deleted = autostart_windows._unregister_all_voicetyper_tasks()
        assert deleted == ["VoiceTyperAutostart_aaaaaaaa"], (
            f"expected the stubbed task name in the deleted list; got {deleted}"
        )
        # the PowerShell call MUST set creationflags.
        assert "creationflags" in captured, (
            "_unregister_all_voicetyper_tasks must pass creationflags "
            "to subprocess.run so powershell.exe doesn't flash a console "
            "during uninstall"
        )
        assert captured["creationflags"] == 0x08000000, (
            "creationflags must be CREATE_NO_WINDOW (0x08000000); "
            f"got {captured['creationflags']:#x}"
        )
        # Sanity: the call IS a powershell.exe invocation.
        assert captured["cmd"][0] == "powershell.exe", (
            f"expected powershell.exe as argv[0]; got {captured['cmd'][0]}"
        )


# ---------------------------------------------------------------------------
# is_wayland_session restricted to Linux only
# ---------------------------------------------------------------------------


class TestIsWaylandSessionLinuxOnly:
    """``is_wayland_session`` must return False on macOS — Wayland
    is a Linux display-server protocol and macOS uses Quartz/Aqua."""

    def test_returns_false_on_macos_even_with_wayland_env(self, monkeypatch):
        """Setting ``WAYLAND_DISPLAY`` on macOS must NOT cause
        ``is_wayland_session`` to return True — macOS does not run
        Wayland. Pre-, this returned True because the platform
        guard accepted ``darwin`` as a Wayland-capable platform."""
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils.sys, "platform", "darwin")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        assert platform_utils.is_wayland_session() is False, (
            "is_wayland_session must return False on macOS even "
            "when WAYLAND_DISPLAY + XDG_SESSION_TYPE=wayland are set — "
            "macOS uses Quartz/Aqua, not Wayland"
        )

    def test_returns_false_on_windows(self, monkeypatch):
        """Windows can never be Wayland — same as pre-fix."""
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils.sys, "platform", "win32")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert platform_utils.is_wayland_session() is False

    def test_returns_true_on_linux_with_wayland_env(self, monkeypatch):
        """Sanity: on Linux with ``WAYLAND_DISPLAY`` set, the function
        still returns True ( only restricts the platform guard,
        not the env-var detection)."""
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils.sys, "platform", "linux")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert platform_utils.is_wayland_session() is True

    def test_returns_true_on_linux_with_xdg_session_type(self, monkeypatch):
        """Sanity: on Linux with ``XDG_SESSION_TYPE=wayland`` (and no
        ``WAYLAND_DISPLAY``), the function still returns True."""
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils.sys, "platform", "linux")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        assert platform_utils.is_wayland_session() is True
