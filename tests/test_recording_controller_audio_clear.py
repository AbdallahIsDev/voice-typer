"""normal-completion path (not only on the force-recover-from-stuck-"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
from voice_typer.server.recording_controller import RecordingController


def _make_controller_for_stop() -> RecordingController:
    """Build a RecordingController with just enough state for ``_stop_impl``."""
    ctrl = RecordingController.__new__(RecordingController)
    ctrl._app = MagicMock()
    # Init the locks and fields ``_stop_impl`` / the transcription
    ctrl._toggle_lock = threading.RLock()
    ctrl._streaming_session = None
    ctrl._streaming_session_lock = threading.Lock()
    ctrl._transcription_thread = None
    ctrl._watchdog_firings = 0
    ctrl._watchdog_max_firings = 3
    ctrl._watchdog_lock = threading.Lock()
    ctrl._watchdog_event = threading.Event()
    ctrl._watchdog_stop_event = threading.Event()
    ctrl._watchdog_thread = None
    ctrl._cancelled_cycle_ids = set()
    ctrl._cancelled_cycle_ids_lock = threading.Lock()
    ctrl._current_audio = None
    return ctrl


def test_current_audio_cleared_after_stop_spawns_transcription_thread(monkeypatch):
    """DJ-17: after ``stop()`` returns and the transcription thread has"""
    ctrl = _make_controller_for_stop()
    app = ctrl._app

    # 1.0s of audio so the ``duration < 0.5`` short-circuit doesn't fire.
    sample_rate = 16000
    audio = np.ones(sample_rate, dtype=np.float32) * 0.01
    app.recorder.recording = True
    app.recorder.stop.return_value = audio
    app.recorder.last_rms = 0.05
    app.config.sample_rate = sample_rate
    app._cycle_id = "#1"

    # Block the transcription thread's pipeline.run() on an event so we
    pipeline_started = threading.Event()
    release_pipeline = threading.Event()

    class FakePipeline:
        def __init__(self, app):
            pass

        def run(self, **kwargs):
            pipeline_started.set()
            assert ctrl._current_audio is None, (
                "DJ-17: _current_audio must be None by the time the "
                "transcription thread calls pipeline.run(); pre-fix it "
                "retained the raw audio bytes until the next stop()."
            )
            # Park the thread until the test releases it so we can
            release_pipeline.wait(timeout=2.0)

    monkeypatch.setattr(
        "voice_typer.server.dictation_pipeline.DictationPipeline",
        FakePipeline,
    )

    ctrl.stop()

    # Wait for the transcription thread to start and capture the audio.
    assert pipeline_started.wait(timeout=2.0), (
        "Transcription thread did not start within 2s, DJ-17 test setup is broken"
    )

    # The shared slot must be None now (the thread cleared it after
    assert ctrl._current_audio is None, (
        "DJ-17: _current_audio must be None after the transcription "
        "thread has started; pre-fix it retained the raw audio bytes."
    )

    # Release the parked pipeline so the daemon thread can exit.
    release_pipeline.set()


def test_current_audio_set_to_audio_during_stop_then_cleared_by_thread(monkeypatch):
    """captured audio, then the transcription thread clears it."""
    ctrl = _make_controller_for_stop()
    app = ctrl._app

    sample_rate = 16000
    audio = np.ones(sample_rate, dtype=np.float32) * 0.01
    app.recorder.recording = True
    app.recorder.stop.return_value = audio
    app.recorder.last_rms = 0.05
    app.config.sample_rate = sample_rate
    app._cycle_id = "#2"

    captured_audio_ref = {}

    class FakePipeline:
        def __init__(self, app):
            pass

        def run(self, **kwargs):
            # Snapshot what the thread received + the shared slot state.
            captured_audio_ref["audio_arg"] = kwargs.get("audio")
            captured_audio_ref["slot_after_capture"] = ctrl._current_audio

    monkeypatch.setattr(
        "voice_typer.server.dictation_pipeline.DictationPipeline",
        FakePipeline,
    )

    ctrl.stop()
    # Wait briefly for the daemon thread to run.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and "audio_arg" not in captured_audio_ref:
        time.sleep(0.005)

    assert "audio_arg" in captured_audio_ref, "Transcription thread did not capture audio"
    # The thread received the audio (via the local capture).
    assert captured_audio_ref["audio_arg"] is audio
    # And the shared slot was cleared before run() returned.
    assert captured_audio_ref["slot_after_capture"] is None
    assert ctrl._current_audio is None


def test_force_recover_clear_still_works():
    """
    DJ-17 regression guard: the existing force-recover clear path
    ``_current_audio = None``. The DJ-17 fix must not regress this path.
    """
    ctrl = _make_controller_for_stop()
    ctrl._current_audio = np.ones(100, dtype=np.float32)

    # Force-recover short-circuits if ``_busy_event.is_set()`` (not busy).
    ctrl._app._busy_event.is_set.return_value = False
    ctrl._app._cycle_id = "#3"
    # The force-recover path also stops the watchdog thread, provide
    ctrl._watchdog_thread = None

    ctrl._force_recover_from_stuck_transcription(force=True)

    assert ctrl._current_audio is None, (
        "DJ-17 regression: force-recover path must STILL clear _current_audio (the pre-existing DE-13 contract)."
    )
