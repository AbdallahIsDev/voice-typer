"""Regression tests: mic-permission revocation must refuse cleanly."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    yield


def test_probe_returns_denied_on_portaudio_error(monkeypatch):
    from voice_typer.server import permissions
    from voice_typer.server.permissions import mic as mic_mod

    class _PortAudioError(Exception):
        pass

    class _FakeStream:
        def start(self):
            raise _PortAudioError(
                "Error opening InputStream: Unanticipated host error "
                "[PaErrorCode -9999]: 'Undefined external error.' [MME error 1]"
            )

        def stop(self):
            pass

        def close(self):
            pass

    class _FakeSD:
        def InputStream(self, **kw):  # noqa: N802 - mirrors sounddevice API
            return _FakeStream()

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", _FakeSD())
    monkeypatch.setattr(mic_mod, "_windows_microphone_consent_denied", lambda: True)
    state = mic_mod._check_windows_microphone()
    assert state == permissions.MicrophonePermissionState.DENIED


def test_probe_unknown_when_registry_unreadable(monkeypatch):
    """-9999 with unreadable consent store is UNKNOWN, not a false DENIED."""
    from voice_typer.server import permissions
    from voice_typer.server.permissions import mic as mic_mod

    class _PortAudioError(Exception):
        pass

    class _FakeStream:
        def start(self):
            raise _PortAudioError(
                "Error opening InputStream: Unanticipated host error "
                "[PaErrorCode -9999]: 'Undefined external error.' [MME error 1]"
            )

        def stop(self):
            pass

        def close(self):
            pass

    class _FakeSD:
        def InputStream(self, **kw):  # noqa: N802 - mirrors sounddevice API
            return _FakeStream()

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", _FakeSD())
    monkeypatch.setattr(mic_mod, "_windows_microphone_consent_denied", lambda: None)
    state = mic_mod._check_windows_microphone()
    assert state == permissions.MicrophonePermissionState.UNKNOWN


def test_preflight_blocks_denied_start(monkeypatch):
    """End-to-end: probe DENIED (-9999 + registry Deny) blocks start."""
    import voice_typer.server.permissions as permissions_mod
    from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
    from voice_typer.server.permissions import mic as mic_mod
    from voice_typer.server.recording import Recorder

    class _PortAudioError(Exception):
        pass

    class _FakeStream:
        def start(self):
            raise _PortAudioError(
                "Error opening InputStream: Unanticipated host error "
                "[PaErrorCode -9999]: 'Undefined external error.' [MME error 1]"
            )

        def stop(self):
            pass

        def close(self):
            pass

    class _FakeSD:
        def InputStream(self, **kw):  # noqa: N802 - mirrors sounddevice API
            return _FakeStream()

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", _FakeSD())
    monkeypatch.setattr(mic_mod, "_windows_microphone_consent_denied", lambda: True)
    monkeypatch.setattr(permissions_mod, "is_macos", lambda: False)
    monkeypatch.setattr(permissions_mod, "is_windows", lambda: True)
    monkeypatch.setattr(permissions_mod, "is_linux", lambda: False)
    config = MagicMock(
        sample_rate=16000,
        microphone=None,
        max_recording_time_seconds=900,
        pre_roll_buffer_seconds=1.0,
        recording_channels=1,
    )
    rec = Recorder(config)
    rec._recording_event.clear()
    with pytest.raises(MicrophonePermissionDeniedError):
        rec.start()


def test_fallback_sweep_cannot_override_denial(monkeypatch):
    import voice_typer.server.permissions as permissions_mod
    from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
    from voice_typer.server.recording.stream_lifecycle import StreamLifecycle

    def _raise_denied():
        raise MicrophonePermissionDeniedError("denied", state="denied")

    monkeypatch.setattr(permissions_mod, "verify_microphone_accessible", _raise_denied)
    lifecycle = StreamLifecycle(recorder=MagicMock())
    recorder = MagicMock()
    with pytest.raises(MicrophonePermissionDeniedError):
        lifecycle.open_stream_fallback(recorder, [], MagicMock(), 16000, None)
    recorder._devices._all_input_device_candidates.assert_not_called()


def test_denied_hotkey_press_kicks_no_model_load():
    import voice_typer.server.permissions as permissions_mod
    from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
    from voice_typer.server.recording_lifecycle import RecordingLifecycle

    app = MagicMock(name="app")
    app.recorder = MagicMock(name="recorder")
    app.recorder.recording = False
    app._busy_event = threading.Event()
    app._busy_event.set()
    app._cycle_id = "#1"
    app._cycle_counter = 0
    app.config = MagicMock()
    app.config.voice_biometric_consent = True
    app.tray = MagicMock()
    app._waveform_bubble = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app._schedule_timer = MagicMock()
    app.models = MagicMock()
    app.models.active_transcriber = MagicMock(return_value=None)
    app.models._model_load_thread = None

    def _raise_denied():
        raise MicrophonePermissionDeniedError("denied", state="denied")

    real_verify = permissions_mod.verify_microphone_accessible
    permissions_mod.verify_microphone_accessible = _raise_denied
    try:
        controller = MagicMock(name="controller")
        controller._app = app
        controller._toggle_lock = threading.RLock()
        lifecycle = RecordingLifecycle()
        lifecycle._toggle_impl(controller)
    finally:
        permissions_mod.verify_microphone_accessible = real_verify
    app.models.start_background_load.assert_not_called()
    app.recorder.start.assert_not_called()
    app._waveform_bubble.set_state.assert_called_once_with("permission_revoked")


def test_too_short_path_resets_bubble():
    import threading

    import numpy as np
    from voice_typer.server.recording_lifecycle import RecordingLifecycle

    app = MagicMock(name="app")
    app.recorder = MagicMock(name="recorder")
    app.recorder._dropped_ring_chunks = 0
    app.recorder.last_rms = 0.0
    app.config = MagicMock()
    app.config.sample_rate = 16000
    app.config.bubble_behavior = "show_on_record"
    app.tray = MagicMock()
    app._waveform_bubble = MagicMock()
    app._busyness = MagicMock()
    app._schedule_timer = MagicMock()
    app._restore_volume = MagicMock()
    app._finalize_audio_quality_report = MagicMock()
    app._cycle_id = "#2"
    controller = MagicMock(name="controller")
    controller._app = app
    controller._watchdog_lock = threading.Lock()
    controller._cancel_streaming_session = MagicMock()
    controller._maybe_restart_level_monitor_for_always_visible_bubble = MagicMock()
    lifecycle = RecordingLifecycle()
    lifecycle._run_stop_and_transcribe(controller, np.zeros(1600, dtype=np.float32), "#2")
    app._waveform_bubble.hide.assert_called_once_with()


def test_hklm_deny_blocks_probe_and_fallback(monkeypatch):
    """HKLM global Deny is enough: probe DENIED, no fallback sweep."""
    from voice_typer.server import permissions
    from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
    from voice_typer.server.permissions import mic as mic_mod
    from voice_typer.server.recording.stream_lifecycle import StreamLifecycle

    class _PortAudioError(Exception):
        pass

    class _FakeStream:
        def start(self):
            raise _PortAudioError(
                "Error opening InputStream: Unanticipated host error "
                "[PaErrorCode -9999]: 'Undefined external error.' [MME error 1]"
            )

        def stop(self):
            pass

        def close(self):
            pass

    class _FakeSD:
        def InputStream(self, **kw):  # noqa: N802 - mirrors sounddevice API
            return _FakeStream()

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", _FakeSD())
    monkeypatch.setattr(mic_mod, "_windows_microphone_consent_denied", lambda: True)
    state = mic_mod._check_windows_microphone()
    assert state == permissions.MicrophonePermissionState.DENIED

    def _raise_denied():
        raise MicrophonePermissionDeniedError("denied", state="denied")

    import voice_typer.server.permissions as permissions_mod

    monkeypatch.setattr(permissions_mod, "verify_microphone_accessible", _raise_denied)
    lifecycle = StreamLifecycle(recorder=MagicMock())
    recorder = MagicMock()
    with pytest.raises(MicrophonePermissionDeniedError):
        lifecycle.open_stream_fallback(recorder, [], MagicMock(), 16000, None)

def test_silent_zero_audio_routes_to_permission_refusal():
    """0.0s + zero RMS (privacy-blocked silence) gets the refusal, not idle."""
    import threading

    import numpy as np
    from voice_typer.server.recording_lifecycle import RecordingLifecycle

    app = MagicMock(name="app")
    app.recorder = MagicMock(name="recorder")
    app.recorder._dropped_ring_chunks = 0
    app.recorder.last_rms = 0.0
    app.config = MagicMock()
    app.config.sample_rate = 16000
    app.config.bubble_behavior = "show_on_record"
    app.tray = MagicMock()
    app._waveform_bubble = MagicMock()
    app._busyness = MagicMock()
    app._schedule_timer = MagicMock()
    app._restore_volume = MagicMock()
    app._finalize_audio_quality_report = MagicMock()
    app._cycle_id = "#9"
    controller = MagicMock(name="controller")
    controller._app = app
    controller._watchdog_lock = threading.Lock()
    controller._cancel_streaming_session = MagicMock()
    controller._maybe_restart_level_monitor_for_always_visible_bubble = MagicMock()
    lifecycle = RecordingLifecycle()
    lifecycle._run_stop_and_transcribe(controller, np.zeros(0, dtype=np.float32), "#9")
    app._waveform_bubble.set_state.assert_called_once_with("permission_revoked")
    app._waveform_bubble.hide.assert_not_called()


def test_short_but_audible_audio_still_resets_idle():
    """0.3s of real audio keeps the plain idle reset, not the refusal."""
    import threading

    import numpy as np
    from voice_typer.server.recording_lifecycle import RecordingLifecycle

    app = MagicMock(name="app")
    app.recorder = MagicMock(name="recorder")
    app.recorder._dropped_ring_chunks = 0
    app.recorder.last_rms = 0.05
    app.config = MagicMock()
    app.config.sample_rate = 16000
    app.config.bubble_behavior = "show_on_record"
    app.tray = MagicMock()
    app._waveform_bubble = MagicMock()
    app._busyness = MagicMock()
    app._schedule_timer = MagicMock()
    app._restore_volume = MagicMock()
    app._finalize_audio_quality_report = MagicMock()
    app._cycle_id = "#10"
    controller = MagicMock(name="controller")
    controller._app = app
    controller._watchdog_lock = threading.Lock()
    controller._cancel_streaming_session = MagicMock()
    controller._maybe_restart_level_monitor_for_always_visible_bubble = MagicMock()
    lifecycle = RecordingLifecycle()
    audio = np.ones(4800, dtype=np.float32)
    lifecycle._run_stop_and_transcribe(controller, audio, "#10")
    app._waveform_bubble.hide.assert_called_once_with()
    app._waveform_bubble.set_state.assert_not_called()


class TestRefusalNotificationDedup:
    """Repeat refusals must not stack OS notifications."""

    @pytest.fixture(autouse=True)
    def _fresh_state(self, monkeypatch):
        from voice_typer.server.permissions import refusal_notify

        monkeypatch.setattr(refusal_notify, "_last_notified", None)

    def test_first_notifies_and_immediate_repeat_is_suppressed(self):
        from voice_typer.server.permissions import refusal_notify

        assert refusal_notify.should_notify_refusal("T", "M") is True
        assert refusal_notify.should_notify_refusal("T", "M") is False

    def test_different_message_notifies_immediately(self):
        from voice_typer.server.permissions import refusal_notify

        assert refusal_notify.should_notify_refusal("T", "M") is True
        assert refusal_notify.should_notify_refusal("T", "other") is True

    def test_repeat_after_window_notifies_again(self):
        import time

        from voice_typer.server.permissions import refusal_notify

        expired = time.monotonic() - refusal_notify.REFUSAL_NOTIFY_SUPPRESS_S - 1.0
        refusal_notify._last_notified = (("T", "M"), expired)
        assert refusal_notify.should_notify_refusal("T", "M") is True

    def test_granted_probe_resets_suppression(self, monkeypatch):
        import time

        from voice_typer.server import permissions
        from voice_typer.server.permissions import checker, refusal_notify

        refusal_notify._last_notified = (("T", "M"), time.monotonic())
        # checker.verify_microphone_accessible reads the probe through
        # the package object (`_p.check_microphone_permission`), so the
        # patch seam is the package attribute.
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: checker.MicrophonePermissionState.GRANTED,
        )
        checker.verify_microphone_accessible()
        assert refusal_notify._last_notified is None
        assert refusal_notify.should_notify_refusal("T", "M") is True

    def test_unknown_probe_does_not_reset_suppression(self, monkeypatch):
        import time

        from voice_typer.server import permissions
        from voice_typer.server.permissions import checker, refusal_notify

        refusal_notify._last_notified = (("T", "M"), time.monotonic())
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: checker.MicrophonePermissionState.UNKNOWN,
        )
        checker.verify_microphone_accessible()
        assert refusal_notify._last_notified is not None
        assert refusal_notify.should_notify_refusal("T", "M") is False

    def test_helper_suppresses_os_notification_and_returns_true(self, monkeypatch):
        """Suppressed repeats return True so callers skip their fallback."""
        from voice_typer.server import event_bus
        from voice_typer.server.recording_lifecycle import (
            _notify_permission_denied_with_settings,
        )

        published: list[dict] = []
        monkeypatch.setattr(event_bus, "has_live_transport", lambda: True)
        monkeypatch.setattr(
            event_bus, "publish", lambda msg: published.append(msg) or True
        )
        assert _notify_permission_denied_with_settings("T", "M") is True
        assert _notify_permission_denied_with_settings("T", "M") is True
        notifications = [m for m in published if m.get("type") == "notification"]
        assert len(notifications) == 1

    def test_full_refusal_path_notifies_once_and_keeps_feedback(self, monkeypatch):
        """Two hotkey refusals: one OS notification, feedback every time."""
        import voice_typer.server.recording_lifecycle as recording_lifecycle
        from voice_typer.server import event_bus
        from voice_typer.server.recording_lifecycle import RecordingLifecycle

        published: list[dict] = []
        monkeypatch.setattr(event_bus, "has_live_transport", lambda: True)
        monkeypatch.setattr(
            event_bus, "publish", lambda msg: published.append(msg) or True
        )
        monkeypatch.setattr(
            recording_lifecycle, "i18n", MagicMock(t=lambda key, **kw: key)
        )

        app = MagicMock()
        app.config.bubble_behavior = "show_on_record"
        lifecycle = RecordingLifecycle()
        lifecycle._publish_permission_denied_refusal(app, "denied")
        lifecycle._publish_permission_denied_refusal(app, "denied")

        notifications = [m for m in published if m.get("type") == "notification"]
        assert len(notifications) == 1, "repeat refusal must not stack OS notifications"
        revocations = [
            m for m in published if m.get("type") == "microphone_permission_revoked"
        ]
        assert len(revocations) == 2, "in-app feedback push fires on every refusal"
        assert app._waveform_bubble.set_state.call_count == 2
        assert app.tray.set_state.called


class TestPermissionRevokedBubbleAutoReset:
    """The permission_revoked bubble variant must revert by itself."""

    @staticmethod
    def _refusal_app(behavior: str):
        app = MagicMock()
        app.config.bubble_behavior = behavior
        return app

    @staticmethod
    def _fire_reset(app):
        """Invoke the scheduled bubble-reset callback (delay 5.0s)."""
        from voice_typer.server.recording_lifecycle import (
            _PERMISSION_REVOKED_BUBBLE_SECONDS,
        )

        calls = [
            c
            for c in app._schedule_timer.call_args_list
            if c.args and c.args[0] == _PERMISSION_REVOKED_BUBBLE_SECONDS
        ]
        assert calls, "refusal must schedule the bubble auto-reset timer"
        calls[-1].args[1]()

    def test_always_visible_returns_to_idle(self):
        from voice_typer.server.recording_lifecycle import RecordingLifecycle

        app = self._refusal_app("always_visible")
        app._waveform_bubble.state = "permission_revoked"
        RecordingLifecycle()._publish_permission_denied_refusal(app, "denied")
        self._fire_reset(app)
        app._waveform_bubble.set_state.assert_any_call("idle")
        app._waveform_bubble.hide.assert_not_called()

    def test_show_on_record_hides_after_window(self):
        from voice_typer.server.recording_lifecycle import RecordingLifecycle

        app = self._refusal_app("show_on_record")
        app._waveform_bubble.state = "permission_revoked"
        RecordingLifecycle()._publish_permission_denied_refusal(app, "denied")
        self._fire_reset(app)
        app._waveform_bubble.hide.assert_called_once_with()
        app._waveform_bubble.set_state.assert_any_call("permission_revoked")
        assert ("idle",) not in [c.args for c in app._waveform_bubble.set_state.call_args_list]

    def test_reset_is_skipped_when_state_moved_on(self):
        """A recording that started inside the window is never clobbered."""
        from voice_typer.server.recording_lifecycle import RecordingLifecycle

        app = self._refusal_app("always_visible")
        # Simulate the state moving on before the timer fires.
        app._waveform_bubble.state = "recording"
        RecordingLifecycle()._publish_permission_denied_refusal(app, "denied")
        self._fire_reset(app)
        assert ("idle",) not in [c.args for c in app._waveform_bubble.set_state.call_args_list]
        app._waveform_bubble.hide.assert_not_called()
