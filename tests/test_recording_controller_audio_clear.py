"""DJ-17: ``RecordingController._current_audio`` must be cleared on the
normal-completion path (not only on the force-recover-from-stuck-
transcription path).

Pre-fix, the ONLY clear site was ``_force_recover_from_stuck_transcription``
(recording_controller.py:1030). The normal ``stop()`` path set
``self._current_audio = audio`` (line 622) and relied on the NEXT
``stop()`` call to overwrite the reference — so for a tray app where
the user dictates once and idles for hours, the previous dictation's
raw voice bytes stayed in process memory the entire time. This is both
a memory leak (1-15MB of float32 audio per dictation) and a privacy
regression (the DE-13 goal of dropping the Python-side reference was
only achieved on the rare force-recover path, not the normal path).

The fix clears ``self._current_audio`` inside the transcription thread
immediately after capturing it into a thread-local. These tests pin
that contract: after ``stop()`` returns and the transcription thread
has had a chance to start, ``recording._current_audio`` MUST be None.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
from voice_typer.server.recording_controller import RecordingController


def _make_controller_for_stop() -> RecordingController:
    """Build a RecordingController with just enough state for ``_stop_impl``.

    ``_stop_impl`` reads ``app.recorder.recording``, calls
    ``app.recorder.stop()`` (returns audio), consults
    ``app.config.sample_rate``, and spawns a transcription thread that
    imports ``DictationPipeline``. We mock the pipeline import so the
    thread can run without real models.
    """
    ctrl = RecordingController.__new__(RecordingController)
    ctrl._app = MagicMock()
    # Init the locks and fields ``_stop_impl`` / the transcription
    # thread touch. ``__new__`` skips ``__init__``, so we set them up
    # explicitly (cheaper than constructing the full app).
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
    """DJ-17: after ``stop()`` returns and the transcription thread has
    started, ``_current_audio`` MUST be None.

    The fix captures the audio into a thread-local and immediately
    clears the shared slot. Pre-fix, the slot retained the audio until
    the next ``stop()`` call.
    """
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
    # can observe state mid-flight; release it at the end so the test
    # doesn't hang on a non-daemon join.
    pipeline_started = threading.Event()
    release_pipeline = threading.Event()

    class FakePipeline:
        def __init__(self, app):
            pass

        def run(self, **kwargs):
            pipeline_started.set()
            # assertion: by the time run() is called, the shared
            # slot MUST already be None (the thread captured the audio
            # into a local and cleared the slot before calling run).
            assert ctrl._current_audio is None, (
                "DJ-17: _current_audio must be None by the time the "
                "transcription thread calls pipeline.run(); pre-fix it "
                "retained the raw audio bytes until the next stop()."
            )
            # Park the thread until the test releases it so we can
            # observe the post-start pre-completion state.
            release_pipeline.wait(timeout=2.0)

    monkeypatch.setattr(
        "voice_typer.server.dictation_pipeline.DictationPipeline",
        FakePipeline,
    )

    ctrl.stop()

    # Wait for the transcription thread to start and capture the audio.
    assert pipeline_started.wait(timeout=2.0), (
        "Transcription thread did not start within 2s — DJ-17 test setup is broken"
    )

    # The shared slot must be None now (the thread cleared it after
    # capturing the audio into a local).
    assert ctrl._current_audio is None, (
        "DJ-17: _current_audio must be None after the transcription "
        "thread has started; pre-fix it retained the raw audio bytes."
    )

    # Release the parked pipeline so the daemon thread can exit.
    release_pipeline.set()


def test_current_audio_set_to_audio_during_stop_then_cleared_by_thread(monkeypatch):
    """DJ-17 (supplemental): ``stop()`` sets ``_current_audio`` to the
    captured audio, then the transcription thread clears it.

    This pins the lifecycle: the slot is non-None ONLY between the
    assignment in ``_stop_impl`` and the capture-and-clear in the
    transcription thread.
    """
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
    """DJ-17 regression guard: the existing force-recover clear path
    (``_force_recover_from_stuck_transcription``) still sets
    ``_current_audio = None``. The DJ-17 fix must not regress this path.
    """
    ctrl = _make_controller_for_stop()
    ctrl._current_audio = np.ones(100, dtype=np.float32)

    # Force-recover short-circuits if ``_busy_event.is_set()`` (not busy).
    # Make the app appear busy so the recovery logic runs.
    ctrl._app._busy_event.is_set.return_value = False
    ctrl._app._cycle_id = "#3"
    # The force-recover path also stops the watchdog thread — provide
    # a current_thread-safe setup (None is fine — the  fix guards
    # against None and self-join).
    ctrl._watchdog_thread = None

    ctrl._force_recover_from_stuck_transcription(force=True)

    assert ctrl._current_audio is None, (
        "DJ-17 regression: force-recover path must STILL clear _current_audio (the pre-existing DE-13 contract)."
    )
