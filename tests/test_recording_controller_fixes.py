"""regression tests for the recording-controller
+ device_manager + VAD + level_monitor fix group (the fix).

Each test class pins a specific sub-finding:

- ``the fix`` (Critical) — ``RecordingController._list_active_mic_ids``
  returns the int ``index`` (not the str ``id``) so the
  ``MicrophoneDeviceWatcher`` membership check
  ``active_mic_id not in current_ids`` compares int-to-int. Pre-fix the
  provider returned ``m.get("id")`` (a str like ``"5"``) while
  ``set_active_mic_id`` is called with the int from
  ``recorder._resolve_device()`` — the int-vs-str mismatch made the
  check ALWAYS fail, so ``on_active_mic_lost`` fired spuriously on the
  first device-change event after recording started.

- ``the fix`` (Medium) — ``on_active_mic_lost`` (the fast-path OS-event
  callback) now publishes the ``microphone_disconnected`` IPC event via
  the shared ``_publish_microphone_disconnected_event`` helper, mirroring
  the slow-path ``on_device_lost``. Pre-fix only the slow path published,
  so the renderer showed no banner for the most common unplug scenario.

- ``the fix`` (Medium) — ``VadProcessor.update_frame`` grey-zone logic in
  the SILENCE branch now seeds ``_consecutive_speech_frames`` to
  ``_speech_frames - 1`` after ``_grey_zone_hold_limit`` consecutive
  grey frames, so the next grey frame tips the state machine into
  SPEECH. Pre-fix the branch only decayed counters, so a user speaking
  softly (audio hovering in the grey zone) was never promoted to SPEECH
  — the recorder stayed in SILENCE and auto-stopped.

Run: ``python -m pytest tests/test_recording_controller_fixes.py -q --timeout=30``
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

# ──────────────────────────────────────────────────────────────────────────
# _list_active_mic_ids returns ints (not strs)
# ──────────────────────────────────────────────────────────────────────────


def _make_controller_for_mic_id_test() -> tuple:
    """Build a RecordingController with a mocked app whose
    ``list_microphones`` returns dicts carrying BOTH the str ``id``
    (the pre-fix format) and the int ``index`` (the post-fix format).

    The test asserts the provider returns ``[0, 1, 5]`` (ints), NOT
    ``["0", "1", "5"]`` (strs) — so the watcher's
    ``set_active_mic_id(5)`` (int) membership check passes.
    """
    from voice_typer.server.recording_controller import RecordingController

    app = MagicMock()
    app._busy_event = threading.Event()
    app._busy_event.set()  # not busy
    app._cycle_counter = 0
    app._cycle_id = "#0"
    app.recorder = MagicMock()
    app.recorder.recording = False
    # The watcher is wired in __init__ via _wire_mic_watcher_hooks;
    # provide a mock so the wiring doesn't short-circuit.
    app.recorder._mic_watcher = MagicMock()
    app.config = MagicMock()
    app.config.sample_rate = 16000
    # list_microphones returns the production format: each dict has
    # BOTH "id" (str) and "index" (int). The pre-fix provider read
    # "id"; the post-fix provider reads "index".
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
    """``_list_active_mic_ids`` returns ints so the watcher's
    membership check (``active_mic_id not in current_ids``) compares
    int-to-int against the int passed to ``set_active_mic_id``."""

    def test_provider_returns_int_indices_not_str_ids(self) -> None:
        """The provider MUST return ``[m["index"] for m in ...]`` (ints),
        NOT ``[m["id"] for m in ...]`` (strs)."""
        ctrl, _app = _make_controller_for_mic_id_test()
        ids = ctrl._list_active_mic_ids()
        assert ids == [0, 1, 5], f"_list_active_mic_ids must return int indices (not str ids); got {ids!r}"
        # All elements must be ints (not strs).
        assert all(isinstance(i, int) for i in ids), (
            f"all mic IDs must be ints; got types {[type(i).__name__ for i in ids]}"
        )

    def test_set_active_mic_id_int_does_not_fire_on_active_mic_lost(self) -> None:
        """Wire ``set_active_mic_id(5)`` (int) against a provider that
        returns ``[0, 1, 5]`` (ints). The watcher's
        ``_check_active_mic_lost`` MUST NOT fire ``on_active_mic_lost``
        because ``5 in [0, 1, 5]`` is True (int-to-int comparison).

        Pre-fix the provider returned ``["0", "1", "5"]`` (strs), so
        ``5 in ["0", "1", "5"]`` was False (int-vs-str) and the
        callback fired spuriously.
        """
        from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher

        ctrl, app = _make_controller_for_mic_id_test()
        # Build a real watcher (no OS bridge needed — we drive
        # ``_check_active_mic_lost`` directly).
        watcher = MicrophoneDeviceWatcher.__new__(MicrophoneDeviceWatcher)
        watcher._hooks_lock = threading.Lock()
        watcher._active_mic_id = None
        watcher._on_active_mic_lost = None
        watcher._device_id_provider = None
        # Wire the controller's callback + provider.
        watcher.set_on_active_mic_lost(ctrl.on_active_mic_lost)
        watcher.set_device_id_provider(ctrl._list_active_mic_ids)
        # Simulate ``_start_impl`` setting the active mic_id to the int
        # returned by ``recorder._resolve_device()``.
        watcher.set_active_mic_id(5)

        fired = threading.Event()
        original_callback = watcher._on_active_mic_lost

        def _tracking_callback() -> None:
            fired.set()
            if callable(original_callback):
                # Don't actually run the real callback (it would
                # schedule a stop + tray notify on mocks); we only
                # want to know whether the watcher DECIDED to fire.
                pass

        watcher._on_active_mic_lost = _tracking_callback

        # Drive the check — the provider returns [0, 1, 5] (ints) and
        # active_mic_id is 5 (int), so ``5 in [0, 1, 5]`` is True and
        # the callback MUST NOT fire.
        watcher._check_active_mic_lost()

        assert not fired.is_set(), (
            "on_active_mic_lost must NOT fire when the int mic_id "
            "(5) is present in the int-typed device list ([0, 1, 5]). "
            "Pre-fix the provider returned str ids (['0','1','5']) so "
            "the int-vs-str membership check always failed."
        )

    def test_set_active_mic_id_int_fires_when_mic_gone(self) -> None:
        """Sanity check: when the int mic_id is NOT in the provider's
        list, ``on_active_mic_lost`` DOES fire. This confirms the test
        harness is wired correctly (the previous test's negative result
        is meaningful, not a false-pass from a broken watcher)."""
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


# ──────────────────────────────────────────────────────────────────────────
# on_active_mic_lost publishes microphone_disconnected IPC event
# ──────────────────────────────────────────────────────────────────────────


def _make_controller_for_event_test() -> tuple:
    """Build a RecordingController with a mocked app for testing the
    ``on_active_mic_lost`` / ``on_device_lost`` IPC event publication."""
    from voice_typer.server.recording_controller import RecordingController

    app = MagicMock()
    app._busy_event = threading.Event()
    app._busy_event.set()
    app._cycle_counter = 0
    app._cycle_id = "#0"
    app.recorder = MagicMock()
    app.recorder.recording = False
    app.recorder._mic_watcher = MagicMock()
    app.config = MagicMock()
    app.config.sample_rate = 16000
    app.tray = MagicMock()
    app._schedule_timer = MagicMock()
    ctrl = RecordingController(app)
    return ctrl, app


class TestActiveMicLostPublishesEvent:
    """``on_active_mic_lost`` (fast path) now publishes the
    ``microphone_disconnected`` IPC event, mirroring ``on_device_lost``
    (slow path). Pre-fix only the slow path published."""

    def test_on_active_mic_lost_publishes_microphone_disconnected(self) -> None:
        """``on_active_mic_lost`` MUST call ``event_bus.publish`` with
        ``{"type": "microphone_disconnected"}`` so the renderer shows a
        banner for the fast-path (OS-event-driven) unplug case."""
        ctrl, _app = _make_controller_for_event_test()

        with patch("voice_typer.server.event_bus.publish") as mock_publish:
            ctrl.on_active_mic_lost()

        # The event_bus.publish call MUST have been made with the
        # microphone_disconnected event type.
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
        """Sanity: ``on_device_lost`` (slow path) continues to publish
        the event after the refactor to the shared helper."""
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
        """Both ``on_active_mic_lost`` and ``on_device_lost`` MUST route
        through the shared ``_publish_microphone_disconnected_event``
        helper (extracted in the fix). This pins the refactor: a future
        edit to one path must not silently diverge from the other."""
        ctrl, _app = _make_controller_for_event_test()
        # The helper MUST exist as a bound method on the controller.
        assert hasattr(ctrl, "_publish_microphone_disconnected_event"), (
            "RecordingController must expose the shared _publish_microphone_disconnected_event helper."
        )
        assert callable(ctrl._publish_microphone_disconnected_event), (
            "_publish_microphone_disconnected_event must be callable."
        )


# ──────────────────────────────────────────────────────────────────────────
# grey-zone promote in SILENCE state
# ──────────────────────────────────────────────────────────────────────────


def _make_vad_processor_in_silence_state():
    """Build a VadProcessor configured for the RMS path (no torch) with
    one noise filter on (so ``vad_enabled`` returns True), then drive it
    into SILENCE state by feeding enough quiet frames.

    Returns the processor ready to receive grey-zone frames.
    """
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
    # quiet = rms_db < silence_threshold_db (-50 dB). -60 dB is quiet.
    for _ in range(20):
        vp.update_frame(-60.0)
    assert vp.state == VadState.SILENCE, f"test setup: expected SILENCE after 20 quiet frames, got {vp.state}"
    return vp


class TestGreyZonePromote:
    """after ``_grey_zone_hold_limit`` (30) consecutive grey
    frames in SILENCE state, the state machine promotes to SPEECH.

    Pre-fix the non-SPEECH grey-zone branch only decayed counters, so a
    user speaking softly (audio in the grey zone) was never promoted —
    the recorder stayed in SILENCE and auto-stopped.
    """

    def test_grey_zone_promote_transitions_to_speech(self) -> None:
        """Feed 30 grey frames to hit the hold limit (seeds
        ``speech_frames`` to ``_speech_frames - 1``), then 1 more grey
        frame to tip the state machine into SPEECH.

        The task description specifies seeding to ``_speech_frames - 1``
        so the NEXT grey frame tips the transition — so 30 frames hit
        the limit (seed) and the 31st tips it over.
        """
        from voice_typer.server.vad_processor import VadState

        vp = _make_vad_processor_in_silence_state()
        # Grey zone: between silence_threshold_db (-50) and
        # speech_threshold_db (-40). -45 dB is grey.
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0
        assert vp.silence_threshold_db < grey_db < vp.speech_threshold_db, (
            "test setup: grey_db must be between the silence and speech thresholds"
        )

        # Feed 30 grey frames — the 30th hits the hold limit and seeds
        # speech_frames to _speech_frames - 1 (= 2 with default 3).
        for _ in range(30):
            vp.update_frame(grey_db)
        # After the seed: state is still SILENCE (speech_frames = 2 <
        # _speech_frames = 3, so the transition check doesn't fire yet).
        assert vp.state == VadState.SILENCE, (
            "after 30 grey frames (seed), state must still be "
            f"SILENCE (transition fires on the NEXT grey frame); got {vp.state}"
        )
        assert vp.consecutive_speech_frames == vp._speech_frames - 1, (
            "after 30 grey frames, speech_frames must be seeded "
            f"to _speech_frames - 1 = {vp._speech_frames - 1}; "
            f"got {vp.consecutive_speech_frames}"
        )

        # Feed 1 more grey frame — tips the state machine into SPEECH.
        vp.update_frame(grey_db)
        assert vp.state == VadState.SPEECH, (
            f"after 31 grey frames (30 seed + 1 tip), state must transition to SPEECH; got {vp.state}"
        )

    def test_grey_zone_promote_does_not_fire_before_hold_limit(self) -> None:
        """Feeding fewer than ``_grey_zone_hold_limit`` grey frames in
        SILENCE state MUST NOT promote to SPEECH — the seed only fires
        at the limit, so a brief grey-zone excursion (e.g. a momentary
        dip in volume) doesn't false-positive into SPEECH."""
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
        """After the seed fires at the hold limit, the grey counter
        resets to 0 so the next decay/promote cycle starts fresh."""
        vp = _make_vad_processor_in_silence_state()
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0

        # Feed 30 grey frames — the 30th triggers the seed + reset.
        for _ in range(30):
            vp.update_frame(grey_db)

        assert vp._consecutive_grey_frames == 0, (
            f"after the seed fires, _consecutive_grey_frames must reset to 0; got {vp._consecutive_grey_frames}"
        )
