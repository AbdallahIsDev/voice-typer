"""DE-2C: regression tests for ``voice_typer/server/permissions.py`` and
related DE-4 / DE-5 / DE-32 fixes.

Coverage map
------------
- **DE-4**: ``MicrophonePermissionDeniedError`` (asr_errors.py) +
  ``verify_microphone_accessible()`` (permissions.py) +
  ``recorder.start()`` pre-flight guard +
  ``_classify_portaudio_open_error`` re-classification of PortAudio
  OSErrors into the typed error when the OS reports DENIED/PROMPT.
- **DE-5**: ``request_microphone_permission()`` +
  ``request_microphone_permission_result()`` +
  ``_open_macos_microphone_settings()`` +
  ``_trigger_macos_microphone_consent_prompt()``.
- **DE-32**: ``_cancelled`` flag guards in
  ``schedule_permission_retry`` / ``cancel_permission_retry`` so an
  in-flight ``_poll`` callback is skipped after a concurrent cancel.

All tests are headless (no real OS / no real PortAudio). Platform
probes are mocked via ``monkeypatch.setattr(permissions, "is_macos",
...)`` etc., matching the convention in ``tests/test_permissions.py``.
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# ─── Shared helpers ────────────────────────────────────────────────────────


def _set_platform(monkeypatch, permissions, *, macos=False, windows=False, linux=False):
    """Patch ``is_macos`` / ``is_windows`` / ``is_linux`` on the
    permissions module so the test runs as if on the chosen platform."""
    monkeypatch.setattr(permissions, "is_macos", lambda: macos)
    monkeypatch.setattr(permissions, "is_windows", lambda: windows)
    monkeypatch.setattr(permissions, "is_linux", lambda: linux)


# ══════════════════════════════════════════════════════════════════════════
# DE-4: MicrophonePermissionDeniedError + verify_microphone_accessible
# ══════════════════════════════════════════════════════════════════════════


class TestMicrophonePermissionDeniedError:
    """DE-4 — typed exception lives in asr_errors.py and is
    ``isinstance``-checkable by the IPC layer."""

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
        """The IPC layer relies on isinstance(err, MicrophonePermissionDeniedError)
        to route to the permission onboarding UI instead of a generic toast."""
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        try:
            raise MicrophonePermissionDeniedError("denied", state="denied")
        except RuntimeError as exc:
            # Confirms isinstance-check works for the broad RuntimeError
            # catch clause that production code uses.
            assert isinstance(exc, MicrophonePermissionDeniedError)


class TestVerifyMicrophoneAccessible:
    """DE-4 — pre-flight guard raises MicrophonePermissionDeniedError
    on DENIED, no-op on GRANTED/PROMPT/UNKNOWN."""

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
        """On macOS NotDetermined (PROMPT), the OS will surface the
        consent dialog on first PortAudio open — we must NOT pre-empt."""
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.PROMPT,
        )
        permissions.verify_microphone_accessible()

    def test_no_raise_on_unknown(self, monkeypatch):
        """UNKNOWN (pyobjc missing on macOS, or unsupported platform)
        does NOT raise — the PortAudio-open re-classification path
        in recorder.py handles the runtime case."""
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.UNKNOWN,
        )
        permissions.verify_microphone_accessible()


# ══════════════════════════════════════════════════════════════════════════
# DE-4: recorder._classify_portaudio_open_error re-classification path
# ══════════════════════════════════════════════════════════════════════════


class _FakeRecorderForClassify:
    """Minimal ``self`` for ``_classify_portaudio_open_error``.

    The method only reads ``self._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS``
    (a class-level tuple on ``Recorder``), so we can avoid constructing
    a full ``Recorder`` instance (which requires a config + PortAudio
    mock) by binding the class attribute on a throwaway namespace.
    """

    def __init__(self):
        from voice_typer.server.recording.recorder import Recorder

        self._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS = (
            Recorder._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS
        )

    def _classify_portaudio_open_error(self, exc):  # type: ignore[no-untyped-def]
        # Late-bound method resolution: pull the real implementation off
        # ``Recorder`` so patches to ``permissions.check_microphone_permission``
        # / ``asr_errors.MicrophonePermissionDeniedError`` are observed.
        from voice_typer.server.recording.recorder import Recorder

        return Recorder._classify_portaudio_open_error(self, exc)


class TestClassifyPortAudioOpenError:
    """DE-4 — OSError-from-PortAudio re-classification into
    MicrophonePermissionDeniedError when the OS reports DENIED/PROMPT."""

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
        # Message doesn't match any of the PortAudio permission-denial
        # substrings — must NOT re-classify even though mic state is DENIED.
        fake._classify_portaudio_open_error(OSError("device unplugged"))

    def test_no_op_when_state_is_granted(self, monkeypatch):
        from voice_typer.server import permissions

        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.GRANTED,
        )
        fake = _FakeRecorderForClassify()
        # Pattern matches but mic state is GRANTED — must NOT re-classify
        # (the real fault is hardware, not permission).
        fake._classify_portaudio_open_error(OSError("No input devices available"))

    def test_no_op_when_state_is_unknown(self, monkeypatch):
        """UNKNOWN (pyobjc missing on macOS) — don't false-positive."""
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
        # __cause__ must chain the original OSError so debuggers see the
        # real PortAudio message.
        assert isinstance(exc_info.value.__cause__, OSError)

    def test_raises_on_prompt_with_matching_pattern(self, monkeypatch):
        """PROMPT (NotDetermined on macOS) is also re-classified —
        the OS would have shown the dialog already if it could."""
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
        """Each substring in _PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS
        triggers re-classification when state is DENIED."""
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


# ══════════════════════════════════════════════════════════════════════════
# DE-4: recorder.start() pre-flight guard integration
# ══════════════════════════════════════════════════════════════════════════


class TestRecorderStartPreflightGuard:
    """DE-4 — recorder.start() must call verify_microphone_accessible()
    BEFORE opening any InputStream, and must re-raise
    MicrophonePermissionDeniedError unchanged."""

    def test_start_raises_when_verify_raises(self, monkeypatch):
        """When verify_microphone_accessible raises
        MicrophonePermissionDeniedError, recorder.start() must propagate
        it — NOT swallow it and proceed to PortAudio."""
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
        from voice_typer.server.recording import Recorder

        # Patch verify_microphone_accessible to raise.
        import voice_typer.server.permissions as permissions_mod

        def _raise_denied():
            raise MicrophonePermissionDeniedError("denied", state="denied")

        monkeypatch.setattr(
            permissions_mod, "verify_microphone_accessible", _raise_denied
        )

        # Build a minimal recorder. ``_recording_event`` must be unset
        # for start() to proceed past the early-return.
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
        """When verify_microphone_accessible is a no-op (state GRANTED),
        recorder.start() must proceed to the PortAudio-open path."""
        from voice_typer.server.recording import Recorder

        import voice_typer.server.permissions as permissions_mod

        # verify_microphone_accessible — no-op.
        monkeypatch.setattr(
            permissions_mod, "verify_microphone_accessible", lambda: None
        )

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
        monkeypatch.setattr(
            recording_pkg.sd, "query_hostapis", lambda idx=None: {"name": "MME"}
        )

        # Should NOT raise from the permission guard. (It may raise later
        # from buffer/state setup if our minimal mock is missing an attr —
        # but the permission guard must not be the cause.)
        try:
            rec.start()
        except MicrophonePermissionDeniedError as exc:
            pytest.fail(f"start() raised MicrophonePermissionDeniedError despite verify being no-op: {exc}")
        except Exception:
            # Other exceptions (incomplete mock) are acceptable for this
            # test — we only care that the permission guard didn't fire.
            pass
        finally:
            # Clean up any state start() may have set.
            with __import__("contextlib").suppress(Exception):
                rec.stop()


# ══════════════════════════════════════════════════════════════════════════
# DE-5: request_microphone_permission + helpers
# ══════════════════════════════════════════════════════════════════════════


class TestRequestMicrophonePermission:
    """DE-5 — mirror of request_keyboard_permission for the microphone."""

    def test_macos_opens_microphone_settings(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=True)
        called = []
        monkeypatch.setattr(
            permissions, "_open_macos_microphone_settings", lambda: called.append("opened")
        )
        monkeypatch.setattr(
            permissions, "_trigger_macos_microphone_consent_prompt", lambda: None
        )
        monkeypatch.setattr(permissions, "schedule_permission_retry", lambda cb, **kw: None)

        permissions.request_microphone_permission()
        assert called == ["opened"]

    def test_macos_triggers_consent_prompt(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=True)
        prompted = []
        monkeypatch.setattr(
            permissions, "_open_macos_microphone_settings", lambda: None
        )
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
        monkeypatch.setattr(
            permissions, "_trigger_macos_microphone_consent_prompt", lambda: None
        )
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
    """DE-5 — IPC-friendly wrapper returns the same dict shape as
    request_keyboard_permission_result."""

    def test_macos_returns_requested_true(self, monkeypatch):
        from voice_typer.server import permissions

        _set_platform(monkeypatch, permissions, macos=True)
        monkeypatch.setattr(permissions, "_open_macos_microphone_settings", lambda: None)
        monkeypatch.setattr(
            permissions, "_trigger_macos_microphone_consent_prompt", lambda: None
        )
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
        monkeypatch.setattr(
            permissions, "_trigger_macos_microphone_consent_prompt", lambda: None
        )

        result = permissions.request_microphone_permission_result()
        assert result["requested"] is False
        assert result["platform"] == "macos"
        assert "subprocess failed" in result["error"]


class TestOpenMacOSMicrophoneSettings:
    """DE-5 — deep-link URL construction for the Microphone pane."""

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
                # ``cmd`` is a list — check whether any element contains
                # the deep-link marker (substring, not equality).
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
    """DE-5 — actively trigger the OS consent dialog via pyobjc."""

    def test_no_op_when_pyobjc_missing(self, monkeypatch):
        """On a dev machine without pyobjc, this must be a silent no-op
        (the OS will prompt on first PortAudio device open instead)."""
        from voice_typer.server import permissions

        # Simulate ImportError for AVFoundation.
        import sys as _sys

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
        # sentinel; the implementation calls ``AVMediaTypeAudio()``.
        media_type_sentinel = MagicMock(name="AVMediaTypeAudio-instance")
        fake_av.AVMediaTypeAudio = MagicMock(
            name="AVMediaTypeAudio-factory", return_value=media_type_sentinel
        )

        import sys as _sys

        monkeypatch.setitem(_sys.modules, "AVFoundation", fake_av)

        # Also stub Foundation (imported inside the function for NSObject).
        fake_foundation = MagicMock()
        monkeypatch.setitem(_sys.modules, "Foundation", fake_foundation)

        permissions._trigger_macos_microphone_consent_prompt()
        assert len(called) == 1
        # The first arg is the result of calling ``AVMediaTypeAudio()``
        # — i.e. the media-type sentinel, not the factory itself.
        assert called[0][0] is media_type_sentinel
        # And the factory was called exactly once.
        fake_av.AVMediaTypeAudio.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════
# DE-32: schedule_permission_retry / cancel_permission_retry race fix
# ══════════════════════════════════════════════════════════════════════════


class TestCancelledFlagBasics:
    """DE-32 — ``_cancelled`` is set under the lock by
    ``cancel_permission_retry`` and reset by ``schedule_permission_retry``."""

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
        # during the test.
        monkeypatch.setattr(
            permissions,
            "check_keyboard_permission",
            lambda: permissions.PermissionState.DENIED,
        )
        try:
            permissions.schedule_permission_retry(
                MagicMock(), interval=10.0, max_attempts=1
            )
            assert permissions._cancelled is False
        finally:
            permissions.cancel_permission_retry()


class TestPollSkipsCallbackAfterCancel:
    """DE-32 — the critical race: ``_poll`` fires in a Timer thread,
    ``check_keyboard_permission()`` returns GRANTED, but
    ``cancel_permission_retry`` runs concurrently BEFORE the callback
    dispatch. ``_poll`` must observe ``_cancelled == True`` under the
    lock and SKIP the callback."""

    def test_callback_not_invoked_when_cancelled_concurrently(self, monkeypatch):
        """Simulate the race: schedule a retry, let the timer fire,
        but cancel JUST BEFORE the callback would be invoked. The
        callback must NOT run."""
        from voice_typer.server import permissions

        callback = MagicMock()

        # We instrument ``check_keyboard_permission`` to call
        # ``cancel_permission_retry`` synchronously — this guarantees
        # the cancel arrives AFTER the timer fires but BEFORE the
        # callback dispatch (because ``_poll`` reads the state, THEN
        # checks the cancelled flag, THEN invokes the callback).
        def _check_then_cancel():
            # Simulate the user granting permission.
            # Then simulate a concurrent shutdown / cancel arriving
            # between the probe and the callback dispatch.
            permissions.cancel_permission_retry()
            return permissions.PermissionState.GRANTED

        monkeypatch.setattr(permissions, "check_keyboard_permission", _check_then_cancel)

        permissions.schedule_permission_retry(callback, interval=0.01, max_attempts=1)
        # Wait long enough for the timer to fire (interval=0.01s).
        time.sleep(0.10)

        assert callback.call_count == 0, (
            "callback should NOT have been invoked because "
            "cancel_permission_retry ran during the _poll window"
        )
        # Cleanup
        permissions.cancel_permission_retry()

    def test_callback_invoked_when_not_cancelled(self, monkeypatch):
        """Baseline: without a concurrent cancel, the callback MUST fire
        when check_keyboard_permission returns GRANTED. This guards
        against over-aggressive cancellation logic breaking the
        legitimate flow."""
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
        """If cancel arrives while _poll is logging (after the GRANTED
        branch but before scheduling the next poll), no next Timer
        should be started. We verify by counting Timer instances."""
        from voice_typer.server import permissions

        original_timer = permissions.threading.Timer
        timer_count = {"n": 0}

        class _CountingTimer(original_timer):  # type: ignore[misc, valid-type]
            def __init__(self, *a, **kw):
                timer_count["n"] += 1
                super().__init__(*a, **kw)

        monkeypatch.setattr(permissions.threading, "Timer", _CountingTimer)

        # DENIED so _poll doesn't take the GRANTED branch; instead it
        # tries to schedule the next poll.
        cancel_called = {"v": False}

        def _check_then_cancel():
            # First call: schedule_permission_retry created one Timer
            # (count == 1). When _poll fires, we cancel — the next-poll
            # branch must NOT create another Timer.
            if not cancel_called["v"]:
                cancel_called["v"] = True
                permissions.cancel_permission_retry()
            return permissions.PermissionState.DENIED

        monkeypatch.setattr(permissions, "check_keyboard_permission", _check_then_cancel)

        callback = MagicMock()
        permissions.schedule_permission_retry(callback, interval=0.01, max_attempts=5)
        time.sleep(0.10)

        # Only the initial Timer from schedule_permission_retry should
        # have been created — the next-poll Timer must NOT have been
        # scheduled because cancel arrived first.
        assert timer_count["n"] == 1, (
            f"expected 1 Timer (initial schedule), got {timer_count['n']} "
            "— _poll scheduled a next-poll Timer despite cancel"
        )
        permissions.cancel_permission_retry()


class TestConcurrentCancelRace:
    """DE-32 — high-concurrency stress test: many threads call
    schedule + cancel simultaneously. The callback should never fire
    after a cancel completes."""

    def test_callback_never_fires_after_final_cancel(self, monkeypatch):
        """Schedule a retry, then concurrently: (1) one thread polls
        the state (returns GRANTED), (2) another thread cancels.
        After both threads complete, the callback must NOT have been
        invoked from a stale poll that raced past the cancel."""
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

        # The callback may or may not have been invoked depending on
        # the exact interleaving, but if it WAS invoked, the cancel
        # must have arrived AFTER the callback (not before). The
        # critical invariant: once cancel_permission_retry() returns,
        # no FUTURE callback invocations may happen.
        call_count_after_cancel = callback.call_count
        # Wait another interval to make sure no stale poll fires.
        time.sleep(0.03)
        assert callback.call_count == call_count_after_cancel, (
            "callback was invoked AFTER cancel_permission_retry returned — "
            "DE-32 race regression"
        )
