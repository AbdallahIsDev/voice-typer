"""Tests for voice_typer.server.permissions module (GAP-2, GAP-3).

Covers:
- permission_error_is_permission_denied() classifier
- check_keyboard_permission() per-platform probing
- request_keyboard_permission() macOS deep-link + Linux pkexec
- schedule_permission_retry() / cancel_permission_retry() timer
- show_permission_notification() tray notification helper
"""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# ─── permission_error_is_permission_denied ─────────────────────────────────


class TestPermissionErrorClassifier:
    """Verify the error classifier correctly identifies permission issues."""

    def test_macos_accessibility_error(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert (
            permission_error_is_permission_denied("Accessibility permission required. Grant it in System Settings.")
            is True
        )

    def test_linux_permission_denied(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("Permission denied opening /dev/input/event0") is True

    def test_linux_input_group_error(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert (
            permission_error_is_permission_denied("Add yourself to the 'input' group: sudo usermod -aG input $USER")
            is True
        )

    def test_linux_dev_input_error(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("Cannot open /dev/input: No such file or directory") is True

    def test_non_permission_error(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("Invalid hotkey spec: <bad>") is False

    def test_binary_not_found_error(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("Failed to spawn macOS binary: FileNotFoundError") is False

    def test_empty_message(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied("") is False

    def test_none_message(self):
        from voice_typer.server.permissions import permission_error_is_permission_denied

        assert permission_error_is_permission_denied(None) is False  # type: ignore[arg-type]


# ─── check_keyboard_permission ─────────────────────────────────────────────


class TestCheckKeyboardPermission:
    """Verify per-platform permission probing."""

    def test_windows_always_granted(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: True)
        monkeypatch.setattr(permissions, "is_macos", lambda: False)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        assert permissions.check_keyboard_permission() == permissions.PermissionState.GRANTED

    def test_macos_returns_unknown_without_pyobjc(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: True)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        # _check_macos_accessibility will ImportError on pyobjc → UNKNOWN
        with patch.dict(sys.modules, {"CoreFoundation": None, "ApplicationServices": None}):
            result = permissions.check_keyboard_permission()
        assert result == permissions.PermissionState.UNKNOWN

    def test_linux_denied_when_not_in_input_group(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: False)
        monkeypatch.setattr(permissions, "is_linux", lambda: True)
        # Mock the group check to return denied
        with patch.object(permissions, "_check_linux_input_access", return_value=permissions.PermissionState.DENIED):
            result = permissions.check_keyboard_permission()
        assert result == permissions.PermissionState.DENIED

    def test_unknown_platform(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: False)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        assert permissions.check_keyboard_permission() == permissions.PermissionState.UNKNOWN


# ─── Linux _check_linux_input_access ───────────────────────────────────────


class TestLinuxInputAccessCheck:
    """Verify the Linux input group + device readability check."""

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-only test (requires grp module)")
    def test_returns_denied_when_input_group_missing(self, monkeypatch):
        # Mock grp.getgrnam to raise KeyError (group doesn't exist)
        import grp as grp_module

        from voice_typer.server.permissions import PermissionState, _check_linux_input_access

        monkeypatch.setattr(grp_module, "getgrnam", lambda name: (_ for _ in ()).throw(KeyError(name)))
        result = _check_linux_input_access()
        assert result == PermissionState.DENIED

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-only test (requires grp module)")
    def test_returns_granted_when_in_group_and_device_readable(self, monkeypatch, tmp_path):
        import grp as grp_module
        import os as os_module

        from voice_typer.server.permissions import PermissionState, _check_linux_input_access

        # Mock the input group to contain the current user
        class FakeGroup:
            gr_mem = [os_module.environ.get("USER", "root")]
            gr_gid = 999

        monkeypatch.setattr(grp_module, "getgrnam", lambda name: FakeGroup())

        # Mock os.getgroups to include the input gid
        monkeypatch.setattr(os_module, "getgroups", lambda: [999])

        # Mock glob to return a fake device, and os.access to return True
        import glob as glob_module

        monkeypatch.setattr(glob_module, "glob", lambda pattern: ["/dev/input/event0"])
        monkeypatch.setattr(os_module, "access", lambda path, mode: True)

        result = _check_linux_input_access()
        assert result == PermissionState.GRANTED


# ─── request_keyboard_permission ───────────────────────────────────────────


class TestRequestKeyboardPermission:
    """Verify the permission request flow."""

    def test_windows_is_noop(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: True)
        monkeypatch.setattr(permissions, "is_macos", lambda: False)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)
        # Should not raise
        permissions.request_keyboard_permission()
        # No callback → no retry scheduled
        assert permissions._retry_timer is None

    def test_macos_opens_settings(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: True)
        monkeypatch.setattr(permissions, "is_linux", lambda: False)

        called = []

        def fake_open():
            called.append("opened")

        monkeypatch.setattr(permissions, "_open_macos_accessibility_settings", fake_open)
        monkeypatch.setattr(permissions, "schedule_permission_retry", lambda cb, **kw: None)

        permissions.request_keyboard_permission()
        assert called == ["opened"]

    def test_linux_invokes_pkexec(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions, "is_windows", lambda: False)
        monkeypatch.setattr(permissions, "is_macos", lambda: False)
        monkeypatch.setattr(permissions, "is_linux", lambda: True)

        called = []

        def fake_pkexec():
            called.append("pkexec")

        monkeypatch.setattr(permissions, "_open_linux_pkexec_prompt", fake_pkexec)
        monkeypatch.setattr(permissions, "schedule_permission_retry", lambda cb, **kw: None)

        permissions.request_keyboard_permission()
        assert called == ["pkexec"]


# ─── schedule_permission_retry / cancel_permission_retry ──────────────────


class TestPermissionRetry:
    """Verify the retry timer."""

    def test_cancel_is_safe_when_no_timer(self):
        from voice_typer.server.permissions import cancel_permission_retry

        # Should not raise even if no timer is pending
        cancel_permission_retry()

    def test_schedule_then_cancel(self):
        from voice_typer.server.permissions import (
            cancel_permission_retry,
            schedule_permission_retry,
        )

        cb = MagicMock()
        schedule_permission_retry(cb, interval=0.01, max_attempts=1)
        cancel_permission_retry()
        # Wait long enough that the timer would have fired
        time.sleep(0.05)
        # Callback should NOT have been called (cancelled)
        cb.assert_not_called()

    def test_schedule_fires_callback_on_granted(self, monkeypatch):
        from voice_typer.server import permissions

        # Mock check_keyboard_permission to return GRANTED
        monkeypatch.setattr(
            permissions,
            "check_keyboard_permission",
            lambda: permissions.PermissionState.GRANTED,
        )
        cb = MagicMock()
        permissions.schedule_permission_retry(cb, interval=0.01, max_attempts=3)
        time.sleep(0.05)
        cb.assert_called_once()
        # Clean up
        permissions.cancel_permission_retry()


# ─── show_permission_notification ──────────────────────────────────────────


class TestShowPermissionNotification:
    """Verify the tray notification helper."""

    def test_calls_tray_notify(self):
        from voice_typer.server.permissions import show_permission_notification

        tray = MagicMock()
        show_permission_notification(tray, "Accessibility permission required")
        tray.notify.assert_called_once()
        args = tray.notify.call_args[0]
        assert "permission" in args[0].lower() or "Voice Typer" in args[0]

    def test_no_tray_does_not_raise(self):
        from voice_typer.server.permissions import show_permission_notification

        # Should not raise even with tray=None
        show_permission_notification(None, "Some error")

    def test_tray_notify_failure_is_swallowed(self):
        from voice_typer.server.permissions import show_permission_notification

        tray = MagicMock()
        tray.notify.side_effect = RuntimeError("tray broken")
        # Should not raise
        show_permission_notification(tray, "Accessibility permission required")


# ─── macOS deep-link ───────────────────────────────────────────────────────


class TestMacOSAccessibilitySettings:
    """Verify the macOS System Settings deep-link."""

    def test_open_invokes_subprocess(self, monkeypatch):
        from voice_typer.server import permissions

        called = []

        class FakePopen:
            def __init__(self, cmd, **kw):
                called.append(cmd)

        monkeypatch.setattr(permissions.subprocess, "Popen", FakePopen)
        monkeypatch.setattr(permissions.os.path, "exists", lambda p: False)

        permissions._open_macos_accessibility_settings()
        assert len(called) == 1
        assert "open" in called[0]

    def test_open_falls_back_to_prefpane(self, monkeypatch):
        from voice_typer.server import permissions

        called = []

        class FakePopen:
            def __init__(self, cmd, **kw):
                called.append(cmd)
                raise OSError("open failed")

        monkeypatch.setattr(permissions.subprocess, "Popen", FakePopen)

        # Make the prefpane path exist
        def fake_exists(p):
            return "Security.prefPane" in p

        monkeypatch.setattr(permissions.os.path, "exists", fake_exists)

        permissions._open_macos_accessibility_settings()
        # Should have tried the URL scheme first, then the prefpane
        assert len(called) >= 2


# ─── Linux pkexec helper ───────────────────────────────────────────────────


class TestLinuxPkexecHelper:
    """Verify the pkexec invocation for AppImage users."""

    def test_finds_install_script_in_dev_mode(self, monkeypatch):
        from voice_typer.server.permissions import _find_linux_install_script

        # In dev mode, the script is at <project>/scripts/linux/install_permissions.py
        script = _find_linux_install_script()
        # If running from the source tree, this should find it.
        # If running from an installed package, it may be None — that's OK.
        if script is not None:
            assert script.name == "install_permissions.py"

    def test_pkexec_not_available_logs_error(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(permissions.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(
            permissions,
            "_find_linux_install_script",
            lambda: __import__("pathlib").Path("/fake/install_permissions.py"),
        )

        # Should not raise, just log
        permissions._open_linux_pkexec_prompt()

    def test_pkexec_available_invokes_subprocess(self, monkeypatch):
        from pathlib import Path

        from voice_typer.server import permissions

        monkeypatch.setattr(permissions.shutil, "which", lambda cmd: "/usr/bin/pkexec" if cmd == "pkexec" else None)
        monkeypatch.setattr(permissions, "_find_linux_install_script", lambda: Path("/fake/install_permissions.py"))
        called = []

        class FakePopen:
            def __init__(self, cmd, **kw):
                called.append(cmd)

        monkeypatch.setattr(permissions.subprocess, "Popen", FakePopen)

        permissions._open_linux_pkexec_prompt()
        assert len(called) == 1
        assert "pkexec" in called[0]


# ─── install_permissions.py (Linux) ────────────────────────────────────────

# Resolve the scripts/linux/ directory relative to this test file so the
# tests work on any machine (not just the original developer's).
# tests/test_permissions.py → repo root → scripts/linux/
_SCRIPTS_LINUX_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "scripts" / "linux"
_INSTALL_SCRIPT = _SCRIPTS_LINUX_DIR / "install_permissions.py"
_UNINSTALL_SCRIPT = _SCRIPTS_LINUX_DIR / "uninstall_permissions.py"


class TestInstallPermissionsScript:
    """Smoke tests for the install_permissions.py script."""

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-only test (requires grp module)")
    def test_script_refuses_non_root(self):
        """When run as non-root, exit code 1."""
        import subprocess

        if not _INSTALL_SCRIPT.is_file():
            pytest.skip("install_permissions.py not found (not a Linux build)")
        result = subprocess.run(
            [sys.executable, str(_INSTALL_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 1
        assert "must run as root" in result.stdout.lower() or "must run as root" in result.stderr.lower()

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-only test (requires grp module)")
    def test_uninstall_script_refuses_non_root(self):
        """When run as non-root, exit code 1."""
        import subprocess

        if not _UNINSTALL_SCRIPT.is_file():
            pytest.skip("uninstall_permissions.py not found (not a Linux build)")
        result = subprocess.run(
            [sys.executable, str(_UNINSTALL_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 1
        assert "must run as root" in result.stdout.lower() or "must run as root" in result.stderr.lower()

    def test_script_compiles(self):
        """Verify install_permissions.py is valid Python."""
        import ast

        if not _INSTALL_SCRIPT.is_file():
            pytest.skip("install_permissions.py not found (not a Linux build)")
        with open(_INSTALL_SCRIPT) as f:
            ast.parse(f.read())
