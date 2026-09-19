"""Focused tests for the ``start()`` hotkey critical-path warm-up policy."""

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
    """Minimal ``MagicMock`` recorder that lets ``start_recording`` run"""
    recorder = MagicMock(name="recorder")
    recorder.config = MagicMock(name="config")
    recorder.config.sample_rate = sample_rate
    recorder.config.microphone = None
    recorder.config.save.return_value = True
    recorder._session_state.cache_session_config.return_value = 30
    recorder._devices._resolve_device.return_value = 5
    recorder._devices._same_physical_microphone_candidates.return_value = [5]
    recorder._stream_lifecycle.build_audio_callback.return_value = object()
    recorder._stream_lifecycle.open_stream_for_candidates.return_value = (5, effective_sr, None)
    recorder._stream_lifecycle._stream = MagicMock(name="opened-stream")
    recorder._recording_event = threading.Event()
    recorder._audio_processor = None
    recorder._preroll_active = False
    recorder._preroll_seconds = 0.0
    recorder._preroll_buffer = deque(maxlen=0)
    recorder._devices._mic_watcher = None
    # Real scalar sample rates: ``refresh_vad_caches`` (invoked directly
    recorder._audio_pipeline._buffer_sr = None
    recorder._effective_sr = sample_rate
    recorder.warm_up_resampler = MagicMock(name="warm_up_resampler")
    return recorder


def _patch_resampler_unloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the warm-up branch to fire: scipy not loaded, no cached error."""
    from voice_typer.server.recording import resampling as rec_resampling

    monkeypatch.setattr(rec_resampling, "_resample_poly", None, raising=False)
    monkeypatch.setattr(rec_resampling, "_resample_poly_error", None, raising=False)


class _AliveThreadHandle:
    """A real daemon thread held alive until released, to stand in for"""

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
    """The synchronous resampler warm-up runs only when no background"""

    def test_sync_warm_up_called_without_in_flight_preloader(self, monkeypatch):
        """preloader existed and for test doubles."""
        _patch_resampler_unloaded(monkeypatch)
        recorder = _build_start_mock_recorder()
        recorder._scipy_preloader_thread = object()  # not a Thread

        start_recording(recorder)

        recorder.warm_up_resampler.assert_called_once()

    def test_sync_warm_up_called_when_preloader_thread_dead(self, monkeypatch):
        """A preloader thread that already exited without loading scipy"""
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
        """While ``Recorder.__init__``'s scipy preloader thread is still"""
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
        """stream opens, the recording event is set, and both workers plus"""
        _patch_resampler_unloaded(monkeypatch)
        recorder = _build_start_mock_recorder()
        preloader = _AliveThreadHandle()
        recorder._scipy_preloader_thread = preloader.thread
        try:
            start_recording(recorder)

            assert recorder._recording_event.is_set()
            recorder._stream_lifecycle.open_stream_for_candidates.assert_called_once()
            recorder._start_audio_worker.assert_called_once()
            recorder._capture.start_event_worker_body.assert_called_once()
            recorder._devices._start_device_health_checker.assert_called_once()
            recorder.warm_up_resampler.assert_not_called()
        finally:
            preloader.stop()

    def test_warm_up_branch_not_entered_when_poly_loaded(self, monkeypatch):
        """When scipy is already loaded, the warm-up branch is skipped"""
        from voice_typer.server.recording import resampling as rec_resampling

        monkeypatch.setattr(rec_resampling, "_resample_poly", object(), raising=False)
        monkeypatch.setattr(rec_resampling, "_resample_poly_error", None, raising=False)
        recorder = _build_start_mock_recorder()

        start_recording(recorder)

        recorder.warm_up_resampler.assert_not_called()


class TestScipyPreloaderThreadWiring:
    """A real ``Recorder`` records which preloader thread it spawned so"""

    def test_real_recorder_records_preloader_thread_ref(self):
        from tests.fixtures.recorder_test_helpers import make_recorder

        r = make_recorder()
        attr = getattr(r, "_scipy_preloader_thread", "missing")
        assert attr != "missing", "Recorder.__init__ must record the preloader thread ref"
        assert attr is None or isinstance(attr, threading.Thread)
