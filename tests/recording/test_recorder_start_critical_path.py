"""Focused tests for the ``start()`` hotkey critical-path warm-up policy.

``start_recording`` (the body of ``Recorder.start``) used to call
``recorder.warm_up_resampler()`` synchronously whenever the device's
effective sample rate differs from the target rate and scipy's
``resample_poly`` was not loaded yet — a 1-2s block on the first
hotkey press after app launch.

The scipy preloader daemon spawned by ``Recorder.__init__``
(``RecorderInitMixin._register_scipy_preloader``) already loads the
resampler in the background, so blocking the hotkey thread for the
same import is wasted latency. The policy now is:

- while the recorder's own preloader thread is alive → SKIP the
  synchronous warm-up (the background thread owns the import; the
  resample helpers load scipy on demand under a lock if a resample
  lands first, so output bytes are identical — only the latency moves
  off the hotkey path);
- otherwise (no preloader, or it already exited without loading
  scipy) → warm up synchronously exactly as before, so the failure is
  logged once at start time.

These tests build a lightweight ``MagicMock`` recorder shaped for
``start_recording`` (a DIFFERENT shape from the secure-clear factory in
``tests/fixtures/recorder_test_helpers.py``, which builds a real
``Recorder`` — see that module's docstring for why per-contract
builders are intentional here), plus one real-Recorder wiring test.
"""

from __future__ import annotations

import threading
from collections import deque
from unittest.mock import MagicMock

import pytest
from voice_typer.server.recording._recorder_split import start_recording


def _build_start_mock_recorder(
    *,
    sample_rate: int = 16000,
    effective_sr: int = 48000,
) -> MagicMock:
    """Minimal ``MagicMock`` recorder that lets ``start_recording`` run
    end-to-end without PortAudio, permissions, or real worker threads."""
    recorder = MagicMock(name="recorder")
    recorder.config = MagicMock(name="config")
    recorder.config.sample_rate = sample_rate
    recorder.config.microphone = None
    recorder.config.save.return_value = True
    recorder._cache_session_config.return_value = 30
    recorder._resolve_device.return_value = 5
    recorder._same_physical_microphone_candidates.return_value = [5]
    recorder._build_audio_callback.return_value = object()
    recorder._open_stream_for_candidates.return_value = (5, effective_sr, None)
    recorder._stream = MagicMock(name="opened-stream")
    recorder._recording_event = threading.Event()
    recorder._audio_processor = None
    recorder._preroll_active = False
    recorder._preroll_seconds = 0.0
    recorder._preroll_buffer = deque(maxlen=0)
    recorder._mic_watcher = None
    recorder.warm_up_resampler = MagicMock(name="warm_up_resampler")
    return recorder


def _patch_resampler_unloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the warm-up branch to fire: scipy not loaded, no cached error."""
    from voice_typer.server.recording import resampling as rec_resampling

    monkeypatch.setattr(rec_resampling, "_resample_poly", None, raising=False)
    monkeypatch.setattr(rec_resampling, "_resample_poly_error", None, raising=False)


class _AliveThreadHandle:
    """A real daemon thread held alive until released, to stand in for
    the ``Recorder.__init__`` scipy preloader mid-import."""

    def __init__(self) -> None:
        self._release = threading.Event()
        self.thread = threading.Thread(
            target=lambda: self._release.wait(timeout=5.0),
            name="fake-scipy-preloader",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self._release.set()
        self.thread.join(timeout=1.0)


class TestStartWarmUpCriticalPathPolicy:
    """The synchronous resampler warm-up runs only when no background
    preloader is in flight."""

    def test_sync_warm_up_called_without_in_flight_preloader(self, monkeypatch):
        """A recorder with no live preloader thread (the attribute is a
        plain object / absent) still warms up synchronously — the
        historical contract for recorders constructed before the
        preloader existed and for test doubles."""
        _patch_resampler_unloaded(monkeypatch)
        recorder = _build_start_mock_recorder()
        recorder._scipy_preloader_thread = object()  # not a Thread

        start_recording(recorder)

        recorder.warm_up_resampler.assert_called_once()

    def test_sync_warm_up_called_when_preloader_thread_dead(self, monkeypatch):
        """A preloader thread that already exited without loading scipy
        (``_resample_poly`` still ``None``) falls back to the synchronous
        warm-up so the failure is surfaced at start time."""
        _patch_resampler_unloaded(monkeypatch)
        recorder = _build_start_mock_recorder()
        finished = threading.Thread(target=lambda: None, name="dead-preloader", daemon=True)
        finished.start()
        finished.join(timeout=1.0)
        assert not finished.is_alive()
        recorder._scipy_preloader_thread = finished

        start_recording(recorder)

        recorder.warm_up_resampler.assert_called_once()

    def test_sync_warm_up_skipped_while_preloader_in_flight(self, monkeypatch):
        """While ``Recorder.__init__``'s scipy preloader thread is still
        loading scipy, the hotkey path must NOT block on the same import
        — the warm-up is left to the background thread."""
        _patch_resampler_unloaded(monkeypatch)
        recorder = _build_start_mock_recorder()
        preloader = _AliveThreadHandle()
        recorder._scipy_preloader_thread = preloader.thread
        try:
            start_recording(recorder)
            recorder.warm_up_resampler.assert_not_called()
        finally:
            preloader.stop()

    def test_start_completes_atomically_when_warm_up_skipped(self, monkeypatch):
        """Skipping the warm-up must not skip any other start step: the
        stream opens, the recording event is set, and both workers plus
        the device-health checker are started — recording still starts
        correctly and atomically."""
        _patch_resampler_unloaded(monkeypatch)
        recorder = _build_start_mock_recorder()
        preloader = _AliveThreadHandle()
        recorder._scipy_preloader_thread = preloader.thread
        try:
            start_recording(recorder)

            assert recorder._recording_event.is_set()
            recorder._open_stream_for_candidates.assert_called_once()
            recorder._start_audio_worker.assert_called_once()
            recorder._start_event_worker.assert_called_once()
            recorder._start_device_health_checker.assert_called_once()
            recorder.warm_up_resampler.assert_not_called()
        finally:
            preloader.stop()

    def test_warm_up_branch_not_entered_when_poly_loaded(self, monkeypatch):
        """When scipy is already loaded, the warm-up branch is skipped
        entirely regardless of preloader state (existing contract)."""
        from voice_typer.server.recording import resampling as rec_resampling

        monkeypatch.setattr(rec_resampling, "_resample_poly", object(), raising=False)
        monkeypatch.setattr(rec_resampling, "_resample_poly_error", None, raising=False)
        recorder = _build_start_mock_recorder()

        start_recording(recorder)

        recorder.warm_up_resampler.assert_not_called()


class TestScipyPreloaderThreadWiring:
    """A real ``Recorder`` records which preloader thread it spawned so
    the start path can check its liveness."""

    def test_real_recorder_records_preloader_thread_ref(self):
        from tests.fixtures.recorder_test_helpers import make_recorder

        r = make_recorder()
        attr = getattr(r, "_scipy_preloader_thread", "missing")
        assert attr != "missing", "Recorder.__init__ must record the preloader thread ref"
        assert attr is None or isinstance(attr, threading.Thread)
