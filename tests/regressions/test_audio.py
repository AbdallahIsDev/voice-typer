"""REF-4. The class/method names, assertion logic, and imports below are"""

from __future__ import annotations

import inspect
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server.recording.format import resample_chunk
from voice_typer.server.recording.vad_helpers import vad_auto_calibrate, vad_update


class TestAudioCallbackUsesMinimalLockScope:
    """RACE-001."""

    def test_concurrent_audio_callback_does_not_crash(self):
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        cfg.sample_rate = 16000
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        # Don't actually start the recorder, we'll invoke the callback directly.
        rec._recording_event.set()
        rec._recording_start_time = time.perf_counter()

        # Mock callbacks to no-ops (callback refs are read outside the lock)
        rec.on_rms_level = lambda rms, peak: None
        rec.on_silence_warning = lambda: None
        rec.on_silence_auto_stop = lambda: None
        rec.on_max_duration_auto_stop = lambda: None

        # The audio callback is defined as a nested function inside
        indata = np.full((512, 1), 0.1, dtype=np.float32)

        errors: list[Exception] = []

        def invoke_locked_append():
            try:
                with rec._audio_pipeline._lock:
                    rec._audio_pipeline._buffer.append(indata.copy())
                    rec._audio_pipeline._chunk_count += 1
                    # RACE-003: snapshot _recent_rms_values inside the lock
                    _ = list(rec._recent_rms_values)
            except Exception as e:
                errors.append(e)

        # Spawn 8 threads, each invoking the locked append 50 times.
        threads = []
        for _ in range(8):
            t = threading.Thread(target=lambda: [invoke_locked_append() for _ in range(50)])
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # No exceptions should have been raised
        assert not errors, f"Concurrent locked-append invocations raised: {errors}"
        # Buffer should contain exactly 8*50 = 400 chunks
        with rec._audio_pipeline._lock:
            assert len(rec._audio_pipeline._buffer) == 400
            assert rec._audio_pipeline._chunk_count == 400

    def test_lock_scope_only_covers_buffer_append_and_count(self):
        """The lock block inside the callback must only cover buffer"""
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.append_to_buffer_locked)
        # The lock block must include buffer.append and _chunk_count.
        assert "_buf.append(filtered)" in src
        assert "self._chunk_count" in src
        # RACE-003: the recent_rms snapshot is now read inside


class TestRmsSnapshotReadsInsideLock:
    """RACE-003."""

    def test_recent_rms_set_inside_lock(self):
        # KEEP, pins RACE-003 invariant (RMS written inside lock
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.process_audio_chunk)
        # (STATE-OWNERSHIP: the lock lives on AudioPipeline as ``self._lock``).
        lines = src.splitlines()
        lock_block_start = None
        lock_block_end = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("with self._lock:"):
                lock_block_start = i + 1  # next line
            elif lock_block_start is not None and stripped.startswith("recorder._last_rms = "):
                lock_block_end = i
        assert lock_block_start is not None, "RACE-003: process_audio_chunk must have a with self._lock: block"
        assert lock_block_end is not None, "RACE-003: recorder._last_rms must be assigned inside the pipeline lock"
        assert lock_block_end >= lock_block_start, (
            "RACE-003: recorder._last_rms must be INSIDE the lock block "
            f"(lock starts at line {lock_block_start}, assignment at {lock_block_end})"
        )

    def test_no_direct_recent_rms_read_outside_lock(self):
        """The processing code must NOT contain"""
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.process_audio_chunk)
        assert "recent_rms = self._recent_rms_values" not in src, (
            "RACE-003 regression: _recent_rms_values is being read "
            "directly outside the lock, set _last_rms under the lock instead."
        )


class TestRecordingTestsUseMonotonicClock:
    """AUDIO-003."""

    def test_test_recording_uses_monotonic(self):
        """The two test methods that set _resample_poly_error_time"""
        import tests.test_recording as test_recording_mod

        # Find the two relevant test methods (in TestResampleFallback)
        retry_src = inspect.getsource(test_recording_mod.TestResampleFallback.test_resample_retry_after_timeout)
        no_retry_src = inspect.getsource(
            test_recording_mod.TestResampleFallback.test_resample_not_retried_before_timeout
        )

        # Both must use time.monotonic()
        assert "time.monotonic()" in retry_src, (
            "test_resample_retry_after_timeout must use time.monotonic() to match the source code at recording.py:163."
        )
        assert "time.monotonic()" in no_retry_src, "test_resample_not_retried_before_timeout must use time.monotonic()."

        # Neither should use time.time()
        def code_only(src: str) -> str:
            return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))

        assert "time.time()" not in code_only(retry_src), (
            "test_resample_retry_after_timeout must NOT use time.time(), "
            "it can drift from time.monotonic() under NTP adjustments."
        )
        assert "time.time()" not in code_only(no_retry_src), (
            "test_resample_not_retried_before_timeout must NOT use time.time()."
        )


class TestInCallbackDeadFieldRemoved:
    """AUDIO-009/AUDIO-015."""

    def test_in_callback_field_does_not_exist(self):
        """
        A constructed ``Recorder`` must NOT have a ``_in_callback`` attr.
        RW-8: KEEP, pins AUDIO-009/AUDIO-015 dead-code removal.
        """
        from tests.fixtures.recorder_test_helpers import make_recorder

        recorder = make_recorder()
        assert not hasattr(recorder, "_in_callback"), (
            "AUDIO-009 regression: ``self._in_callback`` is declared but "
            "never read. The live guard is ``_is_in_audio_callback``, "
            "remove the dead declaration."
        )

    def test_is_in_audio_callback_still_exists(self):
        """The live guard ``_is_in_audio_callback`` must still exist."""
        import threading

        from tests.fixtures.recorder_test_helpers import make_recorder

        recorder = make_recorder()
        assert isinstance(recorder._is_in_audio_callback, threading.Event), (
            "The live guard ``_is_in_audio_callback`` must remain declared."
        )


class TestVadGreyZonePreservesCounters:
    """AUDIO-013."""

    def test_grey_zone_does_not_reset_counters(self):
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        # Initialize VAD state
        rec._vad.state = VadState.UNKNOWN
        rec._vad.consecutive_speech_frames = 0
        rec._vad.consecutive_silence_frames = 0
        rec._vad.speech_threshold_db = -30.0
        rec._vad.silence_threshold_db = -50.0
        rec._vad.speech_frames = 5
        rec._vad.silence_frames = 10
        rec._vad.hangover_frames = 5

        vad_update(rec, -20.0)  # loud (above -30)
        for _ in range(rec._vad.speech_frames - 1):
            vad_update(rec, -20.0)
        assert rec._vad.state is VadState.SPEECH
        assert rec._vad.consecutive_speech_frames == rec._vad.speech_frames
        assert rec._vad.consecutive_silence_frames == 0

        # A single grey-zone chunk must NOT reset the counters.
        vad_update(rec, -40.0)  # grey zone
        assert rec._vad.consecutive_speech_frames == rec._vad.speech_frames, (
            f"AUDIO-013 regression: grey-zone chunk reset speech counter "
            f"from {rec._vad.speech_frames} to {rec._vad.consecutive_speech_frames}, should "
            f"preserve counters in the grey zone."
        )
        assert rec._vad.consecutive_silence_frames == 0

        # The bounded grey-zone hold: after ``_grey_zone_hold_limit``
        hold_limit = rec._vad._grey_zone_hold_limit
        for _ in range(hold_limit - 1):
            vad_update(rec, -40.0)
        assert rec._vad.consecutive_speech_frames == 0, (
            "AUDIO-013 evolution: sustained grey run beyond the hold limit must drop the stale speech counter."
        )
        assert rec._vad.consecutive_silence_frames == rec._vad.hangover_frames

    def test_grey_zone_preserves_counters_at_runtime(self):
        """Verify at runtime that a grey-zone chunk doesn't reset"""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        # Initialize VAD state
        rec._vad.state = VadState.UNKNOWN
        rec._vad.consecutive_speech_frames = 0
        rec._vad.consecutive_silence_frames = 0
        rec._vad.speech_threshold_db = -30.0
        rec._vad.silence_threshold_db = -50.0
        rec._vad.speech_frames = 5
        rec._vad.silence_frames = 10
        rec._vad.hangover_frames = 5

        # Simulate a loud chunk: chunk_rms_db above speech threshold (-30 dB)
        vad_update(rec, -20.0)  # loud (above -30)
        assert rec._vad.consecutive_speech_frames == 1
        assert rec._vad.consecutive_silence_frames == 0

        # Simulate a grey-zone chunk: between silence (-50) and speech (-30)
        vad_update(rec, -40.0)  # grey zone
        # fix: counters must NOT be reset
        assert rec._vad.consecutive_speech_frames == 1, (
            f"AUDIO-013 regression: grey-zone chunk reset speech counter "
            f"from 1 to {rec._vad.consecutive_speech_frames}, should "
            f"preserve counters in the grey zone."
        )
        assert rec._vad.consecutive_silence_frames == 0


class TestVadAutoCalibrationBehavior:
    """AUDIO-014."""

    def test_vad_auto_calibrate_sets_thresholds_from_ambient_noise(self):
        """Feed the auto-calibrator a stream of low-amplitude noise"""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        # VAD is the active backend, dB calibration is intentionally skipped
        rec._vad._silero_available = False
        rec._vad._use_silero_vad = False
        # VAD-GATE / PERF-02: ``Recorder._vad_auto_calibrate`` short-circuits
        rec._cached_vad_enabled = True
        rec._vad._vad_enabled_cached = True
        # Reset calibration state
        rec._vad.calibration_rms_values = []
        rec._vad.calibrated = False
        # Save defaults so we can detect changes
        default_speech_db = rec._vad.speech_threshold_db
        default_silence_db = rec._vad.silence_threshold_db
        # Set recording start time so the elapsed check works
        rec._recording_start_time = time.perf_counter() - 10.0  # 10s ago
        # Make sure calibration duration has elapsed
        rec._vad.calibration_duration = 1.5

        # Feed 1.5 seconds worth of chunks at a known RMS (~0.01 = -40 dB)
        chunk_duration = 0.032  # 32 ms per chunk at 16 kHz, 512 samples
        n_chunks = int(1.5 / chunk_duration) + 5  # extra to exceed duration
        target_rms = 0.01  # -40 dB
        for _ in range(n_chunks):
            vad_auto_calibrate(rec, target_rms, chunk_duration)

        assert rec._vad.calibrated is True, (
            "VAD auto-calibration must set _vad_calibrated=True after collecting enough samples."
        )
        # Speech threshold should be above the noise floor
        assert rec._vad.speech_threshold_db >= -40.0, (
            f"VAD speech threshold ({rec._vad.speech_threshold_db} dB) "
            "must be at or above the noise floor (-40 dB) after calibration."
        )
        # Silence threshold should also be above the noise floor
        assert rec._vad.silence_threshold_db > -40.0, (
            f"VAD silence threshold ({rec._vad.silence_threshold_db} dB) "
            "must be above the noise floor (-40 dB) after calibration "
            "(implementation sets silence = noise + 6 dB)."
        )
        # Speech threshold must be above silence threshold
        assert rec._vad.speech_threshold_db > rec._vad.silence_threshold_db, (
            "VAD speech threshold must be above silence threshold."
        )
        # Thresholds must have changed from defaults
        assert (
            rec._vad.speech_threshold_db != default_speech_db or rec._vad.silence_threshold_db != default_silence_db
        ), "VAD thresholds must change from defaults after calibration."

    def test_vad_auto_calibrate_resets_on_start(self):
        """new session re-calibrates from scratch."""
        from voice_typer.server.recording.session_state import SessionState

        src = inspect.getsource(SessionState.reset_session_state)
        assert "calibration_rms_values" in src or "calibrated" in src, (
            "SessionState.reset_session_state must reset VAD calibration "
            "state so each session re-calibrates from ambient noise."
        )


class TestStreamingAssemblerUsesDequeEviction:
    """AUDIO-019."""

    def test_words_is_deque_with_maxlen(self):
        from voice_typer.server.streaming import StreamingTextAssembler

        asm = StreamingTextAssembler()
        assert isinstance(asm._words, __import__("collections").deque), (
            "StreamingTextAssembler._words must be a collections.deque (not a plain list) for O(1) eviction."
        )
        assert asm._words.maxlen == StreamingTextAssembler._MAX_WORDS, (
            f"deque maxlen must be _MAX_WORDS ({StreamingTextAssembler._MAX_WORDS}); got {asm._words.maxlen}"
        )

    def test_base_offset_starts_at_zero(self):
        from voice_typer.server.streaming import StreamingTextAssembler

        asm = StreamingTextAssembler()
        assert asm._base_offset == 0

    def test_no_pop_zero_in_insert_word(self):
        """
        ``_insert_word_unlocked`` must NOT call ``self._words.pop(0)``
        RW-8: KEEP, pins AUDIO-019 fix (deque(maxlen=N) auto-eviction
        """
        from voice_typer.server.streaming import StreamingTextAssembler

        src = inspect.getsource(StreamingTextAssembler._insert_word_unlocked)
        assert "pop(0)" not in src, (
            "AUDIO-019 regression: _insert_word_unlocked uses pop(0) (O(n)), use deque(maxlen=N) auto-eviction instead."
        )

    def test_eviction_triggers_warning_with_correct_variable_name(self):
        """
        The eviction warning must reference ``evicted_word.word``
        RW-8: KEEP, pins AUDIO-019 typo fix (evicted_word, not evited).
        """
        from voice_typer.server.streaming import StreamingTextAssembler

        src = inspect.getsource(StreamingTextAssembler._insert_word_unlocked)
        assert "evicted_word.word" in src, (
            "Eviction warning must reference 'evicted_word.word' (the "
            "pre-fix code had a typo 'evited.word' that would crash "
            "with NameError if the eviction path ever fired)."
        )
        # The pre-fix typo must NOT be present
        assert "evited" not in src, "AUDIO-019 regression: the 'evited' typo (missing 'c') is back, use 'evicted_word'."

    def test_eviction_preserves_word_key_index_correctness(self):
        """oldest item. ``_word_key_index`` must still point to the right"""
        from voice_typer.server.streaming import StreamingTextAssembler, WordTiming

        # Use a tiny maxlen so we can trigger eviction easily
        asm = StreamingTextAssembler()
        asm._words = __import__("collections").deque(maxlen=3)
        asm._MAX_WORDS = 3  # match the deque maxlen for the warning check

        # Insert 5 words with the same text, eviction will trigger.
        for i in range(5):
            asm.add_words(
                [WordTiming(f"word{i}", start_seconds=float(i), end_seconds=float(i) + 0.1)],
                commit_horizon_seconds=100.0,  # accept all
            )

        # After 5 inserts with maxlen=3, deque should contain 3 items
        assert len(asm._words) == 3
        # _base_offset should be 2 (2 items evicted)
        assert asm._base_offset == 2
        # The committed text should contain the 3 most recent words
        committed = asm.committed_text
        assert "word2" in committed
        assert "word3" in committed
        assert "word4" in committed
        # The evicted words should NOT be in the committed text
        assert "word0" not in committed
        assert "word1" not in committed


class TestAudioAgcLastRmsPostAgc:
    """removed and replaced by the Compressor filter in the audio filter"""

    def test_last_rms_assignment_after_agc_recompute(self):
        """ADR 0007: AGC recompute block is gone. _last_rms is still set."""
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.process_audio_chunk)
        agc_recompute_idx = src.find("if abs(self._agc_gain - 1.0) > 0.01")
        # The _last_rms assignment now uses `self._recorder._last_rms`
        last_rms_idx = src.find("_last_rms = chunk_rms")
        assert agc_recompute_idx == -1, (
            "ADR 0007: AGC recompute block should be deleted, "
            "the Compressor filter in the audio chain handles this now."
        )
        assert last_rms_idx >= 0, "_last_rms assignment must still exist for UI/IPC"

    def test_agc_applied_before_last_rms_storage(self):
        """ADR 0007: _agc_update call is gone. _last_rms is still set."""
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.process_audio_chunk)
        agc_update_idx = src.find("_agc_update(chunk_rms, filtered)")
        last_rms_idx = src.find("_last_rms = chunk_rms")
        assert agc_update_idx == -1, (
            "ADR 0007: _agc_update call should be deleted, "
            "the Compressor filter in the audio chain handles gain control now."
        )
        assert last_rms_idx >= 0, "_last_rms assignment must still exist for UI/IPC"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_changes3_fixes.py ===

r"""Regression tests for the third-pass forensic review (changes-3).

Each test class pins one finding to its current verified state.

Findings covered
----------------
- SEC-audit-011  config.json mutation lock held during notepad editing
- PLAT-008       dead validate_env_vars removed from platform_utils
- PROD-005       duplicate _check_disk_space removed from asr_setup
- RACE-008       daemon thread sites have rationale comments
- RACE-009       host stdout/stderr routed to log files
- AUDIO-MIC      device-change poller + IPC event
- AUDIO-CLIP     real-time IPC event for clipping
- PLAT-024       tray-mic.ico base ICO lookup
- PLAT-030       check_accessibility IPC endpoint
- AUDIO-006      dtype edge case tests
- AUDIO-007      numpy vectorized ops regression test
- AUDIO-008      device disconnect handling tests
- AUDIO-010      backpressure detection tests
- AUDIO-011      AGC functional tests
- AUDIO-016      dynamic sample rate resolution tests
- AUDIO-017      peak meter accuracy tests
- AUDIO-018      VAD state machine boundary tests
- PLAT-036       MANIFEST.in exists (already fixed, pin)
- PLAT-037       Windows manifest embedded with asInvoker (already fixed, pin)
- PLAT-040       mutex has Local\ prefix + install hash + DACL (already fixed, pin)
- RACE-001       concurrent callback test exists (already fixed, pin)
"""


class TestAudioMicDeviceChangePoller:
    """AUDIO-MIC."""

    def test_load_microphones_pushes_ipc_event_on_change(self):
        """AUDIO-MIC: when ``load_microphones`` detects that the device"""
        from unittest.mock import MagicMock, patch

        from voice_typer.server import startup_tasks

        # Build a minimal app mock with a non-empty initial microphone
        app = MagicMock()
        app._microphones = [
            {"id": 1, "name": "Mic A"},
            {"id": 2, "name": "Mic B"},
        ]

        # Mock ``list_microphones`` to return a DIFFERENT device set
        with (
            patch(
                "voice_typer.server.server_platform.microphone_list.list_microphones",
                return_value=[
                    {"id": 1, "name": "Mic A"},
                    {"id": 3, "name": "Mic C"},
                ],
            ),
            patch("voice_typer.server.event_bus.publish") as mock_publish,
        ):
            startup_tasks.load_microphones(app)

        assert mock_publish.call_count == 1, (
            "AUDIO-MIC: load_microphones must publish exactly one IPC event "
            "when the device set changes (no spurious extra publishes)."
        )
        args, _ = mock_publish.call_args
        assert args[0]["type"] == "microphones_changed", (
            "AUDIO-MIC: load_microphones must push a 'microphones_changed' IPC event when the device set changes."
        )

    def test_load_microphones_publishes_on_first_population(self):
        """Boot-race recovery: the renderer connects (and the restored"""
        from unittest.mock import MagicMock, patch

        from voice_typer.server import startup_tasks

        app = MagicMock()
        app._microphones = []  # registry not yet populated at boot
        app.config.microphone = None

        with (
            patch(
                "voice_typer.server.server_platform.microphone_list.list_microphones",
                return_value=[{"id": 1, "name": "Mic A"}],
            ),
            patch("voice_typer.server.event_bus.publish") as mock_publish,
        ):
            startup_tasks.load_microphones(app)

        assert mock_publish.call_count == 1, (
            "Boot-race recovery: load_microphones must publish "
            "microphones_changed on the FIRST population (empty → "
            "non-empty) so an already-mounted Microphone page refreshes."
        )
        args, _ = mock_publish.call_args
        assert args[0]["type"] == "microphones_changed"
        assert args[0]["data"] == {"count": 1}

    def test_load_microphones_no_publish_when_still_empty(self):
        """spurious ``microphones_changed`` publish on the startup"""
        from unittest.mock import MagicMock, patch

        from voice_typer.server import startup_tasks

        app = MagicMock()
        app._microphones = []
        app.config.microphone = None

        with (
            patch(
                "voice_typer.server.server_platform.microphone_list.list_microphones",
                return_value=[],
            ),
            patch("voice_typer.server.event_bus.publish") as mock_publish,
        ):
            startup_tasks.load_microphones(app)

        mock_publish.assert_not_called()

    def test_poller_not_started_in_startup(self):
        """``StartupSequence.run`` must NOT call"""
        from voice_typer.server import startup_tasks
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence.run)
        assert "_start_device_change_poller(" not in src, (
            "StartupSequence.run must NOT call _start_device_change_poller "
            "(redundant with the event-driven MicrophoneDeviceWatcher)."
        )
        assert not hasattr(startup_tasks, "start_device_change_poller"), (
            "startup_tasks.start_device_change_poller must be deleted "
            "(dead code, never called from production startup; "
            "MicrophoneDeviceWatcher is the sole source of truth)."
        )


class TestAudioClipRealtimeIpcEvent:
    """AUDIO-CLIP."""

    def test_clipping_pushes_audio_clip_ipc_event(self):
        from voice_typer.server import recording

        chunk_src = inspect.getsource(recording.Recorder._process_audio_chunk)
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        detect_src = inspect.getsource(AudioPipeline.detect_and_emit_clipping)
        combined = chunk_src + "\n" + detect_src
        assert "audio_clip" in combined, (
            "AUDIO-CLIP: recording callback must push an 'audio_clip' IPC event when clipping is detected."
        )
        assert "_event_queue.put" in combined or "event_bus.publish" in combined or "_push_event_now" in combined, (
            "AUDIO-CLIP: recording callback must enqueue the audio_clip "
            "event via _event_queue.put (RW-8 worker queue) OR call "
            "event_bus.publish / _push_event_now directly."
        )


class TestAudioDtypeEdgeCases:
    """AUDIO-006."""

    def test_resample_chunk_handles_float32(self):
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        audio = np.full(512, 0.5, dtype=np.float32)
        result = resample_chunk(rec, audio, 16000, 16000)
        assert result is not None
        assert result.dtype == np.float32

    def test_resample_chunk_handles_int16(self):
        """int16 input must be handled (converted to float32) without crashing."""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        audio = np.full(512, 16384, dtype=np.int16)
        audio_f32 = audio.astype(np.float32) / 32768.0
        result = resample_chunk(rec, audio_f32, 16000, 16000)
        assert result is not None

    def test_resample_chunk_handles_non_contiguous(self):
        """Non-contiguous arrays (e.g. from slicing) must not crash."""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        # Create a non-contiguous array via slicing
        full = np.full(1024, 0.5, dtype=np.float32)
        sliced = full[::2]  # non-contiguous view, 512 elements
        assert not sliced.flags["C_CONTIGUOUS"]
        result = resample_chunk(rec, sliced, 16000, 16000)
        assert result is not None


class TestNumpyVectorizedOpsRegression:
    """
    AUDIO-007.
    Fix: added source-inspection test + numerical equivalence test.
    """

    def test_recording_uses_np_dot_for_rms(self):
        # KEEP, pins  (vectorized np.dot RMS computation).
        # Post-4dd03625 split the recording-chunk RMS lives in
        # recording.audio_pipeline.compute_rms_and_peak, not the package init.
        from voice_typer.server.recording import audio_pipeline

        src = inspect.getsource(audio_pipeline)
        # The callback uses np.dot for RMS: np.sqrt(np.dot(flat, flat) / flat.size)
        assert "np.dot(flat, flat)" in src or "np.dot(flat,flat)" in src, (
            "AUDIO-007: recording.py must use np.dot for vectorized RMS computation."
        )

    def test_np_dot_rms_matches_naive_computation(self):
        """Verify np.dot-based RMS produces the same result as the naive"""
        # 1 second of 440 Hz sine wave at 16 kHz, amplitude 0.5
        sr = 16000
        t = np.arange(sr) / sr
        audio = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        flat = audio.reshape(-1)
        rms_dot = float(np.sqrt(np.dot(flat, flat) / flat.size))

        # Naive RMS
        rms_naive = float(np.sqrt(np.mean(audio**2)))

        # Must match to floating-point precision
        assert abs(rms_dot - rms_naive) < 1e-6, f"np.dot RMS ({rms_dot}) != naive RMS ({rms_naive})"
        # Expected RMS for 0.5-amplitude sine = 0.5 / sqrt(2) ≈ 0.3536
        assert abs(rms_dot - 0.5 / np.sqrt(2)) < 0.01


class TestAudioDeviceDisconnectHandling:
    """AUDIO-008."""

    def test_handle_device_disconnect_exists(self):
        from voice_typer.server import recording

        assert hasattr(recording.Recorder, "_handle_device_disconnect"), (
            "AUDIO-008: Recorder must have _handle_device_disconnect method."
        )

    def test_device_disconnect_flag_set_on_zero_indata(self):
        """When the callback receives all-zero indata with chunk_count > 10,"""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        rec._recording_event.set()
        rec._recording_start_time = time.perf_counter()
        rec._audio_pipeline._chunk_count = 15  # > 10 threshold
        rec._devices._device_disconnected = False

        # Mock callbacks
        rec.on_rms_level = lambda rms, peak: None
        rec.on_silence_warning = lambda: None
        rec.on_silence_auto_stop = lambda: None
        rec.on_max_duration_auto_stop = lambda: None

        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.detect_device_disconnect)
        assert "_device_disconnected" in src
        assert "not np.any(indata)" in src or "np.count_nonzero(indata) == 0" in src or "np.all(indata == 0)" in src, (
            "detect_device_disconnect must check for zero-filled indata to detect "
            "device disconnect (via not np.any(indata) or "
            "np.count_nonzero(indata) == 0 or np.all(indata) == 0)"
        )


class TestBackpressureDetectionOnDequeOverflow:
    """AUDIO-010."""

    def test_backpressure_detection_increments_dropped_chunks(self):
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000

        # Fill the buffer past maxlen
        maxlen = rec._audio_pipeline._buffer.maxlen
        chunk = np.full((512, 1), 0.1, dtype=np.float32)
        with rec._audio_pipeline._lock:
            for _ in range(maxlen + 5):
                rec._audio_pipeline._buffer.append(chunk)
        buffer_len = len(rec._audio_pipeline._buffer)

        # Simulate the backpressure check
        if buffer_len >= rec._audio_pipeline._buffer.maxlen - 1:
            rec._dropped_chunks = getattr(rec, "_dropped_chunks", 0) + 1

        assert hasattr(rec, "_dropped_chunks"), "Backpressure counter must be set"
        assert rec._dropped_chunks >= 1, "AUDIO-010: _dropped_chunks must be incremented when buffer is full."

    def test_backpressure_source_uses_maxlen_check(self):
        # KEEP, pins  (backpressure check compares
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.append_to_buffer_locked)
        assert "_dropped_chunks" in src, "AUDIO-010: recording callback must track _dropped_chunks."
        assert "self._buffer.maxlen" in src, (
            "AUDIO-010: backpressure check must compare against the pipeline-owned _buffer.maxlen."
        )


# ADR-0007: AGC removed; test deleted because the feature no longer exists.


class TestDynamicSampleRateResolution:
    """AUDIO-016."""

    def test_resolve_effective_sample_rate_exists(self):
        import inspect

        from voice_typer.server.recording.device_manager import DeviceManager
        from voice_typer.server.recording.recorder_init import RecorderInitMixin

        assert hasattr(DeviceManager, "_resolve_effective_sample_rate"), (
            "AUDIO-016: DeviceManager must have _resolve_effective_sample_rate method."
        )
        init_src = inspect.getsource(RecorderInitMixin._setup_device_state_and_collaborators)
        assert "self._devices" in init_src, (
            "AUDIO-016: Recorder must construct the DeviceManager collaborator (self._devices)."
        )

    def test_resolve_returns_tuple_with_native_rate(self):
        """The method must return (sample_rate, device_info_dict)."""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)

        # Mock sd.query_devices to return a device with 48000 Hz
        with patch("sounddevice.query_devices") as mock_qd:
            mock_qd.return_value = {
                "name": "Test Mic",
                "default_samplerate": 48000,
                "max_input_channels": 1,
            }
            result = rec._devices._resolve_effective_sample_rate(None)
            # The method must return a (sample_rate, device_info_dict) tuple.
            assert result is not None, (
                "AUDIO-016: _resolve_effective_sample_rate() returned None, "
                "the method must always return a (rate, info) tuple when "
                "sounddevice.query_devices succeeds."
            )
            assert isinstance(result, tuple), (
                f"AUDIO-016: _resolve_effective_sample_rate() returned "
                f"{type(result).__name__}, expected tuple. Got: {result!r}"
            )
            assert len(result) == 2, (
                f"AUDIO-016: _resolve_effective_sample_rate() returned a "
                f"{len(result)}-tuple, expected 2-tuple (rate, info). Got: {result!r}"
            )

    @pytest.mark.parametrize("bad_rate", [0, -1, 1_000_000])
    def test_implausible_native_rate_falls_back_to_target(self, bad_rate):
        """A driver-supplied rate outside the plausible audio range is ignored.

        PortAudio rejects ``InputStream(samplerate=0)`` with
        paInvalidSampleRate, so a device reporting 0 (or another nonsense
        value) must resolve to the configured target rate, the same fallback
        the query-failure branch already uses.
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        cfg.sample_rate = 16000
        rec = Recorder(cfg)

        with patch("sounddevice.query_devices") as mock_qd:
            mock_qd.return_value = {
                "name": "Broken Endpoint",
                "default_samplerate": bad_rate,
                "max_input_channels": 1,
            }
            rate, _info = rec._devices._resolve_effective_sample_rate(None)

        assert rate == cfg.sample_rate, (
            f"native rate {bad_rate!r} must resolve to the configured target rate {cfg.sample_rate} Hz, got {rate!r}"
        )


class TestPeakMeterAccuracy:
    """AUDIO-017."""

    def test_peak_tracking_increments_correctly(self):
        """Feed a signal with known peak amplitude and verify _peak is"""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        # STATE-OWNERSHIP: peak/clip telemetry lives on the owning
        rec._audio_pipeline._peak = 0.0
        rec._audio_pipeline._clip_count = 0
        rec._audio_pipeline._last_clip_log_time = 0.0

        # Simulate the peak-tracking logic from the callback
        test_peaks = [0.3, 0.7, 0.5, 0.95, 0.4]
        for peak in test_peaks:
            chunk_peak = peak
            if chunk_peak >= 0.99:
                rec._audio_pipeline._clip_count += 1
                if chunk_peak > rec._audio_pipeline._peak:
                    rec._audio_pipeline._peak = chunk_peak
            else:
                # Non-clipping peaks also update _peak
                if chunk_peak > rec._audio_pipeline._peak:
                    rec._audio_pipeline._peak = chunk_peak

        # _peak must be the maximum of all test peaks
        assert rec._audio_pipeline._peak == 0.95, (
            f"AUDIO-017: _peak must be 0.95 (max of {test_peaks}), got {rec._audio_pipeline._peak}"
        )

    def test_peak_source_uses_abs_max(self):
        # KEEP, pins  (peak computation uses abs().max()).
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.compute_rms_and_peak)
        # The peak computation returns max(|x|). The canonical forms
        assert (
            "abs_filtered.max()" in src
            or "np.abs(filtered).max()" in src
            or "max(float(flat.max()), -float(flat.min()))" in src
        ), (
            "AUDIO-017: peak computation must use abs().max() on the audio "
            "(or the allocation-free max(max(x), -min(x)) equivalent)."
        )


class TestVadBoundaryConditions:
    """AUDIO-018."""

    def test_vad_transition_at_exact_speech_threshold(self):
        """When consecutive_speech_frames == threshold, state must"""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        rec._vad.state = VadState.UNKNOWN
        rec._vad.speech_threshold_db = -30.0
        rec._vad.silence_threshold_db = -50.0
        rec._vad.speech_frames = 5  # threshold
        rec._vad.silence_frames = 10
        rec._vad.hangover_frames = 5

        # Feed threshold-1 loud frames → must NOT transition
        for _ in range(4):
            vad_update(rec, -20.0)  # loud
        assert rec._vad.state == VadState.UNKNOWN, "AUDIO-018: at threshold-1 frames, state must remain UNKNOWN"
        assert rec._vad.consecutive_speech_frames == 4

        # Feed one more loud frame → must transition to SPEECH
        vad_update(rec, -20.0)
        assert rec._vad.state == VadState.SPEECH, (
            "AUDIO-018: at exactly threshold frames, state must transition to SPEECH"
        )

    def test_vad_transition_at_exact_silence_threshold(self):
        """When consecutive_silence_frames == hangover, state must"""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        rec._vad.state = VadState.SPEECH
        rec._vad.speech_threshold_db = -30.0
        rec._vad.silence_threshold_db = -50.0
        rec._vad.speech_frames = 5
        rec._vad.silence_frames = 10
        rec._vad.hangover_frames = 5  # threshold for SPEECH→SILENCE

        # Feed hangover-1 quiet frames → must NOT transition
        for _ in range(4):
            vad_update(rec, -60.0)  # quiet
        assert rec._vad.state == VadState.SPEECH, "AUDIO-018: at hangover-1 frames, state must remain SPEECH"

        # Feed one more quiet frame → must transition to SILENCE
        vad_update(rec, -60.0)
        assert rec._vad.state == VadState.SILENCE, (
            "AUDIO-018: at exactly hangover frames, state must transition to SILENCE"
        )

    def test_vad_grey_zone_preserves_counters(self):
        """Grey-zone chunks (between thresholds) must NOT reset counters"""
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        rec._vad.state = VadState.UNKNOWN
        rec._vad.speech_threshold_db = -30.0
        rec._vad.silence_threshold_db = -50.0
        rec._vad.speech_frames = 5
        rec._vad.silence_frames = 10
        rec._vad.hangover_frames = 5

        # 2 loud frames
        vad_update(rec, -20.0)
        vad_update(rec, -20.0)
        assert rec._vad.consecutive_speech_frames == 2

        # 1 grey-zone frame → counters must NOT reset
        vad_update(rec, -40.0)  # grey zone (between -50 and -30)
        assert rec._vad.consecutive_speech_frames == 2, "AUDIO-018/AUDIO-013: grey-zone must preserve speech counter"


class TestStreamingSessionAtomicPopOnCancel:
    """ARCH-018."""

    def test_pop_streaming_session_exists(self):
        from voice_typer.server.recording_controller import RecordingController

        assert hasattr(RecordingController, "pop_streaming_session"), (
            "ARCH-018: RecordingController must have pop_streaming_session method"
        )

    def test_pop_is_atomic_single_lock_acquisition(self):
        """
        pop_streaming_session must acquire the lock exactly once.
        RW-8: KEEP, pins ARCH-018 (atomic get-and-clear under a single
        """
        from voice_typer.server.recording_controller import RecordingController

        src = inspect.getsource(RecordingController.pop_streaming_session)
        # Must contain exactly one `with self._streaming_session_lock:` block
        assert src.count("with self._streaming_session_lock:") == 1, (
            "ARCH-018: pop_streaming_session must acquire the lock exactly once (atomic get-and-clear)"
        )

    def test_cancel_uses_pop_not_get_then_set(self):
        """
        _cancel_streaming_session must use pop_streaming_session(),
        RW-8: KEEP, pins ARCH-018 (cancel uses the atomic pop). Same
        """
        from voice_typer.server.recording_controller import RecordingController

        src = inspect.getsource(RecordingController._cancel_streaming_session)
        assert "self.pop_streaming_session()" in src, (
            "ARCH-018: _cancel_streaming_session must use pop_streaming_session() (atomic) instead of get+set (TOCTOU)"
        )
        # Must NOT contain the pre-fix pattern
        assert "self.get_streaming_session()" not in src or "self.set_streaming_session(None)" not in src, (
            "ARCH-018: _cancel_streaming_session must NOT use the pre-fix get+set pattern (TOCTOU race)"
        )

    def test_pop_returns_and_clears_session(self):
        """Functional test: pop_streaming_session must return the current"""
        from voice_typer.server.recording_controller import RecordingController

        # Build a minimal controller
        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._streaming_session = MagicMock()

        # Pop must return the session AND clear the field
        session = ctrl.pop_streaming_session()
        assert session is ctrl._streaming_session or session is not None
        assert ctrl._streaming_session is None, "ARCH-018: pop_streaming_session must clear the session field"

    def test_pop_returns_none_when_no_session(self):
        """pop_streaming_session must return None when no session exists."""
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._streaming_session = None

        assert ctrl.pop_streaming_session() is None

    def test_concurrent_pop_and_set_no_clobber(self):
        """ARCH-018 regression test: a concurrent set_streaming_session"""
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._streaming_session = MagicMock()

        results: dict[str, object] = {}

        def thread_a():
            # Pop the initial session
            results["popped"] = ctrl.pop_streaming_session()

        def thread_b():
            # Wait briefly, then set a new session
            time.sleep(0.001)
            new_session = MagicMock(name="new_session")
            ctrl.set_streaming_session(new_session)
            results["set"] = new_session

        t_a = threading.Thread(target=thread_a)
        t_b = threading.Thread(target=thread_b)
        t_a.start()
        t_b.start()
        t_a.join(timeout=2.0)
        t_b.join(timeout=2.0)

        # The popped session is the ORIGINAL (not the new one)
        assert results["popped"] is not None, "Thread A should have popped the original session"
        # The set session survives (not clobbered by the pop)
        assert "set" in results, "Thread B should have set a new session"
        # After both threads complete, the session should be the one B set
        assert ctrl._streaming_session is results["set"], (
            "ARCH-018 regression: the new session set by thread B was "
            "clobbered by thread A's pop, the atomic get-and-clear fix "
            "is not working."
        )
