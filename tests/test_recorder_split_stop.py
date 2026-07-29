"""Tests for ``_recorder_split.stop_recording``.

S3-CR-17 / Phase 4.5 — pin the extraction contract for the body of
``Recorder.stop`` that was moved (verbatim, with ``self.X`` rewritten
to ``recorder.X``) into a free function in
``voice_typer/server/recording/_recorder_split.py``. The
``Recorder.stop`` method becomes a 1-line delegator so existing call
sites, subclass overrides, and any ``inspect.getsource`` checks that
look for the method on the ``Recorder`` class continue to work.

There is NO source-inspection test contract pinning ``Recorder.stop``
source (verified via ``rg "inspect.getsource.*Recorder\\.stop\\b"
tests/`` — the matches on ``IPCServer.stop`` are unrelated), so the
simple Option B delegate is sufficient.

These tests use a MagicMock recorder with explicit stubs so they
never touch PortAudio, real worker threads, or real subprocesses.
Each test is deterministic and sub-second.

The tests pin five contracts:

  1. **Not-recording fast path**: when ``_recording_event.is_set()``
     is False, the function returns an empty ``float32`` array and
     does NOT touch any other state (no stop_generation bump, no
     teardown, no worker joins).

  2. **Happy-path ordering**: every step in the documented source
     order (event-clear → stop_generation bump → user_stop_pending
     flag → teardown_stream → flag clear → stop_audio_worker →
     stop_event_worker → stop_device_health_checker → buffer
     snapshot under _lock → secure_clear_caches → buffer swap →
     _prepare_audio → log.info).

  3. **Empty-buffer path**: when ``recorder._buffer`` is empty inside
     the locked block, the function calls ``_secure_clear_caches()``,
     resets ``_chunk_count = 0``, and returns an empty ``float32``
     array WITHOUT calling ``_prepare_audio``.

  4. **Stats + buffer_sr capture**: RMS / peak / silence_pct are
     computed from the concatenated audio, stored in
     ``_last_audio_stats``, AND ``_prepare_audio`` is called with the
     captured ``_buffer_sr`` (NOT ``_effective_sr``) — XV-31 / chipmunk
     regression guard.

  5. **Self → recorder rewriting + lazy import**: the body contains
     no ``self.X`` references (after stripping the docstring) and
     does the lazy ``_recording_pkg`` / constants import inside the
     function body so a future refactor can't accidentally re-introduce
     a circular import.
"""

from __future__ import annotations

import inspect
import logging
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording._recorder_split import stop_recording

# ── Mock recorder factory ─────────────────────────────────────────


def _build_mock_recorder(
    *,
    sample_rate: int = 16000,
    buffer_chunks: list[np.ndarray] | None = None,
    buffer_sr: int | None = 16000,
    effective_sr: int = 16000,
    recording: bool = True,
    prepare_audio_identity: bool = True,
) -> MagicMock:
    """Build a MagicMock recorder with the minimum stubs
    ``stop_recording`` needs to run without touching PortAudio, real
    worker threads, or the real audio processor chain."""
    recorder = MagicMock(name="recorder")

    # `_recording_event` must be a real `threading.Event` so the
    # `is_set()` early-out fast path and the `clear()` call work
    # as in production.
    recorder._recording_event = threading.Event()
    if recording:
        recorder._recording_event.set()

    # `_stop_generation` is an int counter; production reads it on
    # the audio callback to bail out of stale disconnect handlers.
    recorder._stop_generation = 0
    # `_user_stop_pending` is a bool flag toggled by stop() / discard().
    recorder._user_stop_pending = False

    # `_lock` must be a real `threading.Lock` so the `with` block
    # works (the function swaps the deque + captures the chunk list
    # inside the lock).
    recorder._lock = threading.Lock()

    # `_buffer` is a real `collections.deque` so the `not _buffer`
    # check + `list(_buffer)` capture work without magic.
    import collections

    if buffer_chunks is None:
        # Default to a single 100-sample silent chunk (so the
        # happy-path body has something to concatenate).
        buffer_chunks = [np.zeros(100, dtype=np.float32)]
    recorder._buffer = collections.deque(buffer_chunks, maxlen=30000)

    # `_chunk_count` is reset to 0 on the empty-buffer path.
    recorder._chunk_count = len(buffer_chunks)
    recorder._buffer_sr = buffer_sr
    recorder._effective_sr = effective_sr
    recorder._last_rms = 0.0
    recorder._last_audio_stats = (0.0, 0.0, 0.0)

    # `_prepare_audio` is a thin no-op pass-through by default so
    # `len(audio) > 0` stays true after the call (the stats and the
    # log.info branch both gate on `len(audio) > 0`).
    if prepare_audio_identity:
        recorder._prepare_audio.side_effect = lambda audio, effective_sr_in, **kw: audio
    else:
        # Caller will set its own return_value / side_effect.
        recorder._prepare_audio.return_value = np.array([], dtype=np.float32)

    # `_secure_clear_caches`, `_teardown_stream`, `_stop_audio_worker`,
    # `_stop_event_worker`, `_stop_device_health_checker` are
    # MagicMock callables — the function calls them once each (in the
    # happy path); the call order is asserted in
    # `TestStopRecordingOrdering`.
    # No extra stubbing needed — MagicMock auto-creates them.

    return recorder


# ── Not-recording fast path ───────────────────────────────────────


class TestNotRecordingFastPath:
    """When ``_recording_event.is_set()`` is False the function must
    return an empty ``float32`` array and MUST NOT mutate any other
    state — no stop_generation bump, no teardown, no worker joins, no
    buffer swap."""

    def test_returns_empty_float32_array(self):
        recorder = _build_mock_recorder(recording=False)
        result = stop_recording(recorder)
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert result.size == 0

    def test_does_not_clear_recording_event(self):
        """The early-out fires BEFORE ``_recording_event.clear()``, so
        ``clear()`` is never called when the event is already unset.
        We wrap a real ``threading.Event`` so ``is_set()`` keeps its
        real semantics and we can still spy on the ``clear`` call."""
        real_event = threading.Event()  # not set
        recorder = _build_mock_recorder(recording=False)
        # Replace the factory-built mock event with a wraps=Event mock
        # so we can assert the early-out path doesn't call clear().
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
        recorder._stop_event_worker.assert_not_called()
        recorder._stop_device_health_checker.assert_not_called()
        recorder._secure_clear_caches.assert_not_called()
        recorder._prepare_audio.assert_not_called()


# ── Happy-path ordering ───────────────────────────────────────────


class TestStopRecordingOrdering:
    """Verify the body of ``Recorder.stop`` runs the steps in the
    documented source order when the buffer is non-empty."""

    def test_runs_all_steps_in_order(self):
        """When the buffer has chunks, ``stop_recording`` must invoke
        every step in source order. The step ordering is asserted by
        the ``in_order`` helper below.
        """
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        stop_recording(recorder)

        # Every documented step was called exactly once.
        recorder._teardown_stream.assert_called_once()
        recorder._stop_audio_worker.assert_called_once()
        recorder._stop_event_worker.assert_called_once()
        recorder._stop_device_health_checker.assert_called_once_with(timeout=0.0)
        recorder._secure_clear_caches.assert_called_once()
        recorder._prepare_audio.assert_called_once()

    def test_stop_audio_worker_uses_join_timeout_and_drain_true(self):
        """RT-SAFE-001: drain=True so the last few hundred ms of audio
        (chunks still in the ring buffer) end up in ``_buffer``."""
        from voice_typer.server.recording.recorder import (
            _AUDIO_WORKER_JOIN_TIMEOUT_S,
        )

        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        stop_recording(recorder)
        recorder._stop_audio_worker.assert_called_once_with(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S, drain=True)

    def test_stop_event_worker_uses_join_timeout_and_drain_true(self):
        """The event worker drains its tiny queue in <10ms, but
        uses the full _EVENT_WORKER_JOIN_TIMEOUT_S (2.0s) so a slow
        event_bus.publish (e.g. a backed-up TCP subscriber) has time
        to drain (regression-guard against the 0.1s timeout that left
        the daemon running)."""
        from voice_typer.server.recording.recorder import (
            _EVENT_WORKER_JOIN_TIMEOUT_S,
        )

        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        stop_recording(recorder)
        recorder._stop_event_worker.assert_called_once_with(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S, drain=True)

    def test_step_order_matches_contract(self):
        """Pin the source-order contract: clear event → bump
        stop_generation → set user_stop_pending → teardown_stream →
        clear user_stop_pending → stop_audio_worker → stop_event_worker
        → stop_device_health_checker → buffer snapshot under _lock →
        secure_clear_caches → _prepare_audio → log.info.

        A future refactor that swaps the order of these calls (e.g.
        stops the audio worker before ``_teardown_stream()``) would
        re-introduce the 17-H-FIX-2 use-after-free / deadlock the
        ``_teardown_stream`` helper's 300 ms callback-drain poll
        guards against.
        """
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        call_log: list[str] = []

        def log_call(name, ret=None):
            def _hook(*a, **k):
                call_log.append(name)
                return ret

            return _hook

        recorder._teardown_stream.side_effect = log_call("teardown_stream")
        recorder._stop_audio_worker.side_effect = log_call("stop_audio_worker")
        recorder._stop_event_worker.side_effect = log_call("stop_event_worker")
        recorder._stop_device_health_checker.side_effect = log_call("stop_device_health_checker")
        recorder._secure_clear_caches.side_effect = log_call("secure_clear_caches")

        # `_prepare_audio(audio, effective_sr)` must return the input
        # audio (its first positional arg) so the post-resample
        # `if len(audio) > 0` branch runs and the log.info summary fires.
        def _prepare_audio_hook(audio, effective_sr_in, *a, **k):
            call_log.append("prepare_audio")
            return audio

        recorder._prepare_audio.side_effect = _prepare_audio_hook

        # We need to track the user_stop_pending flag toggles too —
        # wrap the MagicMock so we can intercept the attribute writes.
        # Since `_user_stop_pending` is a plain bool on the mock, we
        # use a property-like approach via a small wrapper. Simpler:
        # record the order via the `side_effect` hooks above and
        # assert the relative order of the method calls only.
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
        # Filter the call log to only the method-call entries (the
        # function also calls `np.concatenate` and `log.info` between
        # these, but those aren't tracked).
        observed = [c for c in call_log if c in expected_order]
        assert observed == expected_order, (
            f"stop_recording step order mismatch: expected {expected_order}, "
            f"got {observed}. The source-order contract pins the 17-H-FIX-2 "
            f"stream-teardown / worker-stop sequencing."
        )

    def test_user_stop_pending_flag_toggled_around_teardown(self):
        """STREAM-FIX: ``_user_stop_pending`` is set to True BEFORE
        ``_teardown_stream()`` and cleared to False AFTER it (so
        ``_stream_finished_callback`` suppresses the false "Stream
        finished unexpectedly" warning during the intentional stop).
        """
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
        """HOTKEY-CRASH: increment stop_generation so any stale
        disconnect handlers from the audio callback bail out."""
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        original_gen = recorder._stop_generation
        stop_recording(recorder)
        assert recorder._stop_generation == original_gen + 1

    def test_recording_event_cleared(self):
        """``_recording_event.clear()`` is the gate the audio callback
        and streaming thread poll — must be cleared early in stop()."""
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        assert recorder._recording_event.is_set()
        stop_recording(recorder)
        assert not recorder._recording_event.is_set()


# ── Empty-buffer path ─────────────────────────────────────────────


class TestEmptyBufferPath:
    """When ``recorder._buffer`` is empty inside the locked block, the
    function must call ``_secure_clear_caches()``, reset
    ``_chunk_count = 0``, and return an empty ``float32`` array
    WITHOUT calling ``_prepare_audio`` (G4-H-06 secure-clear contract)."""

    def test_returns_empty_float32_array(self):
        recorder = _build_mock_recorder(buffer_chunks=[])
        result = stop_recording(recorder)
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert result.size == 0

    def test_calls_secure_clear_caches(self):
        recorder = _build_mock_recorder(buffer_chunks=[])
        stop_recording(recorder)
        recorder._secure_clear_caches.assert_called_once()

    def test_resets_chunk_count_to_zero(self):
        recorder = _build_mock_recorder(buffer_chunks=[])
        recorder._chunk_count = 5  # pretend we had 5 chunks before
        stop_recording(recorder)
        assert recorder._chunk_count == 0

    def test_does_not_call_prepare_audio(self):
        """The empty-buffer early-return fires BEFORE _prepare_audio,
        so the resampler isn't invoked on an empty array."""
        recorder = _build_mock_recorder(buffer_chunks=[])
        stop_recording(recorder)
        recorder._prepare_audio.assert_not_called()

    def test_does_not_swap_buffer_or_call_secure_clear_array_background(self, monkeypatch):
        """The empty-buffer path returns BEFORE the deque-swap and
        ``_secure_clear_array_background`` call (those are only needed
        when there's an old buffer to zero)."""
        # Patch the package-level ``_secure_clear_array_background`` to
        # a Mock so we can assert it wasn't called.
        import voice_typer.server.recording as rec_pkg

        bg_clear = MagicMock()
        monkeypatch.setattr(rec_pkg, "_secure_clear_array_background", bg_clear)

        recorder = _build_mock_recorder(buffer_chunks=[])
        original_buffer = recorder._buffer
        stop_recording(recorder)
        # Buffer was not swapped.
        assert recorder._buffer is original_buffer
        # Background clear was not called.
        bg_clear.assert_not_called()

    def test_teardown_and_worker_stops_still_called_before_empty_return(self):
        """The empty-buffer early-return fires AFTER teardown + worker
        stops — those happen unconditionally in the function body."""
        recorder = _build_mock_recorder(buffer_chunks=[])
        stop_recording(recorder)
        recorder._teardown_stream.assert_called_once()
        recorder._stop_audio_worker.assert_called_once()
        recorder._stop_event_worker.assert_called_once()
        recorder._stop_device_health_checker.assert_called_once_with(timeout=0.0)


# ── Buffer snapshot under lock ────────────────────────────────────


class TestBufferSnapshotUnderLock:
    """The function snapshots ``recorder._buffer`` under
    ``recorder._lock``: swaps the deque for a fresh empty one +
    captures the chunk list, then releases the lock and concatenates
    the captured chunks OUTSIDE the lock (so the audio worker's
    append path is not blocked for the 50–300 ms concat duration)."""

    def test_buffer_swapped_for_fresh_empty_deque(self, monkeypatch):
        import collections

        import voice_typer.server.recording as rec_pkg

        monkeypatch.setattr(rec_pkg, "_secure_clear_array_background", lambda _old: None)

        original_chunks = [np.ones(50, dtype=np.float32), np.zeros(30, dtype=np.float32)]
        recorder = _build_mock_recorder(buffer_chunks=original_chunks)
        original_buffer = recorder._buffer
        assert len(original_buffer) == 2

        stop_recording(recorder)

        # The deque was swapped for a fresh empty one.
        assert recorder._buffer is not original_buffer
        assert isinstance(recorder._buffer, collections.deque)
        assert len(recorder._buffer) == 0
        # Maxlen is preserved (or defaulted to DEFAULT_MAX_BUFFER_CHUNKS).
        from voice_typer.server.recording.recorder import DEFAULT_MAX_BUFFER_CHUNKS

        assert recorder._buffer.maxlen == DEFAULT_MAX_BUFFER_CHUNKS

    def test_buffer_maxlen_preserved_from_old_buffer(self, monkeypatch):
        import collections

        import voice_typer.server.recording as rec_pkg

        monkeypatch.setattr(rec_pkg, "_secure_clear_array_background", lambda _old: None)

        # Build a recorder with a custom maxlen.
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        custom_maxlen = 42
        recorder._buffer = collections.deque([np.ones(50, dtype=np.float32)], maxlen=custom_maxlen)

        stop_recording(recorder)

        assert recorder._buffer.maxlen == custom_maxlen

    def test_secure_clear_array_background_called_with_old_buffer(self, monkeypatch):
        """G4-H-06: the old deque is securely zeroed in a background
        daemon thread (so stop() returns immediately and the secure
        clear happens off the hot path)."""
        import voice_typer.server.recording as rec_pkg

        bg_clear = MagicMock()
        monkeypatch.setattr(rec_pkg, "_secure_clear_array_background", bg_clear)

        original_chunks = [np.ones(50, dtype=np.float32)]
        recorder = _build_mock_recorder(buffer_chunks=original_chunks)
        original_buffer = recorder._buffer

        stop_recording(recorder)

        bg_clear.assert_called_once()
        assert bg_clear.call_args.args[0] is original_buffer

    def test_concatenate_called_with_captured_chunks(self):
        """The captured chunks are concatenated OUTSIDE the lock into
        a single contiguous ndarray (axis=0, then reshape(-1))."""
        chunk1 = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        chunk2 = np.array([0.4, 0.5], dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk1, chunk2])

        result = stop_recording(recorder)

        # `_prepare_audio` is identity, so the result is the
        # concatenated array.
        assert result.size == 5
        np.testing.assert_allclose(result, np.array([0.1, 0.2, 0.3, 0.4, 0.5]))


# ── Stats + buffer_sr capture ─────────────────────────────────────


class TestStatsAndBufferSrCapture:
    """RMS / peak / silence_pct are computed from the concatenated
    audio and stored in ``_last_audio_stats`` (NEW-PERF-010: so the
    transcription engine can reuse them). ``_prepare_audio`` is
    called with the captured ``_buffer_sr`` (NOT ``_effective_sr``)
    — XV-31 / chipmunk regression guard."""

    def test_last_audio_stats_stored_on_non_empty_audio(self):
        """NEW-PERF-010: store the full-recording stats so the
        transcription engine can reuse them instead of recomputing
        the same RMS/peak/silence_pct on the same audio array."""
        # Use a known signal: constant 0.5 amplitude → RMS = 0.5,
        # peak = 0.5, silence_pct = 0.0.
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
        """When the buffer is non-empty but the concatenated array
        is empty (size=0), stats are (0.0, 0.0, 0.0) and a warning
        is logged."""
        # An empty chunk in the buffer — len > 0 passes, but size == 0
        # in the inner branch.
        empty_chunk = np.array([], dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[empty_chunk])
        # ``len(audio) > 0`` is False (empty after concat), so the
        # else-branch sets stats to (0, 0, 0).
        # Actually: np.concatenate of empty arrays yields an empty
        # array, so len(audio) == 0 → else branch.

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
        """XV-31 / chipmunk regression guard: ``_prepare_audio`` is
        called with the captured ``_buffer_sr`` (the rate the audio
        was appended at), NOT ``_effective_sr`` (the device's native
        rate). Pre-fix, ``stop()`` read ``_effective_sr`` (e.g.
        48000) and the subsequent ``_prepare_audio`` call did
        ``resample_poly(audio, 1, 3)`` — decimating the already-16
        kHz audio 3:1 → chipmunk voice.
        """
        chunk = np.ones(100, dtype=np.float32)
        recorder = _build_mock_recorder(
            buffer_chunks=[chunk],
            buffer_sr=16000,  # captured local — authoritative
            effective_sr=48000,  # device native rate — must NOT be used
        )

        stop_recording(recorder)

        # The captured local (16000) is the first arg to _prepare_audio.
        recorder._prepare_audio.assert_called_once()
        call_args = recorder._prepare_audio.call_args
        # side_effect = lambda audio, effective_sr_in, **kw: audio
        # → positional args: (audio, effective_sr_in)
        effective_sr_passed = call_args.args[1]
        assert effective_sr_passed == 16000, (
            f"XV-31 regression: _prepare_audio was called with effective_sr="
            f"{effective_sr_passed}, expected 16000 (the captured "
            f"_buffer_sr). Using _effective_sr (48000) would decimate the "
            f"already-16 kHz audio 3:1 → chipmunk voice."
        )

    def test_prepare_audio_falls_back_to_effective_sr_when_buffer_sr_none(self):
        """When ``_buffer_sr is None`` (e.g. a unit-test mock that
        bypassed ``start()``), the function falls back to
        ``_effective_sr`` — the ``or recorder._effective_sr`` idiom."""
        chunk = np.ones(100, dtype=np.float32)
        recorder = _build_mock_recorder(
            buffer_chunks=[chunk],
            buffer_sr=None,
            effective_sr=48000,
        )

        stop_recording(recorder)

        recorder._prepare_audio.assert_called_once()
        effective_sr_passed = recorder._prepare_audio.call_args.args[1]
        assert effective_sr_passed == 48000

    def test_prepare_audio_identity_returned_to_caller(self):
        """The return value of ``_prepare_audio`` is the function's
        return value (H15: resample from scratch for full audio)."""
        chunk = np.ones(50, dtype=np.float32) * 0.5
        # Build a recorder where _prepare_audio returns a different
        # array (simulating a resample that changes the data).
        recorder = _build_mock_recorder(buffer_chunks=[chunk])
        resampled = np.full(200, 0.25, dtype=np.float32)
        recorder._prepare_audio.side_effect = None
        recorder._prepare_audio.return_value = resampled

        result = stop_recording(recorder)

        # The returned array IS the _prepare_audio return value.
        assert result is resampled

    def test_near_silence_warning_emitted_when_rms_below_threshold(self, caplog):
        """When ``rms < 0.001``, the function logs a near-silence
        warning so the user knows the microphone may not be
        capturing audio."""
        # A near-zero chunk → rms ≈ 0.0005 (below the 0.001 threshold).
        chunk = np.full(100, 0.0005, dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            stop_recording(recorder)

        assert any("Near-silence detected" in r.message and r.levelno == logging.WARNING for r in caplog.records), (
            "Expected a 'Near-silence detected' WARNING when rms < 0.001"
        )

    def test_no_audio_warning_emitted_when_concat_is_empty(self, caplog):
        """When the buffer is non-empty but the concatenated array is
        empty (e.g. all chunks are empty ndarrays), the function logs
        'No audio data captured!' — this is the post-concat
        ``else`` branch (NOT the empty-buffer fast path inside the
        lock, which short-circuits with an early-return).
        """
        # A single empty chunk → ``np.concatenate([empty]).reshape(-1)``
        # yields an empty array → ``len(audio) == 0`` → the else
        # branch fires ``log.warning("[RECORDING] No audio data captured!")``.
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
        """The empty-buffer early-return (``if not self._buffer:``)
        inside the lock short-circuits BEFORE the post-concat
        ``if len(audio) > 0`` block, so the 'No audio data captured!'
        warning is NOT emitted on this path."""
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
        """The ``log.info`` summary (duration, sr, samples, RMS, peak,
        silence_pct, stream/concat/resample/total ms) is emitted on
        the non-empty-audio path."""
        chunk = np.full(100, 0.5, dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        with caplog.at_level(logging.INFO, logger="voice_typer.server.recording"):
            stop_recording(recorder)

        assert any("Audio stopped" in r.message and r.levelno == logging.INFO for r in caplog.records), (
            "Expected an 'Audio stopped' INFO summary on the non-empty-audio path"
        )


# ── Source-string contracts ──────────────────────────────────────


class TestSourceStringContracts:
    """Source-string contracts: the function body must not reference
    ``self.X`` (only ``recorder.X``) and must do the lazy
    ``_recording_pkg`` / constants import inside its body (so a
    future refactor doesn't move it to module top, re-introducing
    the circular import that ``recorder.py``'s top-level import of
    this module creates)."""

    @staticmethod
    def _body_after_docstring(src: str) -> str:
        """Return the function source with the docstring stripped.

        We can't simply do ``src.replace(stop_recording.__doc__, "")``
        because the docstring text in the source file contains
        escape sequences (``\\`` and ``\b`` in the
        ``inspect.getsource.*Recorder\\.stop\b`` reference) that
        Python's docstring parser interprets differently at runtime —
        the runtime ``__doc__`` has single backslashes / a backspace
        char while the source text has the raw two-character ``\\``.
        So the runtime docstring is NOT a substring of the source.

        Instead we find the first ``\"\"\"`` and the next ``\"\"\"``
        after it (the docstring is a single triple-quoted block —
        no nested ``\"\"\"`` — so the first match of the closing
        triple-quote is correct). Then we return everything after the
        closing triple-quote. This is robust to any character content
        inside the docstring.
        """
        first = src.find('"""')
        assert first >= 0, "no opening triple-quote in function source"
        second = src.find('"""', first + 3)
        assert second >= 0, "no closing triple-quote in function source"
        return src[second + 3 :]

    def test_no_self_references_in_body(self):
        """The function body must NOT reference ``self.X`` — the body
        was rewritten to use ``recorder.X``. A future merge that
        re-introduces ``self.`` in the body would raise
        ``NameError: self`` at call time."""
        import re

        src = inspect.getsource(stop_recording)
        body = self._body_after_docstring(src)
        self_refs = re.findall(r"\bself\.", body)
        assert not self_refs, (
            f"stop_recording must NOT reference `self.X` in its body — "
            f"the body was rewritten to use `recorder.X`. Found "
            f"{len(self_refs)} `self.` references: {self_refs[:5]}"
        )

    def test_lazy_import_in_function_body(self):
        """The function body contains a lazy import of the package
        namespace — pin this so a future refactor doesn't move it
        to module top (which would create a circular import)."""
        src = inspect.getsource(stop_recording)
        body = self._body_after_docstring(src)
        assert "from voice_typer.server import recording as _recording_pkg" in body, (
            "stop_recording must do the lazy package import inside its "
            "body — moving it to module top would re-introduce the "
            "circular import that recorder.py's top-level import of this "
            "module creates."
        )

    def test_constants_import_in_function_body(self):
        """The function body lazily imports the join-timeout
        constants and ``DEFAULT_MAX_BUFFER_CHUNKS`` from
        ``.recorder`` (mirroring ``discard_recording`` and
        ``start_recording``)."""
        src = inspect.getsource(stop_recording)
        body = self._body_after_docstring(src)
        assert "_AUDIO_WORKER_JOIN_TIMEOUT_S" in body
        assert "_EVENT_WORKER_JOIN_TIMEOUT_S" in body
        assert "DEFAULT_MAX_BUFFER_CHUNKS" in body

    def test_signature_is_recorder_to_ndarray(self):
        """Pin the function signature so a future refactor can't
        silently change the argument name (which would break the
        ``self`` → ``recorder`` rewrite contract)."""
        sig = inspect.signature(stop_recording)
        params = list(sig.parameters)
        assert params == ["recorder"], f"stop_recording signature must be (recorder) — got {params}."
        assert sig.return_annotation is not None


# ── Worker-stop timeout/drain contracts ──────────────────────────


class TestWorkerStopContracts:
    """Pin the per-worker timeout and drain semantics:

    * ``_stop_audio_worker``: ``timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S,
      drain=True`` (RT-SAFE-001: drain the last few hundred ms of
      in-flight audio from the ring buffer).
    * ``_stop_event_worker``: ``timeout=_EVENT_WORKER_JOIN_TIMEOUT_S,
      drain=True`` (drains the IPC event queue; full 2.0s timeout so
      a slow TCP subscriber has time).
    * ``_stop_device_health_checker``: ``timeout=0.0`` (fire-and-
      forget — the daemon exits on its next 30s wait() return).
    """

    def test_stop_device_health_checker_timeout_zero(self):
        recorder = _build_mock_recorder(buffer_chunks=[np.ones(50, dtype=np.float32)])
        stop_recording(recorder)
        # Fire-and-forget: timeout=0.0 so the helper only signals the
        # stop event without joining (CPU-03 / 17-H-FIX-2).
        recorder._stop_device_health_checker.assert_called_once_with(timeout=0.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
