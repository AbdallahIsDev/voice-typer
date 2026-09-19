"""Tests for ``_recorder_split.stop_recording``."""

from __future__ import annotations

import inspect
import logging
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording._recorder_split import stop_recording

# ``stop_recording`` invokes the free function ``prepare_audio``

_prepare_audio_mock_holder: dict = {}


@pytest.fixture(autouse=True)
def _mock_prepare_audio(monkeypatch):
    """Patch ``_recorder_split.prepare_audio`` with an identity"""
    import voice_typer.server.recording._recorder_split as split_mod

    mock = MagicMock(name="prepare_audio", side_effect=lambda rec, audio, effective_sr_in, **kw: audio)
    monkeypatch.setattr(split_mod, "prepare_audio", mock)
    _prepare_audio_mock_holder["mock"] = mock
    yield mock
    _prepare_audio_mock_holder.pop("mock", None)


def _prep() -> MagicMock:
    return _prepare_audio_mock_holder["mock"]


def _build_mock_recorder(
    *,
    sample_rate: int = 16000,
    buffer_chunks: list[np.ndarray] | None = None,
    buffer_sr: int | None = 16000,
    effective_sr: int = 16000,
    recording: bool = True,
) -> MagicMock:
    """Build a MagicMock recorder with the minimum stubs"""
    recorder = MagicMock(name="recorder")

    recorder._recording_event = threading.Event()
    if recording:
        recorder._recording_event.set()

    recorder._stop_generation = 0
    # `_user_stop_pending` is a bool flag toggled by stop() / discard().
    recorder._user_stop_pending = False
    # `_worker_thread` / `_event_worker_thread` are None on a real
    recorder._worker_thread = None
    recorder._event_worker_thread = None

    # `_lock` must be a real `threading.Lock` so the `with` block
    recorder._audio_pipeline._lock = threading.Lock()

    # `_buffer` is a real `collections.deque` so the `not _buffer`
    import collections

    if buffer_chunks is None:
        buffer_chunks = [np.zeros(100, dtype=np.float32)]
    recorder._audio_pipeline._buffer = collections.deque(buffer_chunks, maxlen=30000)

    # `_chunk_count` is reset to 0 on the empty-buffer path.
    recorder._audio_pipeline._chunk_count = len(buffer_chunks)
    recorder._audio_pipeline._buffer_sr = buffer_sr
    recorder._effective_sr = effective_sr
    recorder._last_rms = 0.0
    recorder._last_audio_stats = (0.0, 0.0, 0.0)

    # ``_teardown_stream``, ``_stop_audio_worker``,

    return recorder


class TestNotRecordingFastPath:
    """
    When ``_recording_event.is_set()`` is False and no worker refs
    MUST NOT mutate any other state, no stop_generation bump, no
    """

    def test_returns_empty_float32_array(self):
        recorder = _build_mock_recorder(recording=False)
        result = stop_recording(recorder)
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert result.size == 0

    def test_does_not_clear_recording_event(self):
        """The early-out fires BEFORE ``_recording_event.clear()``, so"""
        real_event = threading.Event()  # not set
        recorder = _build_mock_recorder(recording=False)
        # Replace the factory-built mock event with a wraps=Event mock
        recorder._recording_event = MagicMock(wraps=real_event)
        stop_recording(recorder)
        recorder._recording_event.clear.assert_not_called()
        recorder._recording_event.is_set.assert_called_once()

    def test_does_not_bump_stop_generation(self):
        recorder = _build_mock_recorder(recording=False)
        original_gen = recorder._stop_generation
        stop_recording(recorder)
        assert recorder._stop_generation == original_gen

    def test_does_not_call_teardown_or_worker_stops(self):
        recorder = _build_mock_recorder(recording=False)
        stop_recording(recorder)
        recorder._teardown_stream.assert_not_called()
        recorder._stop_audio_worker.assert_not_called()
        recorder._capture.stop_event_worker_body.assert_not_called()
        recorder._stop_device_health_checker.assert_not_called()
        recorder._session_state.secure_clear_caches.assert_not_called()
        _prep().assert_not_called()

    def test_cleared_event_with_live_worker_still_stops_workers(self):
        """GT-23R: when ``_recording_event`` is cleared but a worker ref"""
        recorder = _build_mock_recorder(recording=False)
        recorder._worker_thread = threading.Thread(target=lambda: None)
        recorder._event_worker_thread = threading.Thread(target=lambda: None)

        stop_recording(recorder)

        recorder._teardown_stream.assert_called_once()
        recorder._stop_audio_worker.assert_called_once()
        recorder._capture.stop_event_worker_body.assert_called_once()


class TestStopRecordingOrdering:
    """Verify the body of ``Recorder.stop`` runs the steps in the"""

    def test_runs_all_steps_in_order(self):
        """When the buffer has chunks, ``stop_recording`` must invoke"""
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        stop_recording(recorder)

        # Every documented step was called exactly once.
        recorder._teardown_stream.assert_called_once()
        recorder._stop_audio_worker.assert_called_once()
        recorder._capture.stop_event_worker_body.assert_called_once()
        recorder._stop_device_health_checker.assert_called_once_with(timeout=0.0)
        recorder._session_state.secure_clear_caches.assert_called_once()
        _prep().assert_called_once()

    def test_stop_audio_worker_uses_join_timeout_and_drain_true(self):
        """RT-SAFE-001: drain=True so the last few hundred ms of audio"""
        from voice_typer.server.recording.recorder import (
            _AUDIO_WORKER_JOIN_TIMEOUT_S,
        )

        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        stop_recording(recorder)
        recorder._stop_audio_worker.assert_called_once_with(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S, drain=True)

    def test_stop_event_worker_uses_join_timeout_and_drain_true(self):
        """The event worker drains its tiny queue in <10ms, but"""
        from voice_typer.server.recording.recorder import (
            _EVENT_WORKER_JOIN_TIMEOUT_S,
        )

        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        stop_recording(recorder)
        recorder._capture.stop_event_worker_body.assert_called_once_with(
            recorder, timeout=_EVENT_WORKER_JOIN_TIMEOUT_S, drain=True
        )

    def test_step_order_matches_contract(self):
        """Pin the source-order contract: clear event → bump"""
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        call_log: list[str] = []

        def log_call(name, ret=None):
            def _hook(*a, **k):
                call_log.append(name)
                return ret

            return _hook

        recorder._teardown_stream.side_effect = log_call("teardown_stream")
        recorder._stop_audio_worker.side_effect = log_call("stop_audio_worker")
        recorder._capture.stop_event_worker_body.side_effect = log_call("stop_event_worker")
        recorder._stop_device_health_checker.side_effect = log_call("stop_device_health_checker")
        recorder._session_state.secure_clear_caches.side_effect = log_call("secure_clear_caches")

        # `_prepare_audio(audio, effective_sr)` must return the input
        def _prepare_audio_hook(rec, audio, effective_sr_in, *a, **k):
            call_log.append("prepare_audio")
            return audio

        _prep().side_effect = _prepare_audio_hook

        # We need to track the user_stop_pending flag toggles too —
        stop_recording(recorder)

        # The expected source-order of the *method* calls.
        expected_order = [
            "teardown_stream",
            "stop_audio_worker",
            "stop_event_worker",
            "stop_device_health_checker",
            "secure_clear_caches",
            "prepare_audio",
        ]
        observed = [c for c in call_log if c in expected_order]
        assert observed == expected_order, (
            f"stop_recording step order mismatch: expected {expected_order}, "
            f"got {observed}. The source-order contract pins the 17-H-FIX-2 "
            f"stream-teardown / worker-stop sequencing."
        )

    def test_user_stop_pending_flag_toggled_around_teardown(self):
        """STREAM-FIX: ``_user_stop_pending`` is set to True BEFORE"""
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])

        flag_history: list[bool] = []

        def _teardown_hook(*a, **k):
            # Inside the call, the flag should have just been set True.
            flag_history.append(("during_teardown", recorder._user_stop_pending))

        recorder._teardown_stream.side_effect = _teardown_hook
        stop_recording(recorder)

        # Before teardown: True
        assert recorder._user_stop_pending is False, (
            "STREAM-FIX regression: _user_stop_pending must be cleared to "
            "False after _teardown_stream() returns (so a later genuine "
            "disconnect fires the warning)."
        )
        # During teardown: True
        assert flag_history == [("during_teardown", True)], (
            "STREAM-FIX regression: _user_stop_pending must be True during "
            "the _teardown_stream() call (so _stream_finished_callback "
            "suppresses the false 'Stream finished unexpectedly' warning)."
        )

    def test_stop_generation_incremented(self):
        """HOTKEY-CRASH: increment stop_generation so any stale"""
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        original_gen = recorder._stop_generation
        stop_recording(recorder)
        assert recorder._stop_generation == original_gen + 1

    def test_recording_event_cleared(self):
        """``_recording_event.clear()`` is the gate the audio callback"""
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        assert recorder._recording_event.is_set()
        stop_recording(recorder)
        assert not recorder._recording_event.is_set()


class TestEmptyBufferPath:
    """
    function must call ``_secure_clear_caches()``, reset
    WITHOUT calling ``_prepare_audio`` (G4-H-06 secure-clear contract).
    """

    def test_returns_empty_float32_array(self):
        recorder = _build_mock_recorder(buffer_chunks=[])
        result = stop_recording(recorder)
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert result.size == 0

    def test_calls_secure_clear_caches(self):
        recorder = _build_mock_recorder(buffer_chunks=[])
        stop_recording(recorder)
        recorder._session_state.secure_clear_caches.assert_called_once()

    def test_resets_chunk_count_to_zero(self):
        recorder = _build_mock_recorder(buffer_chunks=[])
        recorder._audio_pipeline._chunk_count = 5  # pretend we had 5 chunks before
        stop_recording(recorder)
        assert recorder._audio_pipeline._chunk_count == 0

    def test_does_not_call_prepare_audio(self):
        """The empty-buffer early-return fires BEFORE _prepare_audio,"""
        recorder = _build_mock_recorder(buffer_chunks=[])
        stop_recording(recorder)
        _prep().assert_not_called()

    def test_does_not_swap_buffer_or_call_secure_clear_array_background(self, monkeypatch):
        """``_secure_clear_array_background`` call (those are only needed"""
        import voice_typer.server.recording as rec_pkg

        bg_clear = MagicMock()
        monkeypatch.setattr(rec_pkg, "_secure_clear_array_background", bg_clear)

        recorder = _build_mock_recorder(buffer_chunks=[])
        original_buffer = recorder._audio_pipeline._buffer
        stop_recording(recorder)
        # Buffer was not swapped.
        assert recorder._audio_pipeline._buffer is original_buffer
        # Background clear was not called.
        bg_clear.assert_not_called()

    def test_teardown_and_worker_stops_still_called_before_empty_return(self):
        """The empty-buffer early-return fires AFTER teardown + worker"""
        recorder = _build_mock_recorder(buffer_chunks=[])
        stop_recording(recorder)
        recorder._teardown_stream.assert_called_once()
        recorder._stop_audio_worker.assert_called_once()
        recorder._capture.stop_event_worker_body.assert_called_once()
        recorder._stop_device_health_checker.assert_called_once_with(timeout=0.0)


class TestBufferSnapshotUnderLock:
    """The function snapshots ``recorder._audio_pipeline._buffer`` under"""

    def test_buffer_swapped_for_fresh_empty_container(self, monkeypatch):
        import voice_typer.server.recording as rec_pkg
        from voice_typer.server.recording._recorder_split import GrowableRecordingBuffer

        monkeypatch.setattr(rec_pkg, "_secure_clear_array_background", lambda _old: None)

        original_chunks = [np.ones(50, dtype=np.float32), np.zeros(30, dtype=np.float32)]
        recorder = _build_mock_recorder(buffer_chunks=original_chunks)
        original_buffer = recorder._audio_pipeline._buffer
        assert len(original_buffer) == 2

        stop_recording(recorder)

        assert recorder._audio_pipeline._buffer is not original_buffer
        assert isinstance(recorder._audio_pipeline._buffer, GrowableRecordingBuffer)
        assert len(recorder._audio_pipeline._buffer) == 0
        assert recorder._audio_pipeline._buffer.total_samples == 0
        # Maxlen is preserved (or defaulted to DEFAULT_MAX_BUFFER_CHUNKS).
        from voice_typer.server.recording.recorder import DEFAULT_MAX_BUFFER_CHUNKS

        assert recorder._audio_pipeline._buffer.maxlen == DEFAULT_MAX_BUFFER_CHUNKS

    def test_buffer_maxlen_preserved_from_old_buffer(self, monkeypatch):
        import collections

        # Patch the OWNING module: stop_recording calls
        monkeypatch.setattr("voice_typer.server.recording.buffer._secure_clear_array_background", lambda _old: None)

        # Build a recorder with a custom maxlen.
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        custom_maxlen = 42
        recorder._audio_pipeline._buffer = collections.deque([np.ones(50, dtype=np.float32)], maxlen=custom_maxlen)

        stop_recording(recorder)

        assert recorder._audio_pipeline._buffer.maxlen == custom_maxlen

    def test_secure_clear_array_background_called_with_old_buffer(self, monkeypatch):
        """daemon thread (so stop() returns immediately and the secure"""
        from voice_typer.server.recording._recorder_split import GrowableRecordingBuffer

        bg_clear = MagicMock()
        # Patch the OWNING module: stop_recording calls
        monkeypatch.setattr("voice_typer.server.recording.buffer._secure_clear_array_background", bg_clear)

        recorder = _build_mock_recorder(buffer_chunks=[])
        buf = GrowableRecordingBuffer(maxlen=30000, nominal_sample_rate=16000)
        buf.append(np.ones(50, dtype=np.float32))
        recorder._audio_pipeline._buffer = buf

        stop_recording(recorder)

        bg_clear.assert_called_once()
        assert bg_clear.call_args.args[0] is buf

    def test_concatenate_called_with_captured_chunks(self):
        """The captured chunks are concatenated OUTSIDE the lock into"""
        chunk1 = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        chunk2 = np.array([0.4, 0.5], dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk1, chunk2])

        result = stop_recording(recorder)

        assert result.size == 5
        np.testing.assert_allclose(result, np.array([0.1, 0.2, 0.3, 0.4, 0.5]))


class TestStatsAndBufferSrCapture:
    """RMS / peak / silence_pct are computed from the concatenated"""

    def test_last_audio_stats_stored_on_non_empty_audio(self):
        """transcription engine can reuse them instead of recomputing"""
        # Use a known signal: constant 0.5 amplitude → RMS = 0.5,
        chunk = np.full(100, 0.5, dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        stop_recording(recorder)

        stats = recorder._last_audio_stats
        assert isinstance(stats, tuple)
        assert len(stats) == 3
        rms, peak, silence_pct = stats
        assert rms == pytest.approx(0.5, abs=1e-6)
        assert peak == pytest.approx(0.5, abs=1e-6)
        assert silence_pct == pytest.approx(0.0, abs=1e-6)
        # _last_rms is also updated.
        assert recorder._last_rms == pytest.approx(0.5, abs=1e-6)

    def test_last_audio_stats_zero_on_empty_audio(self):
        """When the buffer is non-empty but the concatenated array"""
        # An empty chunk in the buffer, len > 0 passes, but size == 0
        empty_chunk = np.array([], dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[empty_chunk])

        stop_recording(recorder)

        assert recorder._last_audio_stats == (0.0, 0.0, 0.0)
        assert recorder._last_rms == 0.0

    def test_silence_percentage_computed(self):
        """silence_pct = (samples with abs < 0.001) / total * 100."""
        # 4 silent + 6 loud samples → silence_pct = 40.0
        chunk = np.array(
            [0.0, 0.0, 0.0005, 0.0009, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
            dtype=np.float32,
        )
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        stop_recording(recorder)

        _, _, silence_pct = recorder._last_audio_stats
        assert silence_pct == pytest.approx(40.0, abs=1e-3)

    def test_prepare_audio_called_with_captured_buffer_sr(self):
        """XV-31 / chipmunk regression guard: ``_prepare_audio`` is"""
        chunk = np.ones(100, dtype=np.float32)
        recorder = _build_mock_recorder(
            buffer_chunks=[chunk],
            buffer_sr=16000,  # captured local, authoritative
            effective_sr=48000,  # device native rate, must NOT be used
        )

        stop_recording(recorder)

        # The captured local (16000) is the first arg to _prepare_audio.
        _prep().assert_called_once()
        call_args = _prep().call_args
        effective_sr_passed = call_args.args[2]
        assert effective_sr_passed == 16000, (
            f"XV-31 regression: _prepare_audio was called with effective_sr="
            f"{effective_sr_passed}, expected 16000 (the captured "
            f"_buffer_sr). Using _effective_sr (48000) would decimate the "
            f"already-16 kHz audio 3:1 → chipmunk voice."
        )

    def test_prepare_audio_falls_back_to_effective_sr_when_buffer_sr_none(self):
        """``_effective_sr``, the ``or recorder._effective_sr`` idiom."""
        chunk = np.ones(100, dtype=np.float32)
        recorder = _build_mock_recorder(
            buffer_chunks=[chunk],
            buffer_sr=None,
            effective_sr=48000,
        )

        stop_recording(recorder)

        _prep().assert_called_once()
        effective_sr_passed = _prep().call_args.args[2]
        assert effective_sr_passed == 48000

    def test_prepare_audio_identity_returned_to_caller(self):
        """The return value of ``_prepare_audio`` is the function's"""
        chunk = np.ones(50, dtype=np.float32) * 0.5
        # Build a recorder where _prepare_audio returns a different
        recorder = _build_mock_recorder(buffer_chunks=[chunk])
        resampled = np.full(200, 0.25, dtype=np.float32)
        _prep().side_effect = None
        _prep().return_value = resampled

        result = stop_recording(recorder)

        # The returned array IS the _prepare_audio return value.
        assert result is resampled

    def test_near_silence_warning_emitted_when_rms_below_threshold(self, caplog):
        """When ``rms < 0.001``, the function logs a near-silence"""
        # A near-zero chunk → rms ≈ 0.0005 (below the 0.001 threshold).
        chunk = np.full(100, 0.0005, dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            stop_recording(recorder)

        assert any("Near-silence detected" in r.message and r.levelno == logging.WARNING for r in caplog.records), (
            "Expected a 'Near-silence detected' WARNING when rms < 0.001"
        )

    def test_no_audio_warning_emitted_when_concat_is_empty(self, caplog):
        """When the buffer is non-empty but the concatenated array is"""
        # A single empty chunk → ``np.concatenate([empty]).reshape(-1)``
        empty_chunk = np.array([], dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[empty_chunk])

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            stop_recording(recorder)

        assert any("No audio data captured" in r.message and r.levelno == logging.WARNING for r in caplog.records), (
            "Expected a 'No audio data captured!' WARNING when the buffer is "
            "non-empty but the concatenated array is empty (post-concat else "
            "branch). The empty-buffer fast path inside the lock short-"
            "circuits with an early-return and does NOT log this warning."
        )

    def test_no_audio_warning_not_emitted_on_empty_buffer_fast_path(self, caplog):
        """The empty-buffer early-return (``if not self._buffer:``)"""
        recorder = _build_mock_recorder(buffer_chunks=[])

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            stop_recording(recorder)

        assert not any(
            "No audio data captured" in r.message and r.levelno == logging.WARNING for r in caplog.records
        ), (
            "The empty-buffer fast path (inside the lock) short-circuits "
            "with an early-return; the 'No audio data captured!' warning is "
            "only emitted on the post-concat else branch (when the buffer "
            "was non-empty but the concatenated array happened to be empty)."
        )

    def test_info_summary_emitted_on_non_empty_audio(self, caplog):
        """The ``log.info`` summary (duration, sr, samples, RMS, peak,"""
        chunk = np.full(100, 0.5, dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        with caplog.at_level(logging.INFO, logger="voice_typer.server.recording"):
            stop_recording(recorder)

        assert any("Audio stopped" in r.message and r.levelno == logging.INFO for r in caplog.records), (
            "Expected an 'Audio stopped' INFO summary on the non-empty-audio path"
        )


class TestSourceStringContracts:
    """Source-string contracts: the function body must not reference"""

    @staticmethod
    def _body_after_docstring(src: str) -> str:
        """Return the function source with the docstring stripped."""
        first = src.find('"""')
        assert first >= 0, "no opening triple-quote in function source"
        second = src.find('"""', first + 3)
        assert second >= 0, "no closing triple-quote in function source"
        return src[second + 3 :]

    def test_no_self_references_in_body(self):
        """The function body must NOT reference ``self.X``, the body"""
        import re

        src = inspect.getsource(stop_recording)
        body = self._body_after_docstring(src)
        self_refs = re.findall(r"\bself\.", body)
        assert not self_refs, (
            f"stop_recording must NOT reference `self.X` in its body, "
            f"the body was rewritten to use `recorder.X`. Found "
            f"{len(self_refs)} `self.` references: {self_refs[:5]}"
        )

    def test_lazy_import_in_function_body(self):
        """The function body contains the lazy ``from … recorder import``"""
        src = inspect.getsource(stop_recording)
        body = self._body_after_docstring(src)
        assert "from voice_typer.server.recording.recorder import (" in body, (
            "stop_recording must do the lazy constants import inside its "
            "body, moving it to module top would re-introduce the "
            "circular import that recorder.py's top-level import of this "
            "module creates."
        )

    def test_constants_import_in_function_body(self):
        """The function body lazily imports the join-timeout"""
        src = inspect.getsource(stop_recording)
        body = self._body_after_docstring(src)
        assert "_AUDIO_WORKER_JOIN_TIMEOUT_S" in body
        assert "_EVENT_WORKER_JOIN_TIMEOUT_S" in body

    def test_signature_is_recorder_to_ndarray(self):
        """
        Pin the function signature so a future refactor can't
        ``self`` → ``recorder`` rewrite contract).
        """
        sig = inspect.signature(stop_recording)
        params = list(sig.parameters)
        assert params == ["recorder"], f"stop_recording signature must be (recorder), got {params}."
        assert sig.return_annotation is not None


class TestWorkerStopContracts:
    """Pin the per-worker timeout and drain semantics:"""

    def test_stop_device_health_checker_timeout_zero(self):
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        stop_recording(recorder)
        recorder._stop_device_health_checker.assert_called_once_with(timeout=0.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
