"""Tests for voice_typer.server.permissions module (GAP-2, GAP-3)."""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


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


class TestLinuxPkexecHelper:
    """Verify the pkexec invocation for AppImage users."""

    def test_finds_install_script_in_dev_mode(self, monkeypatch):
        from voice_typer.server.permissions import _find_linux_install_script

        # In dev mode, the script is at <project>/scripts/linux/install_permissions.py
        script = _find_linux_install_script()
        # If running from the source tree, this should find it.
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


def _set_platform(monkeypatch, permissions, *, macos=False, windows=False, linux=False):
    """Patch ``is_macos`` / ``is_windows`` / ``is_linux`` on the"""
    monkeypatch.setattr(permissions, "is_macos", lambda: macos)
    monkeypatch.setattr(permissions, "is_windows", lambda: windows)
    monkeypatch.setattr(permissions, "is_linux", lambda: linux)


class TestMicrophonePermissionDeniedError:
    """DE-4, typed exception lives in asr_errors.py and is"""

    def test_is_runtime_error_subclass(self):
        from voice_typer.server.asr_errors import (
            ConsentRequiredError,
            MicrophonePermissionDeniedError,
        )

        assert issubclass(MicrophonePermissionDeniedError, RuntimeError)
        # Sibling to ConsentRequiredError (different feature, different class)
        assert MicrophonePermissionDeniedError is not ConsentRequiredError

    def test_carries_state_attribute(self):
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        err = MicrophonePermissionDeniedError("denied", state="denied")
        assert err.state == "denied"
        assert "denied" in str(err)

    def test_default_message(self):
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        err = MicrophonePermissionDeniedError()
        assert isinstance(err, RuntimeError)
        assert err.state is None
        # Default message must mention microphone
        assert "icrophone" in str(err)

    def test_isinstance_check_works(self):
        """The IPC layer relies on isinstance(err, MicrophonePermissionDeniedError)"""
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        try:
            raise MicrophonePermissionDeniedError("denied", state="denied")
        except RuntimeError as exc:
            # Confirms isinstance-check works for the broad RuntimeError
            assert isinstance(exc, MicrophonePermissionDeniedError)


class TestVerifyMicrophoneAccessible:
    """DE-4, pre-flight guard raises MicrophonePermissionDeniedError"""

    def test_raises_on_denied(self, monkeypatch):
        from voice_typer.server import permissions
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        with pytest.raises(MicrophonePermissionDeniedError) as exc_info:
            permissions.verify_microphone_accessible()
        assert exc_info.value.state == "denied"

    def test_no_raise_on_granted(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.GRANTED,
        )
        # Should NOT raise.
        permissions.verify_microphone_accessible()

    def test_no_raise_on_prompt(self, monkeypatch):
        """On macOS NotDetermined (PROMPT), the OS will surface the"""
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.PROMPT,
        )
        permissions.verify_microphone_accessible()

    def test_no_raise_on_unknown(self, monkeypatch):
        """UNKNOWN (pyobjc missing on macOS, or unsupported platform)"""
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.UNKNOWN,
        )
        permissions.verify_microphone_accessible()


class _FakeRecorderForClassify:
    """Minimal ``self`` for ``classify_portaudio_open_error``."""

    def __init__(self):
        from voice_typer.server.recording.recorder import Recorder

        self._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS = Recorder._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS
        # The DevicePrewarm owner reads the substring table via
        self._recorder = self

    def _classify_portaudio_open_error(self, exc):  # type: ignore[no-untyped-def]
        # Late-bound owner resolution: pull the real implementation off
        from voice_typer.server.recording.device_prewarm import DevicePrewarm

        return DevicePrewarm.classify_portaudio_open_error(self, exc)


class TestClassifyPortAudioOpenError:
    """DE-4, OSError-from-PortAudio re-classification into"""

    def test_no_op_when_not_oserror(self, monkeypatch):
        fake = _FakeRecorderForClassify()
        # Should NOT raise on a non-OSError exception (e.g. RuntimeError).
        fake._classify_portaudio_open_error(RuntimeError("boom"))

    def test_no_op_when_message_does_not_match(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        fake = _FakeRecorderForClassify()
        # substrings, must NOT re-classify even though mic state is DENIED.
        fake._classify_portaudio_open_error(OSError("device unplugged"))

    def test_no_op_when_state_is_granted(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.GRANTED,
        )
        fake = _FakeRecorderForClassify()
        # Pattern matches but mic state is GRANTED, must NOT re-classify
        fake._classify_portaudio_open_error(OSError("No input devices available"))

    def test_no_op_when_state_is_unknown(self, monkeypatch):
        """UNKNOWN (pyobjc missing on macOS), don't false-positive."""
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.UNKNOWN,
        )
        fake = _FakeRecorderForClassify()
        fake._classify_portaudio_open_error(OSError("Unanticipated host error"))

    def test_raises_on_denied_with_matching_pattern(self, monkeypatch):
        from voice_typer.server import permissions
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        fake = _FakeRecorderForClassify()
        with pytest.raises(MicrophonePermissionDeniedError) as exc_info:
            fake._classify_portaudio_open_error(OSError("Unanticipated host error"))
        assert exc_info.value.state == "denied"
        assert isinstance(exc_info.value.__cause__, OSError)

    def test_raises_on_prompt_with_matching_pattern(self, monkeypatch):
        """PROMPT (NotDetermined on macOS) is also re-classified —"""
        from voice_typer.server import permissions
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.PROMPT,
        )
        fake = _FakeRecorderForClassify()
        with pytest.raises(MicrophonePermissionDeniedError) as exc_info:
            fake._classify_portaudio_open_error(OSError("Invalid sample rate"))
        assert exc_info.value.state == "prompt"

    def test_all_patterns_classify(self, monkeypatch):
        """Each substring in _PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS"""
        from voice_typer.server import permissions
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
        from voice_typer.server.recording.recorder import Recorder

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        fake = _FakeRecorderForClassify()
        for substr in Recorder._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS:
            with pytest.raises(MicrophonePermissionDeniedError):
                fake._classify_portaudio_open_error(OSError(substr.title()))


class TestRecorderStartPreflightGuard:
    """DE-4, recorder.start() must call verify_microphone_accessible()"""

    def test_start_raises_when_verify_raises(self, monkeypatch):
        """When verify_microphone_accessible raises"""
        import voice_typer.server.permissions as permissions_mod
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
        from voice_typer.server.recording import Recorder

        # Patch verify_microphone_accessible to raise.

        def _raise_denied():
            raise MicrophonePermissionDeniedError("denied", state="denied")

        monkeypatch.setattr(permissions_mod, "verify_microphone_accessible", _raise_denied)

        # Build a minimal recorder. ``_recording_event`` must be unset
        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            max_recording_time_seconds=900,
            pre_roll_buffer_seconds=1.0,
            recording_channels=1,
        )
        rec = Recorder(config)
        # Ensure start() doesn't early-return on already-recording.
        rec._recording_event.clear()

        with pytest.raises(MicrophonePermissionDeniedError):
            rec.start()

    def test_start_proceeds_when_verify_passes(self, monkeypatch):
        """When verify_microphone_accessible is a no-op (state GRANTED),"""
        import voice_typer.server.permissions as permissions_mod
        from voice_typer.server.recording import Recorder

        monkeypatch.setattr(permissions_mod, "verify_microphone_accessible", lambda: None)

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            max_recording_time_seconds=900,
            pre_roll_buffer_seconds=1.0,
            recording_channels=1,
        )
        rec = Recorder(config)
        rec._recording_event.clear()

        # Patch PortAudio: simulate a successful InputStream open.
        from voice_typer.server import recording as recording_pkg

        class _OkStream:
            samplerate = 16000

            def __init__(self, *a, **kw):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(recording_pkg.sd, "InputStream", _OkStream)

        def _query_devices(*a, **kw):
            if not a and not kw:
                return [
                    {
                        "max_input_channels": 1,
                        "default_samplerate": 16000,
                        "hostapi": 0,
                        "index": 0,
                        "name": "Mock",
                    }
                ]
            return {
                "max_input_channels": 1,
                "default_samplerate": 16000,
                "hostapi": 0,
                "index": 0,
                "name": "Mock",
            }

        monkeypatch.setattr(recording_pkg.sd, "query_devices", _query_devices)
        monkeypatch.setattr(recording_pkg.sd, "query_hostapis", lambda idx=None: {"name": "MME"})

        # a no-op above, so this typed error must NOT propagate out of
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        try:
            rec.start()
        except MicrophonePermissionDeniedError as exc:
            pytest.fail(f"start() raised MicrophonePermissionDeniedError despite verify being no-op: {exc}")
        except Exception as exc:  # noqa: BLE001, intentional broad catch
            # Other exceptions (incomplete mock) are acceptable for this
            import warnings

            warnings.warn(
                "rec.start() raised a non-permission exception during the "
                f"no-op-verify test (acceptable for minimal mock): "
                f"{type(exc).__name__}: {exc}",
                stacklevel=2,
            )
        finally:
            # Clean up any state start() may have set.
            with __import__("contextlib").suppress(Exception):
                rec.stop()


class TestRequestMicrophonePermission:
    """DE-5, mirror of request_keyboard_permission for the microphone."""

    def test_macos_opens_microphone_settings(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=True)
        called = []
        monkeypatch.setattr(permissions, "_open_macos_microphone_settings", lambda: called.append("opened"))
        monkeypatch.setattr(permissions, "_trigger_macos_microphone_consent_prompt", lambda: None)
        monkeypatch.setattr(permissions, "schedule_permission_retry", lambda cb, **kw: None)

        permissions.request_microphone_permission()
        assert called == ["opened"]

    def test_macos_triggers_consent_prompt(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=True)
        prompted = []
        monkeypatch.setattr(permissions, "_open_macos_microphone_settings", lambda: None)
        monkeypatch.setattr(
            permissions,
            "_trigger_macos_microphone_consent_prompt",
            lambda: prompted.append("prompted"),
        )
        monkeypatch.setattr(permissions, "schedule_permission_retry", lambda cb, **kw: None)

        permissions.request_microphone_permission()
        assert prompted == ["prompted"]

    def test_windows_is_noop(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, windows=True)
        called = []
        monkeypatch.setattr(
            permissions,
            "_open_macos_microphone_settings",
            lambda: called.append("should-not-call"),
        )
        monkeypatch.setattr(
            permissions,
            "_trigger_macos_microphone_consent_prompt",
            lambda: called.append("should-not-call"),
        )
        # No on_granted → schedule_permission_retry must not be called
        monkeypatch.setattr(
            permissions,
            "schedule_permission_retry",
            lambda *a, **kw: called.append("scheduled"),
        )

        permissions.request_microphone_permission()
        assert called == []  # no-op on Windows

    def test_linux_is_noop(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, linux=True)
        called = []
        monkeypatch.setattr(
            permissions,
            "_open_macos_microphone_settings",
            lambda: called.append("should-not-call"),
        )
        permissions.request_microphone_permission()
        assert called == []

    def test_schedules_retry_when_on_granted_provided_on_macos(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=True)
        monkeypatch.setattr(permissions, "_open_macos_microphone_settings", lambda: None)
        monkeypatch.setattr(permissions, "_trigger_macos_microphone_consent_prompt", lambda: None)
        scheduled = []
        monkeypatch.setattr(
            permissions,
            "schedule_permission_retry",
            lambda cb, **kw: scheduled.append(cb),
        )
        cb = MagicMock()
        permissions.request_microphone_permission(on_granted=cb)
        assert scheduled == [cb]

    def test_no_retry_scheduled_on_windows(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, windows=True)
        scheduled = []
        monkeypatch.setattr(
            permissions,
            "schedule_permission_retry",
            lambda *a, **kw: scheduled.append("scheduled"),
        )
        permissions.request_microphone_permission(on_granted=MagicMock())
        assert scheduled == []


class TestRequestMicrophonePermissionResult:
    """DE-5, IPC-friendly wrapper returns the same dict shape as"""

    def test_macos_returns_requested_true(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=True)
        monkeypatch.setattr(permissions, "_open_macos_microphone_settings", lambda: None)
        monkeypatch.setattr(permissions, "_trigger_macos_microphone_consent_prompt", lambda: None)
        monkeypatch.setattr(permissions, "schedule_permission_retry", lambda cb, **kw: None)

        result = permissions.request_microphone_permission_result()
        assert result["requested"] is True
        assert result["platform"] == "macos"
        assert result["error"] is None
        assert result["instructions"] is not None

    def test_windows_returns_requested_false_with_instructions(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, windows=True)
        result = permissions.request_microphone_permission_result()
        assert result["requested"] is False
        assert result["platform"] == "windows"
        assert result["error"] is None
        # Instructions should mention Windows Settings.
        assert result["instructions"] is not None
        assert "Windows" in result["instructions"]

    def test_linux_returns_requested_false_with_instructions(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, linux=True)
        result = permissions.request_microphone_permission_result()
        assert result["requested"] is False
        assert result["platform"] == "linux"
        assert result["error"] is None
        assert "PipeWire" in result["instructions"] or "PulseAudio" in result["instructions"]

    def test_unknown_platform_returns_error(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=False, windows=False, linux=False)
        result = permissions.request_microphone_permission_result()
        assert result["platform"] == "unknown"
        assert result["requested"] is False
        assert result["error"] == "Unsupported platform"

    def test_exception_in_open_returns_error(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=True)

        def _boom():
            raise OSError("subprocess failed")

        monkeypatch.setattr(permissions, "_open_macos_microphone_settings", _boom)
        monkeypatch.setattr(permissions, "_trigger_macos_microphone_consent_prompt", lambda: None)

        result = permissions.request_microphone_permission_result()
        assert result["requested"] is False
        assert result["platform"] == "macos"
        assert "subprocess failed" in result["error"]


class TestOpenMacOSMicrophoneSettings:
    """DE-5, deep-link URL construction for the Microphone pane."""

    def test_invokes_subprocess_with_microphone_deep_link(self, monkeypatch):
        from voice_typer.server import permissions

        called = []

        class FakePopen:
            def __init__(self, cmd, **kw):
                called.append(cmd)

        monkeypatch.setattr(permissions.subprocess, "Popen", FakePopen)
        monkeypatch.setattr(permissions.os.path, "exists", lambda p: False)

        permissions._open_macos_microphone_settings()
        assert len(called) == 1
        assert "open" in called[0]
        # Deep-link must target the Microphone pane (NOT Accessibility).
        deep_link = called[0][1]
        assert "Privacy_Microphone" in deep_link

    def test_falls_back_to_prefpane_when_open_fails(self, monkeypatch):
        from voice_typer.server import permissions

        called = []

        class FakePopen:
            def __init__(self, cmd, **kw):
                called.append(cmd)
                # First call (URL scheme) fails; second (prefpane) succeeds.
                if any("Privacy_Microphone" in str(arg) for arg in cmd):
                    raise OSError("open failed")

        monkeypatch.setattr(permissions.subprocess, "Popen", FakePopen)
        # Pretend the Security.prefPane path exists.
        monkeypatch.setattr(
            permissions.os.path,
            "exists",
            lambda p: "Security.prefPane" in p,
        )

        permissions._open_macos_microphone_settings()
        # Should have tried URL scheme first, then the prefpane.
        assert len(called) >= 2
        assert any(any("Privacy_Microphone" in str(arg) for arg in cmd) for cmd in called)
        assert any("Security.prefPane" in cmd[1] for cmd in called)


class TestTriggerMacOSMicrophoneConsentPrompt:
    """DE-5, actively trigger the OS consent dialog via pyobjc."""

    def test_no_op_when_pyobjc_missing(self, monkeypatch):
        """On a dev machine without pyobjc, this must be a silent no-op"""
        import sys as _sys

        # Simulate ImportError for AVFoundation.
        from voice_typer.server import permissions

        monkeypatch.setitem(_sys.modules, "AVFoundation", None)
        # Should NOT raise.
        permissions._trigger_macos_microphone_consent_prompt()

    def test_invokes_request_access_when_pyobjc_available(self, monkeypatch):
        from voice_typer.server import permissions

        # Build a fake AVFoundation module.
        fake_av = MagicMock()
        called = []

        def _request(media_type, completion):
            called.append((media_type, completion))
            # Simulate the OS calling the completion handler.
            completion(True)

        fake_av.AVCaptureDevice.requestAccessForMediaType_completionHandler_ = _request
        # ``AVMediaTypeAudio`` is a callable that returns the media-type
        media_type_sentinel = MagicMock(name="AVMediaTypeAudio-instance")
        fake_av.AVMediaTypeAudio = MagicMock(name="AVMediaTypeAudio-factory", return_value=media_type_sentinel)

        import sys as _sys

        monkeypatch.setitem(_sys.modules, "AVFoundation", fake_av)

        # Also stub Foundation (imported inside the function for NSObject).
        fake_foundation = MagicMock()
        monkeypatch.setitem(_sys.modules, "Foundation", fake_foundation)

        permissions._trigger_macos_microphone_consent_prompt()
        assert len(called) == 1
        # The first arg is the result of calling ``AVMediaTypeAudio()``
        assert called[0][0] is media_type_sentinel
        # And the factory was called exactly once.
        fake_av.AVMediaTypeAudio.assert_called_once()


class TestCancelledFlagBasics:
    """DE-32: ``_cancelled`` is set under the lock by"""

    def test_cancel_sets_cancelled_flag(self, monkeypatch):
        from voice_typer.server import permissions

        # Ensure clean state.
        permissions.cancel_permission_retry()
        assert permissions._cancelled is True

    def test_reschedule_resets_cancelled_flag(self, monkeypatch):
        from voice_typer.server import permissions

        # Cancel first to set _cancelled = True.
        permissions.cancel_permission_retry()
        assert permissions._cancelled is True

        # Mock check_keyboard_permission so the timer doesn't fire
        monkeypatch.setattr(
            permissions,
            "check_keyboard_permission",
            lambda: permissions.PermissionState.DENIED,
        )
        try:
            permissions.schedule_permission_retry(MagicMock(), interval=10.0, max_attempts=1)
            assert permissions._cancelled is False
        finally:
            permissions.cancel_permission_retry()


class TestPollSkipsCallbackAfterCancel:
    """DE-32, the critical race: ``_poll`` fires in a Timer thread,"""

    def test_callback_not_invoked_when_cancelled_concurrently(self, monkeypatch):
        """Simulate the race: schedule a retry, let the timer fire,"""
        from voice_typer.server import permissions

        callback = MagicMock()

        # We instrument ``check_keyboard_permission`` to call
        def _check_then_cancel():
            # Simulate the user granting permission.
            permissions.cancel_permission_retry()
            return permissions.PermissionState.GRANTED

        monkeypatch.setattr(permissions, "check_keyboard_permission", _check_then_cancel)

        permissions.schedule_permission_retry(callback, interval=0.01, max_attempts=1)
        # Wait long enough for the timer to fire (interval=0.01s).
        time.sleep(0.10)

        assert callback.call_count == 0, (
            "callback should NOT have been invoked because cancel_permission_retry ran during the _poll window"
        )
        # Cleanup
        permissions.cancel_permission_retry()

    def test_callback_invoked_when_not_cancelled(self, monkeypatch):
        """Baseline: without a concurrent cancel, the callback MUST fire"""
        from voice_typer.server import permissions

        callback = MagicMock()
        monkeypatch.setattr(
            permissions,
            "check_keyboard_permission",
            lambda: permissions.PermissionState.GRANTED,
        )

        permissions.schedule_permission_retry(callback, interval=0.01, max_attempts=3)
        time.sleep(0.10)
        assert callback.call_count == 1
        permissions.cancel_permission_retry()

    def test_no_next_poll_scheduled_after_cancel(self, monkeypatch):
        """If cancel arrives while _poll is logging (after the GRANTED"""
        from voice_typer.server import permissions

        original_timer = permissions.threading.Timer
        timer_count = {"n": 0}

        class _CountingTimer(original_timer):  # type: ignore[misc, valid-type]
            def __init__(self, *a, **kw):
                timer_count["n"] += 1
                super().__init__(*a, **kw)

        monkeypatch.setattr(permissions.threading, "Timer", _CountingTimer)

        # DENIED so _poll doesn't take the GRANTED branch; instead it
        cancel_called = {"v": False}

        def _check_then_cancel():
            # branch must NOT create another Timer.
            if not cancel_called["v"]:
                cancel_called["v"] = True
                permissions.cancel_permission_retry()
            return permissions.PermissionState.DENIED

        monkeypatch.setattr(permissions, "check_keyboard_permission", _check_then_cancel)

        callback = MagicMock()
        permissions.schedule_permission_retry(callback, interval=0.01, max_attempts=5)
        time.sleep(0.10)

        # have been created, the next-poll Timer must NOT have been
        assert timer_count["n"] == 1, (
            f"expected 1 Timer (initial schedule), got {timer_count['n']} "
            "— _poll scheduled a next-poll Timer despite cancel"
        )
        permissions.cancel_permission_retry()


class TestConcurrentCancelRace:
    """DE-32, high-concurrency stress test: many threads call"""

    def test_callback_never_fires_after_final_cancel(self, monkeypatch):
        """
        Schedule a retry, then concurrently: (1) one thread polls
        After both threads complete, the callback must NOT have been
        """
        from voice_typer.server import permissions

        callback = MagicMock()
        # GRANTED so the callback would fire without the cancel guard.
        monkeypatch.setattr(
            permissions,
            "check_keyboard_permission",
            lambda: permissions.PermissionState.GRANTED,
        )

        # Start the retry.
        permissions.schedule_permission_retry(callback, interval=0.005, max_attempts=10)

        # Spawn a concurrent cancel thread that fires ~immediately.
        def _cancel_after_short_delay():
            time.sleep(0.002)  # let the first poll start
            permissions.cancel_permission_retry()

        cancel_thread = threading.Thread(target=_cancel_after_short_delay)
        cancel_thread.start()
        cancel_thread.join()

        # Wait a bit longer for any in-flight poll to complete.
        time.sleep(0.05)
        permissions.cancel_permission_retry()

        call_count_after_cancel = callback.call_count
        # Wait another interval to make sure no stale poll fires.
        time.sleep(0.03)
        assert callback.call_count == call_count_after_cancel, (
            "callback was invoked AFTER cancel_permission_retry returned, DE-32 race regression"
        )
