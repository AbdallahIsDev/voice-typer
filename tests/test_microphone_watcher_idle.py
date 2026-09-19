"""Pin the contract that ``Recorder.start`` / ``Recorder.stop``"""

from __future__ import annotations

import collections
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording._recorder_split import (
    start_recording,
    stop_recording,
)

# ``start_recording`` / ``stop_recording`` invoke the free functions

_mock_bindings_holder: dict = {}


@pytest.fixture(autouse=True)
def _mock_split_bindings(monkeypatch):
    import voice_typer.server.recording._recorder_split as split_mod

    refresh_mock = MagicMock(name="refresh_vad_caches")
    prepare_mock = MagicMock(name="prepare_audio", side_effect=lambda rec, audio, effective_sr_in, **kw: audio)
    monkeypatch.setattr(split_mod, "refresh_vad_caches", refresh_mock)
    monkeypatch.setattr(split_mod, "prepare_audio", prepare_mock)
    _mock_bindings_holder["refresh"] = refresh_mock
    _mock_bindings_holder["prepare"] = prepare_mock
    yield
    _mock_bindings_holder.pop("refresh", None)
    _mock_bindings_holder.pop("prepare", None)


def _refresh() -> MagicMock:
    return _mock_bindings_holder["refresh"]


def _prep() -> MagicMock:
    return _mock_bindings_holder["prepare"]


def _build_start_recorder(*, open_success: bool = True) -> MagicMock:
    """Build a MagicMock recorder sufficient for ``start_recording``."""
    recorder = MagicMock(name="recorder")
    recorder.config = MagicMock(name="config")
    recorder.config.sample_rate = 16000
    recorder.config.microphone = None
    recorder.config.save.return_value = True

    recorder._session_state.cache_session_config.return_value = 30
    recorder._devices._resolve_device.return_value = 5
    recorder._devices._same_physical_microphone_candidates.return_value = [5]
    recorder._stream_lifecycle.build_audio_callback.return_value = object()

    if open_success:
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (5, 16000, None)
        recorder._stream_lifecycle._stream = MagicMock(name="opened-stream")
    else:
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (
            None,
            16000,
            RuntimeError("no input device could be opened"),
        )
        recorder._stream_lifecycle._stream = None

    recorder._recording_event = threading.Event()
    recorder._audio_processor = None
    recorder._preroll_active = False
    recorder._preroll_seconds = 0.0
    recorder._preroll_buffer = collections.deque(maxlen=0)

    # Explicit spied watcher (no auto-child) so call counts are
    recorder._devices._mic_watcher = MagicMock(name="mic_watcher")
    return recorder


def _build_stop_recorder(
    *,
    recording: bool = True,
    buffer_chunks: list[np.ndarray] | None = None,
) -> MagicMock:
    """Build a MagicMock recorder sufficient for ``stop_recording``."""
    recorder = MagicMock(name="recorder")

    recorder._recording_event = threading.Event()
    if recording:
        recorder._recording_event.set()

    recorder._stop_generation = 0
    recorder._user_stop_pending = False
    recorder._worker_thread = None
    recorder._event_worker_thread = None
    recorder._audio_pipeline._lock = threading.Lock()

    if buffer_chunks is None:
        buffer_chunks = [np.zeros(100, dtype=np.float32)]
    recorder._audio_pipeline._buffer = collections.deque(buffer_chunks, maxlen=30000)

    recorder._audio_pipeline._chunk_count = len(buffer_chunks)
    recorder._audio_pipeline._buffer_sr = 16000
    recorder._effective_sr = 16000
    recorder._last_rms = 0.0
    recorder._last_audio_stats = (0.0, 0.0, 0.0)

    _prep().side_effect = lambda rec, audio, effective_sr_in, **kw: audio

    # Explicit spied watcher.
    recorder._devices._mic_watcher = MagicMock(name="mic_watcher")
    return recorder


class TestStartRecordingWiresSetIdleFalse:
    """``start_recording`` must call ``set_idle(False)`` on success."""

    def test_start_calls_set_idle_false_once(self):
        """A successful ``start_recording`` must invoke"""
        recorder = _build_start_recorder()
        start_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_called_once_with(False)

    def test_start_does_not_call_set_idle_true(self):
        """``start_recording`` must NOT call ``set_idle(True)`` —"""
        recorder = _build_start_recorder()
        start_recording(recorder)

        # ``set_idle`` was called exactly once with ``False`` —
        assert recorder._devices._mic_watcher.set_idle.call_count == 1
        assert recorder._devices._mic_watcher.set_idle.call_args == ((False,), {})

    def test_set_idle_false_runs_after_start_device_health_checker(self):
        """``start_recording`` so any earlier failure prevents the toggle"""
        recorder = _build_start_recorder()
        call_log: list[str] = []

        def log_call(name, ret=None):
            def _hook(*a, **k):
                call_log.append(name)
                return ret

            return _hook

        recorder._secure_clear_session_caches.side_effect = log_call("secure_clear")
        recorder._session_state.reset_session_state.side_effect = log_call("reset_session")
        recorder._session_state.cache_session_config.side_effect = log_call("cache_config", ret=30)
        recorder._devices._resolve_device.side_effect = log_call("resolve_device", ret=5)
        recorder._devices._same_physical_microphone_candidates.side_effect = log_call("candidates", ret=[5])
        recorder._stream_lifecycle.build_audio_callback.side_effect = log_call("build_callback", ret=object())
        recorder._stream_lifecycle.open_stream_for_candidates.side_effect = log_call(
            "open_stream", ret=(5, 16000, None)
        )
        recorder._session_state.resize_buffers_for_sample_rate.side_effect = log_call("resize_buffers")
        recorder._recording_event = MagicMock(wraps=threading.Event())
        recorder._recording_event.set.side_effect = log_call("event.set")
        _refresh().side_effect = log_call("refresh_vad")
        recorder._start_audio_worker.side_effect = log_call("start_audio_worker")
        recorder._capture.start_event_worker_body.side_effect = log_call("start_event_worker")
        recorder._devices._start_device_health_checker.side_effect = log_call("start_device_health_checker")

        def _set_idle_hook(is_idle):
            call_log.append(f"set_idle({is_idle})")

        recorder._devices._mic_watcher.set_idle.side_effect = _set_idle_hook

        start_recording(recorder)

        # ``set_idle(False)`` must be the FINAL entry, after
        assert call_log[-1] == "set_idle(False)", (
            f"set_idle(False) must be the last call in start_recording; got call_log={call_log}"
        )
        assert call_log.index("start_device_health_checker") < call_log.index("set_idle(False)"), (
            f"set_idle(False) must come AFTER start_device_health_checker; got call_log={call_log}"
        )


class TestStartRecordingFailurePath:
    """If ``start_recording`` raises, ``set_idle(False)`` must NOT be"""

    def test_set_idle_false_not_called_when_event_worker_raises(self):
        """
        When ``_start_event_worker`` raises (the second-to-last
        ``set_idle(False)`` must NOT be called.
        """
        recorder = _build_start_recorder()
        recorder._capture.start_event_worker_body.side_effect = RuntimeError("event worker spawn failed")

        with pytest.raises(RuntimeError, match="event worker spawn failed"):
            start_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_not_called()

    def test_set_idle_false_not_called_when_audio_worker_raises(self):
        """When ``_start_audio_worker`` raises, the rollback path"""
        recorder = _build_start_recorder()
        recorder._start_audio_worker.side_effect = RuntimeError("audio worker spawn failed")

        with pytest.raises(RuntimeError, match="audio worker spawn failed"):
            start_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_not_called()

    def test_set_idle_false_not_called_when_stream_open_fails(self):
        """When the stream-open path fails (no input device could be"""
        recorder = _build_start_recorder(open_success=False)
        recorder._stream_lifecycle.open_stream_fallback.return_value = (
            None,
            16000,
            False,
            RuntimeError("fallback failed"),
        )

        with pytest.raises(RuntimeError, match="fallback failed"):
            start_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_not_called()


class TestStartRecordingNoneWatcherGuard:
    """
    When ``recorder._devices._mic_watcher is None`` (macOS-without-pyobjc
    ``DeviceManager.__init__``), ``start_recording`` must NOT touch
    """

    def test_start_does_not_raise_when_mic_watcher_is_none(self):
        """A ``None`` ``_mic_watcher`` must be tolerated, the watcher"""
        recorder = _build_start_recorder()
        recorder._devices._mic_watcher = None

        # Must not raise AttributeError.
        start_recording(recorder)


class TestStopRecordingWiresSetIdleTrue:
    """``stop_recording`` must call ``set_idle(True)`` on each"""

    def test_stop_calls_set_idle_true_once_non_empty_buffer(self):
        """A successful ``stop_recording`` with a non-empty buffer"""
        recorder = _build_stop_recorder(recording=True)
        stop_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_called_once_with(True)

    def test_stop_calls_set_idle_true_on_empty_buffer_path(self):
        """``if not recorder._audio_pipeline._buffer:`` branch inside the lock) must"""
        recorder = _build_stop_recorder(recording=True, buffer_chunks=[])
        stop_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_called_once_with(True)

    def test_stop_does_not_call_set_idle_false(self):
        """``stop_recording`` must NOT call ``set_idle(False)`` —"""
        recorder = _build_stop_recorder(recording=True)
        stop_recording(recorder)

        assert recorder._devices._mic_watcher.set_idle.call_count == 1
        assert recorder._devices._mic_watcher.set_idle.call_args == ((True,), {})


class TestStopRecordingNoneWatcherGuard:
    """When ``recorder._devices._mic_watcher is None``, ``stop_recording``"""

    def test_stop_does_not_raise_when_mic_watcher_is_none(self):
        recorder = _build_stop_recorder(recording=True)
        recorder._devices._mic_watcher = None

        # Must not raise AttributeError.
        result = stop_recording(recorder)
        # The non-empty-buffer happy path still returns the audio array.
        assert isinstance(result, np.ndarray)


class TestStartStopRoundTrip:
    """Verify a start() → stop() round-trip toggles the watcher"""

    def test_round_trip_calls_set_idle_false_then_true(self):
        recorder = _build_start_recorder()
        # Reuse the same watcher mock for stop, start populated
        start_recording(recorder)
        recorder._audio_pipeline._buffer = collections.deque([np.zeros(100, dtype=np.float32)], maxlen=30000)
        recorder._audio_pipeline._buffer_sr = 16000
        recorder._effective_sr = 16000
        recorder._audio_pipeline._lock = threading.Lock()
        _prep().side_effect = lambda rec, audio, effective_sr_in, **kw: audio

        stop_recording(recorder)

        # Two calls: first ``False`` (start), then ``True`` (stop).
        assert recorder._devices._mic_watcher.set_idle.call_count == 2
        assert recorder._devices._mic_watcher.set_idle.call_args_list[0] == ((False,), {})
        assert recorder._devices._mic_watcher.set_idle.call_args_list[1] == ((True,), {})
