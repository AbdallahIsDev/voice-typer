"""regression tests for the recording-controller"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch


def _make_controller_for_mic_id_test() -> tuple:
    """Build a RecordingController with a mocked app whose"""
    from voice_typer.server.recording_controller import RecordingController

    app = MagicMock()
    app._busy_event = threading.Event()
    app._busy_event.set()  # not busy
    app._cycle_counter = 0
    app._cycle_id = "#0"
    app.recorder = MagicMock()
    app.recorder.recording = False
    # The watcher is wired in __init__ via _wire_mic_watcher_hooks;
    app.recorder._devices._mic_watcher = MagicMock()
    app.config = MagicMock()
    app.config.sample_rate = 16000
    app.list_microphones = MagicMock(
        return_value=[
            {"id": "0", "index": 0, "name": "Mic A"},
            {"id": "1", "index": 1, "name": "Mic B"},
            {"id": "5", "index": 5, "name": "Mic E"},
        ]
    )
    ctrl = RecordingController(app)
    return ctrl, app


class TestMicIdTypeMismatch:
    """``_list_active_mic_ids`` returns ints so the watcher's"""

    def test_provider_returns_int_indices_not_str_ids(self) -> None:
        """The provider MUST return ``[m[\"index\"] for m in ...]`` (ints),"""
        ctrl, _app = _make_controller_for_mic_id_test()
        ids = ctrl._list_active_mic_ids()
        assert ids == [0, 1, 5], f"_list_active_mic_ids must return int indices (not str ids); got {ids!r}"
        # All elements must be ints (not strs).
        assert all(isinstance(i, int) for i in ids), (
            f"all mic IDs must be ints; got types {[type(i).__name__ for i in ids]}"
        )

    def test_set_active_mic_id_int_does_not_fire_on_active_mic_lost(self) -> None:
        """
        returns ``[0, 1, 5]`` (ints). The watcher's
        ``_check_active_mic_lost`` MUST NOT fire ``on_active_mic_lost``
        """
        from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher

        ctrl, app = _make_controller_for_mic_id_test()
        # Build a real watcher (no OS bridge needed, we drive
        watcher = MicrophoneDeviceWatcher.__new__(MicrophoneDeviceWatcher)
        watcher._hooks_lock = threading.Lock()
        watcher._active_mic_id = None
        watcher._on_active_mic_lost = None
        watcher._device_id_provider = None
        # Wire the controller's callback + provider.
        watcher.set_on_active_mic_lost(ctrl.on_active_mic_lost)
        watcher.set_device_id_provider(ctrl._list_active_mic_ids)
        # Simulate ``_start_impl`` setting the active mic_id to the int
        watcher.set_active_mic_id(5)

        fired = threading.Event()
        original_callback = watcher._on_active_mic_lost

        def _tracking_callback() -> None:
            fired.set()
            if callable(original_callback):
                # Don't actually run the real callback (it would
                pass

        watcher._on_active_mic_lost = _tracking_callback

        watcher._check_active_mic_lost()

        assert not fired.is_set(), (
            "on_active_mic_lost must NOT fire when the int mic_id "
            "(5) is present in the int-typed device list ([0, 1, 5]). "
            "Pre-fix the provider returned str ids (['0','1','5']) so "
            "the int-vs-str membership check always failed."
        )

    def test_set_active_mic_id_int_fires_when_mic_gone(self) -> None:
        """Sanity check: when the int mic_id is NOT in the provider's"""
        from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher

        ctrl, app = _make_controller_for_mic_id_test()
        watcher = MicrophoneDeviceWatcher.__new__(MicrophoneDeviceWatcher)
        watcher._hooks_lock = threading.Lock()
        watcher._active_mic_id = None
        watcher._on_active_mic_lost = None
        watcher._device_id_provider = None
        watcher.set_on_active_mic_lost(ctrl.on_active_mic_lost)
        watcher.set_device_id_provider(ctrl._list_active_mic_ids)
        # Set active mic_id to 99 (not in [0, 1, 5]).
        watcher.set_active_mic_id(99)

        fired = threading.Event()
        watcher._on_active_mic_lost = lambda: fired.set()

        watcher._check_active_mic_lost()

        assert fired.is_set(), (
            "sanity: on_active_mic_lost MUST fire when the int mic_id (99) is absent from the int-typed device list."
        )


def _make_controller_for_event_test() -> tuple:
    """Build a RecordingController with a mocked app for testing the"""
    from voice_typer.server.recording_controller import RecordingController

    app = MagicMock()
    app._busy_event = threading.Event()
    app._busy_event.set()
    app._cycle_counter = 0
    app._cycle_id = "#0"
    app.recorder = MagicMock()
    app.recorder.recording = False
    app.recorder._devices._mic_watcher = MagicMock()
    app.config = MagicMock()
    app.config.sample_rate = 16000
    app.tray = MagicMock()
    app._schedule_timer = MagicMock()
    ctrl = RecordingController(app)
    return ctrl, app


class TestActiveMicLostPublishesEvent:
    """``microphone_disconnected`` IPC event, mirroring ``on_device_lost``"""

    def test_on_active_mic_lost_publishes_microphone_disconnected(self) -> None:
        """banner for the fast-path (OS-event-driven) unplug case."""
        ctrl, _app = _make_controller_for_event_test()

        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            ctrl.on_active_mic_lost()

        assert mock_publish.called, (
            "on_active_mic_lost must publish the "
            "microphone_disconnected IPC event (pre-fix only "
            "on_device_lost published it)."
        )
        # Find the call with the "type" key == "microphone_disconnected".
        published_events = [
            call.args[0] if call.args else call.kwargs.get("event") for call in mock_publish.call_args_list
        ]
        assert any(
            isinstance(evt, dict) and evt.get("type") == "microphone_disconnected" for evt in published_events
        ), (
            "on_active_mic_lost must publish an event with "
            f'type="microphone_disconnected"; got calls: {mock_publish.call_args_list}'
        )

    def test_on_device_lost_still_publishes_microphone_disconnected(self) -> None:
        """Sanity: ``on_device_lost`` (slow path) continues to publish"""
        ctrl, _app = _make_controller_for_event_test()

        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            ctrl.on_device_lost()

        assert mock_publish.called, (
            "on_device_lost must still publish the microphone_disconnected event after the refactor."
        )
        published_events = [
            call.args[0] if call.args else call.kwargs.get("event") for call in mock_publish.call_args_list
        ]
        assert any(
            isinstance(evt, dict) and evt.get("type") == "microphone_disconnected" for evt in published_events
        ), "on_device_lost must publish type=microphone_disconnected."

    def test_both_paths_use_shared_helper(self) -> None:
        """Both ``on_active_mic_lost`` and ``on_device_lost`` MUST route"""
        ctrl, _app = _make_controller_for_event_test()
        # The helper MUST exist as a bound method on the controller.
        assert hasattr(ctrl, "_publish_microphone_disconnected_event"), (
            "RecordingController must expose the shared _publish_microphone_disconnected_event helper."
        )
        assert callable(ctrl._publish_microphone_disconnected_event), (
            "_publish_microphone_disconnected_event must be callable."
        )


def _make_vad_processor_in_silence_state():
    """one noise filter on (so ``vad_enabled`` returns True), then drive it"""
    from voice_typer.server.vad_processor import VadProcessor, VadState

    cfg = MagicMock()
    cfg.use_silero_vad = False  # force RMS path (no torch in test env)
    cfg.vad_speech_threshold = 0.5
    cfg.vad_silence_threshold = 0.3
    # At least one noise filter on so vad_enabled returns True.
    cfg.noise_filter_highpass = True
    cfg.noise_filter_gate = False
    cfg.noise_filter_eq = False
    cfg.noise_filter_compressor = False
    cfg.noise_filter_limiter = False
    cfg.noise_filter_notch = False
    cfg.noise_suppression_method = "none"
    # Use the default grey-zone hold limit (30 frames).
    cfg.vad_grey_zone_hold_limit = 30

    vp = VadProcessor(cfg)
    # Drive into SILENCE: feed > _silence_frames (15) quiet frames.
    for _ in range(20):
        vp.update_frame(-60.0)
    assert vp.state == VadState.SILENCE, f"test setup: expected SILENCE after 20 quiet frames, got {vp.state}"
    return vp


class TestGreyZonePromote:
    """after ``_grey_zone_hold_limit`` (30) consecutive grey"""

    def test_grey_zone_promote_transitions_to_speech(self) -> None:
        """Feed 30 grey frames to hit the hold limit (seeds"""
        from voice_typer.server.vad_processor import VadState

        vp = _make_vad_processor_in_silence_state()
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0
        assert vp.silence_threshold_db < grey_db < vp.speech_threshold_db, (
            "test setup: grey_db must be between the silence and speech thresholds"
        )

        # Feed 30 grey frames, the 30th hits the hold limit and seeds
        for _ in range(30):
            vp.update_frame(grey_db)
        # After the seed: state is still SILENCE (speech_frames = 2 <
        assert vp.state == VadState.SILENCE, (
            "after 30 grey frames (seed), state must still be "
            f"SILENCE (transition fires on the NEXT grey frame); got {vp.state}"
        )
        assert vp.consecutive_speech_frames == vp._speech_frames - 1, (
            "after 30 grey frames, speech_frames must be seeded "
            f"to _speech_frames - 1 = {vp._speech_frames - 1}; "
            f"got {vp.consecutive_speech_frames}"
        )

        # Feed 1 more grey frame, tips the state machine into SPEECH.
        vp.update_frame(grey_db)
        assert vp.state == VadState.SPEECH, (
            f"after 31 grey frames (30 seed + 1 tip), state must transition to SPEECH; got {vp.state}"
        )

    def test_grey_zone_promote_does_not_fire_before_hold_limit(self) -> None:
        """SILENCE state MUST NOT promote to SPEECH, the seed only fires"""
        from voice_typer.server.vad_processor import VadState

        vp = _make_vad_processor_in_silence_state()
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0

        # Feed 29 grey frames (one short of the limit).
        for _ in range(29):
            vp.update_frame(grey_db)

        assert vp.state == VadState.SILENCE, (
            f"29 grey frames (below hold limit) must NOT promote to SPEECH; got {vp.state}"
        )
        assert vp.consecutive_speech_frames == 0, (
            f"before the hold limit, speech_frames must stay 0 (no seed yet); got {vp.consecutive_speech_frames}"
        )

    def test_grey_zone_promote_resets_grey_counter_after_seed(self) -> None:
        """After the seed fires at the hold limit, the grey counter"""
        vp = _make_vad_processor_in_silence_state()
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0

        # Feed 30 grey frames, the 30th triggers the seed + reset.
        for _ in range(30):
            vp.update_frame(grey_db)

        assert vp._consecutive_grey_frames == 0, (
            f"after the seed fires, _consecutive_grey_frames must reset to 0; got {vp._consecutive_grey_frames}"
        )
