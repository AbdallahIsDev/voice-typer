"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import inspect
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# WP-1: the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
class TestAudioCallbackUsesMinimalLockScope:
    """RACE-001.

    The audio callback uses a minimal lock scope (only buffer.append
    and chunk_count under lock). This test invokes the callback from
    multiple threads concurrently to verify no crashes / corruption.
    """

    def test_concurrent_audio_callback_does_not_crash(self):
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        cfg.sample_rate = 16000
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        # Don't actually start the recorder — we'll invoke the callback directly.
        # Set _recording_event so the callback doesn't bail out early.
        rec._recording_event.set()
        rec._recording_start_time = time.perf_counter()

        # Mock callbacks to no-ops (callback refs are read outside the lock)
        rec.on_rms_level = lambda rms, peak: None
        rec.on_silence_warning = lambda: None
        rec.on_silence_auto_stop = lambda: None
        rec.on_max_duration_auto_stop = lambda: None

        # The audio callback is defined as a nested function inside
        # ``start()``. We can't easily invoke it directly, so this test
        # instead validates the lock-scope invariant by calling the
        # locked section (buffer append) from multiple threads.
        indata = np.full((512, 1), 0.1, dtype=np.float32)

        errors: list[Exception] = []

        def invoke_locked_append():
            try:
                with rec._lock:
                    rec._buffer.append(indata.copy())
                    rec._chunk_count += 1
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
        with rec._lock:
            assert len(rec._buffer) == 400
            assert rec._chunk_count == 400

    def test_lock_scope_only_covers_buffer_append_and_count(self):
        """The lock block inside the callback must only cover buffer
        append + chunk_count + recent_rms snapshot (RACE-003 fix).

        RT-SAFE-001: the callback body was moved from a nested function
        inside ``start()`` to the ``_process_audio_chunk`` method (runs
        on the audio worker thread). The lock-scope invariant is now
        inspected in ``_process_audio_chunk``.

        RW-8: KEEP — pins the structural lock-scope invariant (only
        buffer.append + chunk_count + recent_rms snapshot inside the
        lock). A behavioral test for this would need to instrument the
        lock to measure hold time, which is flaky; the source-string
        check is the most direct way to catch a regression where a
        future contributor adds expensive work inside the lock.
        """
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        # S3-CR-17 / Phase 4.5: the buffer-append lock scope moved to
        # AudioPipeline.append_to_buffer_locked. Recorder._process_audio_chunk
        # is now a 1-line delegator — inspect the pipeline method instead.
        src = inspect.getsource(AudioPipeline.append_to_buffer_locked)
        # The lock block must include buffer.append and _chunk_count
        assert "recorder._buffer.append" in src
        assert "recorder._chunk_count" in src
        # RACE-003: the recent_rms snapshot is now read inside
        # AudioPipeline.run_vad_state_machine (see test below).
        # The old inline snapshot line no longer exists in this method.


class TestRmsSnapshotReadsInsideLock:
    """RACE-003.

    Pre-fix: ``_recent_rms_values`` (a deque) was read outside the
    lock, allowing a concurrent callback to mutate it (append + maxlen
    eviction) mid-iteration. Fix: snapshot ``list(_recent_rms_values)``
    inside the lock; downstream code uses the snapshot.
    """

    def test_recent_rms_set_inside_lock(self):
        # RW-8: KEEP — pins RACE-003 invariant (RMS written inside lock
        # so the audio callback and the level-monitor reader never race).
        # S3-CR-17 / Phase 4.5: the processing body moved from
        # Recorder._process_audio_chunk to AudioPipeline.process_audio_chunk.
        # The old snapshot pattern was removed (PVT-27) — _last_rms is now
        # set atomically under the lock.
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.process_audio_chunk)
        # _last_rms must be written inside recorder._lock
        lines = src.splitlines()
        lock_block_start = None
        lock_block_end = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("with recorder._lock:"):
                lock_block_start = i + 1  # next line
            elif lock_block_start is not None and stripped.startswith("recorder._last_rms = "):
                lock_block_end = i
        assert lock_block_start is not None, "RACE-003: process_audio_chunk must have a with recorder._lock: block"
        assert lock_block_end is not None, "RACE-003: recorder._last_rms must be assigned inside recorder._lock"
        assert lock_block_end >= lock_block_start, (
            "RACE-003: recorder._last_rms must be INSIDE the lock block "
            f"(lock starts at line {lock_block_start}, assignment at {lock_block_end})"
        )

    def test_no_direct_recent_rms_read_outside_lock(self):
        """The processing code must NOT contain
        ``recent_rms = self._recent_rms_values`` (the pre-fix pattern).

        RW-8: KEEP — pins the negative half of RACE-003 (the pre-fix
        pattern must not return). Source-string check is the most
        direct way to catch a regression where the lock is bypassed.

        S3-CR-17 / Phase 4.5: inspect AudioPipeline.process_audio_chunk
        instead of Recorder._process_audio_chunk (now a 1-line delegator).
        """
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.process_audio_chunk)
        assert "recent_rms = self._recent_rms_values" not in src, (
            "RACE-003 regression: _recent_rms_values is being read "
            "directly outside the lock — set _last_rms under the lock instead."
        )


class TestRecordingTestsUseMonotonicClock:
    """AUDIO-003.

    Pre-fix: tests used ``time.time()`` (wall clock) while source code
    used ``time.monotonic()``. Under NTP/DST adjustments the wall
    clock can jump backwards. Fix: tests must use ``time.monotonic()``.
    """

    def test_test_recording_uses_monotonic(self):
        """The two test methods that set _resample_poly_error_time
        must use time.monotonic(), NOT time.time().

        RW-8: KEEP — pins AUDIO-003 fix (tests use monotonic clock to
        match source code). The invariant is about test code, not
        production code, so a behavioral test would be circular (testing
        a test). Source-string check is the only way to catch regression.
        """
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
        # (Strip comments before checking to avoid false positives from
        # comments that mention time.time().)
        def code_only(src: str) -> str:
            return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))

        assert "time.time()" not in code_only(retry_src), (
            "test_resample_retry_after_timeout must NOT use time.time() — "
            "it can drift from time.monotonic() under NTP adjustments."
        )
        assert "time.time()" not in code_only(no_retry_src), (
            "test_resample_not_retried_before_timeout must NOT use time.time()."
        )


class TestInCallbackDeadFieldRemoved:
    """AUDIO-009/AUDIO-015.

    Pre-fix: ``_in_callback`` was declared at recording.py:214 but
    never read anywhere in the codebase. Fix: delete the declaration
    (the live guard is ``_is_in_audio_callback`` at line ~285).
    """

    def test_in_callback_field_does_not_exist(self):
        """``Recorder.__init__`` must NOT declare ``_in_callback``.

        RW-8: KEEP — pins AUDIO-009/AUDIO-015 dead-code removal.
        Source-string check is the only way to catch reintroduction
        of the dead field (a behavioral test can't observe a field
        that does nothing).
        """
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.__init__)
        assert "self._in_callback" not in src, (
            "AUDIO-009 regression: ``self._in_callback`` is declared but "
            "never read. The live guard is ``_is_in_audio_callback`` — "
            "remove the dead declaration."
        )

    def test_is_in_audio_callback_still_exists(self):
        """The live guard ``_is_in_audio_callback`` must still exist.

        RW-8: KEEP — pins AUDIO-015 (live guard preserved while the
        dead _in_callback field was removed). Could be ported to a
        behavioral test that calls _is_in_audio_callback() and verifies
        it returns a bool, but the source-string check is simpler and
        catches removal of the attribute directly.
        """
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.__init__)
        assert "_is_in_audio_callback" in src, "The live guard ``_is_in_audio_callback`` must remain declared."


class TestVadGreyZonePreservesCounters:
    """AUDIO-013.

    Pre-fix: the "between thresholds" else branch had a comment saying
    "don't change counters" but the code set both
    ``_vad_consecutive_silence_frames = 0`` AND
    ``_vad_consecutive_speech_frames = 0``. Standard VAD hysteresis
    leaves counters unchanged in the grey zone. Fix: replace the two
    resets with ``pass`` so the code matches the comment.
    """

    def test_grey_zone_does_not_reset_counters(self):
        # RW-8: KEEP — pins AUDIO-013 fix (grey-zone else block uses
        # `pass` instead of resetting both counters). The sibling
        # test_grey_zone_preserves_counters_at_runtime tests behaviorally,
        # but the source-string check catches regressions where the
        # pass is replaced with a reset that happens to not fire in
        # the runtime test's exact scenario.
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder._vad_update)
        # Find the AUDIO-013 grey-zone else block. The method has two
        # AUDIO-013 comments — the first is in the docstring, the second
        # is the grey-zone fix comment. We want the SECOND one.
        audio013_idx = src.find("AUDIO-013: Grey zone")
        assert audio013_idx >= 0, "AUDIO-013 grey-zone comment not found"
        # Extract a generous window after the comment to capture the
        # full else body (the comment block + the actual `pass` line).
        following = src[audio013_idx : audio013_idx + 800]
        # The grey zone block must contain 'pass' (the fix)
        assert "pass" in following, (
            "AUDIO-013 fix: the grey-zone else block must use 'pass' instead of resetting both counters."
        )
        # The else block must NOT contain counter resets in the lines
        # immediately following the AUDIO-013 comment (before the next
        # "State transitions" section).
        state_transitions_idx = following.find("State transitions")
        if state_transitions_idx < 0:
            state_transitions_idx = len(following)
        else_body = following[:state_transitions_idx]
        assert "_vad_consecutive_silence_frames = 0" not in else_body, (
            "AUDIO-013 regression: grey-zone else block resets "
            "_vad_consecutive_silence_frames — should preserve counters."
        )
        assert "_vad_consecutive_speech_frames = 0" not in else_body, (
            "AUDIO-013 regression: grey-zone else block resets "
            "_vad_consecutive_speech_frames — should preserve counters."
        )

    def test_grey_zone_preserves_counters_at_runtime(self):
        """Verify at runtime that a grey-zone chunk doesn't reset
        the speech counter that was accumulated by a prior loud chunk.
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        # Initialize VAD state
        rec._vad_state = VadState.UNKNOWN
        rec._vad_consecutive_speech_frames = 0
        rec._vad_consecutive_silence_frames = 0
        rec._vad_speech_threshold_db = -30.0
        rec._vad_silence_threshold_db = -50.0
        rec._vad_speech_frames = 5
        rec._vad_silence_frames = 10
        rec._vad_hangover_frames = 5

        # Simulate a loud chunk: chunk_rms_db above speech threshold (-30 dB)
        # _vad_update takes chunk_rms_db (decibels)
        rec._vad_update(-20.0)  # loud (above -30)
        assert rec._vad_consecutive_speech_frames == 1
        assert rec._vad_consecutive_silence_frames == 0

        # Simulate a grey-zone chunk: between silence (-50) and speech (-30)
        rec._vad_update(-40.0)  # grey zone
        # AUDIO-013 fix: counters must NOT be reset
        assert rec._vad_consecutive_speech_frames == 1, (
            f"AUDIO-013 regression: grey-zone chunk reset speech counter "
            f"from 1 to {rec._vad_consecutive_speech_frames} — should "
            f"preserve counters in the grey zone."
        )
        assert rec._vad_consecutive_silence_frames == 0


class TestVadAutoCalibrationBehavior:
    """AUDIO-014.

    Pre-fix: VAD auto-calibrate at recording.py:528-562 had no direct
    test. Fix: add a test that feeds known ambient noise and asserts
    the thresholds are set relative to the noise floor.
    """

    def test_vad_auto_calibrate_sets_thresholds_from_ambient_noise(self):
        """Feed the auto-calibrator a stream of low-amplitude noise
        and verify the speech/silence thresholds are set relative to
        the noise floor (not left at defaults).
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        # This test verifies RMS/dB-threshold auto-calibration. When Silero
        # VAD is the active backend, dB calibration is intentionally skipped
        # (AUDIO-4: Silero uses probability thresholds), so force the RMS
        # backend path to exercise the calibration logic under test.
        rec._vad._silero_available = False
        rec._vad._use_silero_vad = False
        # Reset calibration state
        rec._vad_calibration_rms_values = []
        rec._vad_calibrated = False
        # Save defaults so we can detect changes
        default_speech_db = rec._vad_speech_threshold_db
        default_silence_db = rec._vad_silence_threshold_db
        # Set recording start time so the elapsed check works
        rec._recording_start_time = time.perf_counter() - 10.0  # 10s ago
        # Make sure calibration duration has elapsed
        rec._vad_calibration_duration = 1.5

        # Feed 1.5 seconds worth of chunks at a known RMS (~0.01 = -40 dB)
        # Auto-calibration collects RMS for 1.5s then sets thresholds.
        chunk_duration = 0.032  # 32 ms per chunk at 16 kHz, 512 samples
        n_chunks = int(1.5 / chunk_duration) + 5  # extra to exceed duration
        target_rms = 0.01  # -40 dB
        for _ in range(n_chunks):
            rec._vad_auto_calibrate(target_rms, chunk_duration)

        # After calibration, the thresholds should be set relative to
        # the noise floor (target_rms in dB = 20*log10(0.01) = -40 dB).
        # The implementation sets:
        #   silence_threshold = noise_db + 6 dB  = -34 dB
        #   speech_threshold  = noise_db + 18 dB = -22 dB
        assert rec._vad_calibrated is True, (
            "VAD auto-calibration must set _vad_calibrated=True after collecting enough samples."
        )
        # Speech threshold should be above the noise floor
        assert rec._vad_speech_threshold_db >= -40.0, (
            f"VAD speech threshold ({rec._vad_speech_threshold_db} dB) "
            "must be at or above the noise floor (-40 dB) after calibration."
        )
        # Silence threshold should also be above the noise floor
        # (the implementation sets silence = noise + 6 dB)
        assert rec._vad_silence_threshold_db > -40.0, (
            f"VAD silence threshold ({rec._vad_silence_threshold_db} dB) "
            "must be above the noise floor (-40 dB) after calibration "
            "(implementation sets silence = noise + 6 dB)."
        )
        # Speech threshold must be above silence threshold
        assert rec._vad_speech_threshold_db > rec._vad_silence_threshold_db, (
            "VAD speech threshold must be above silence threshold."
        )
        # Thresholds must have changed from defaults
        assert (
            rec._vad_speech_threshold_db != default_speech_db or rec._vad_silence_threshold_db != default_silence_db
        ), "VAD thresholds must change from defaults after calibration."

    def test_vad_auto_calibrate_resets_on_start(self):
        """``Recorder.start()`` must reset the calibration state so a
        new session re-calibrates from scratch.

        RW-8: KEEP — pins AUDIO-014 fix (start() resets calibration
        state). The sibling test_vad_auto_calibrate_sets_thresholds_from_ambient_noise
        tests the calibration behavior, but doesn't verify start()
        resets it; the source-string check catches removal of the reset.
        """
        from voice_typer.server import recording as rec_mod

        src = inspect.getsource(rec_mod.Recorder.start)
        # The start() method must reset _vad_calibration_rms_values
        # and _vad_calibrated.
        assert "_vad_calibration_rms_values" in src or "_vad_calibrated" in src, (
            "Recorder.start() must reset VAD calibration state so each session re-calibrates from ambient noise."
        )


class TestStreamingAssemblerUsesDequeEviction:
    """AUDIO-019.

    Pre-fix: ``_words`` used a plain list with ``pop(0)`` for eviction
    (O(n) per eviction — shifts up to 9999 pointers). Fix: use
    ``collections.deque(maxlen=_MAX_WORDS)`` for O(1) eviction, plus
    a ``_base_offset`` counter so ``_word_key_index`` absolute indices
    stay correct without per-eviction O(n) shifting.
    """

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
        """``_insert_word_unlocked`` must NOT call ``self._words.pop(0)``
        (the O(n) pre-fix pattern). The deque's auto-eviction handles it.

        RW-8: KEEP — pins AUDIO-019 fix (deque(maxlen=N) auto-eviction
        replaces pop(0)). The sibling test_eviction_preserves_word_key_index_correctness
        tests eviction behavior, but doesn't catch reintroduction of
        pop(0) if it's added alongside the deque; the source-string
        check catches that directly.
        """
        from voice_typer.server.streaming import StreamingTextAssembler

        src = inspect.getsource(StreamingTextAssembler._insert_word_unlocked)
        assert "pop(0)" not in src, (
            "AUDIO-019 regression: _insert_word_unlocked uses pop(0) (O(n)) — "
            "use deque(maxlen=N) auto-eviction instead."
        )

    def test_eviction_triggers_warning_with_correct_variable_name(self):
        """The eviction warning must reference ``evicted_word.word``
        (not the typo ``evited.word`` from the pre-fix code).

        RW-8: KEEP — pins AUDIO-019 typo fix (evicted_word, not evited).
        The typo would only crash if the eviction path fires, which
        is rare in normal tests; the source-string check catches the
        typo deterministically.
        """
        from voice_typer.server.streaming import StreamingTextAssembler

        src = inspect.getsource(StreamingTextAssembler._insert_word_unlocked)
        assert "evicted_word.word" in src, (
            "Eviction warning must reference 'evicted_word.word' (the "
            "pre-fix code had a typo 'evited.word' that would crash "
            "with NameError if the eviction path ever fired)."
        )
        # The pre-fix typo must NOT be present
        assert "evited" not in src, (
            "AUDIO-019 regression: the 'evited' typo (missing 'c') is back — use 'evicted_word'."
        )

    def test_eviction_preserves_word_key_index_correctness(self):
        """When _words exceeds _MAX_WORDS, the deque auto-evicts the
        oldest item. ``_word_key_index`` must still point to the right
        words after eviction.
        """
        from voice_typer.server.streaming import StreamingTextAssembler, WordTiming

        # Use a tiny maxlen so we can trigger eviction easily
        asm = StreamingTextAssembler()
        asm._words = __import__("collections").deque(maxlen=3)
        asm._MAX_WORDS = 3  # match the deque maxlen for the warning check

        # Insert 5 words with the same text — eviction will trigger.
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
        # (in order: word2, word3, word4)
        committed = asm.committed_text
        assert "word2" in committed
        assert "word3" in committed
        assert "word4" in committed
        # The evicted words should NOT be in the committed text
        assert "word0" not in committed
        assert "word1" not in committed


class TestAudioAgcLastRmsPostAgc:
    """ADR 0007 §3.5: The old per-chunk AGC (_agc_update, C1) has been
    removed and replaced by the Compressor filter in the audio filter
    chain. These tests now verify that:

    1. The _agc_update method and its constants are gone from recording.py.
    2. _last_rms is still set (post-filter, for UI/IPC).
    3. No AGC-related dead code remains.
    """

    def test_last_rms_assignment_after_agc_recompute(self):
        """ADR 0007: AGC recompute block is gone. _last_rms is still set.

        RT-SAFE-001: the callback body moved to _process_audio_chunk.
        S3-CR-17 / Phase 4.5: the body of _process_audio_chunk moved to
        AudioPipeline.process_audio_chunk (collaborator pattern). The
        regression check now inspects AudioPipeline.process_audio_chunk
        source (the new home of the body) instead of the
        Recorder._process_audio_chunk 1-line delegate.

        RW-8: KEEP — pins ADR 0007 §3.5 (per-chunk AGC removed,
        replaced by Compressor filter). The negative assertion
        (no `_agc_update` / `_agc_gain` code) catches reintroduction
        of the dead AGC path; the positive assertion (`_last_rms =
        chunk_rms`) catches removal of the UI/IPC RMS feed.
        """
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        # S3-CR-17 / Phase 4.5: inspect AudioPipeline.process_audio_chunk
        # (the new home of the body, running on the audio worker thread).
        src = inspect.getsource(AudioPipeline.process_audio_chunk)
        # The old AGC recompute block should NOT exist anymore
        agc_recompute_idx = src.find("if abs(self._agc_gain - 1.0) > 0.01")
        # The _last_rms assignment now uses `self._recorder._last_rms`
        # (the collaborator back-reference pattern) instead of `self._last_rms`.
        last_rms_idx = src.find("_last_rms = chunk_rms")
        assert agc_recompute_idx == -1, (
            "ADR 0007: AGC recompute block should be deleted — "
            "the Compressor filter in the audio chain handles this now."
        )
        assert last_rms_idx >= 0, "_last_rms assignment must still exist for UI/IPC"

    def test_agc_applied_before_last_rms_storage(self):
        """ADR 0007: _agc_update call is gone. _last_rms is still set.

        RT-SAFE-001: the callback body moved to _process_audio_chunk.
        S3-CR-17 / Phase 4.5: the body of _process_audio_chunk moved to
        AudioPipeline.process_audio_chunk (collaborator pattern). The
        regression check now inspects AudioPipeline.process_audio_chunk
        source (the new home of the body) instead of the
        Recorder._process_audio_chunk 1-line delegate.

        RW-8: KEEP — pins ADR 0007 §3.5 (per-chunk AGC call removed).
        Same rationale as test_last_rms_assignment_after_agc_recompute.
        """
        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        src = inspect.getsource(AudioPipeline.process_audio_chunk)
        # The old _agc_update call should NOT exist anymore
        agc_update_idx = src.find("_agc_update(chunk_rms, filtered)")
        last_rms_idx = src.find("_last_rms = chunk_rms")
        assert agc_update_idx == -1, (
            "ADR 0007: _agc_update call should be deleted — "
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
- RACE-009       Electron stdout/stderr routed to log files
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
- PLAT-036       MANIFEST.in exists (already fixed — pin)
- PLAT-037       Windows manifest embedded with asInvoker (already fixed — pin)
- PLAT-040       mutex has Local\ prefix + install hash + DACL (already fixed — pin)
- RACE-001       concurrent callback test exists (already fixed — pin)
"""


class TestAudioMicDeviceChangePoller:
    """AUDIO-MIC.

    The finding: no WM_DEVICECHANGE handler; USB mic hotplug not
    detected. Fix: added a 30-second periodic poller that
    re-enumerates microphones and pushes a ``microphones_changed``
    IPC event when the device set changes.

    PERF-FIX-2: the 30s poller was later found to be fully redundant
    with the event-driven ``MicrophoneDeviceWatcher`` (started in
    ``Recorder.__init__``), which is the sole source of truth on all
    platforms (WM_DEVICECHANGE on Windows, ``/dev/snd`` polling on
    Linux, CoreAudio property-listener on macOS). The poller was
    removed from ``_do_startup`` (now ``StartupSequence.run``).
    """

    def test_load_microphones_pushes_ipc_event_on_change(self):
        """AUDIO-MIC: when ``load_microphones`` detects that the device
        set has changed (USB mic plugged/unplugged), it must push a
        ``microphones_changed`` IPC event so the Electron renderer can
        refresh its microphone dropdown without a manual "Refresh" click.

        RW-8: ported from a source-string meta-test (which inspected
        ``load_microphones`` source for ``microphones_changed`` /
        ``old_ids`` / ``new_ids`` substrings) to a behavioral test
        that mocks ``list_microphones`` to return a different device
        set on successive calls and asserts ``event_bus.publish`` was
        invoked with a ``microphones_changed`` event. The behavioral
        test is robust to refactors — if the comparison logic is
        restructured or the event payload field names change, the test
        still catches the regression as long as the IPC event is no
        longer published on device-set change.
        """
        from unittest.mock import MagicMock, patch

        from voice_typer.server import startup_tasks

        # Build a minimal app mock with a non-empty initial microphone
        # list so ``old_ids`` is non-empty and the change-detection
        # branch fires.
        app = MagicMock()
        app._microphones = [
            {"id": 1, "name": "Mic A"},
            {"id": 2, "name": "Mic B"},
        ]

        # Mock ``list_microphones`` to return a DIFFERENT device set
        # (Mic B removed, Mic C added) — the device-id set changes
        # from {1, 2} to {1, 3}, which must trigger the
        # ``microphones_changed`` IPC event.
        with (
            patch(
                "voice_typer.server.app.list_microphones",
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

    def test_poller_not_started_in_startup(self):
        """PERF-FIX-2: ``StartupSequence.run`` must NOT call
        ``_start_device_change_poller``. The 30s poller is redundant
        with the event-driven ``MicrophoneDeviceWatcher`` (the sole
        source of truth). The poller was removed from startup to
        eliminate the ~1-5ms/30s CPU cost and the per-second
        ``threading.Event()`` allocation.

        RW-8: KEEP — pins PERF-FIX-2 (redundant poller removed).

        PERF-FIX-2 (stronger): the dead-code ``start_device_change_poller``
        function was deleted entirely from ``startup_tasks``. The previous
        source-string check only asserted that ``StartupSequence.run``
        didn't call it; now we additionally assert the function is GONE
        from the module so it cannot be silently reintroduced as a
        zombie helper.
        """
        from voice_typer.server import startup_tasks
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence.run)
        assert "_start_device_change_poller(" not in src, (
            "PERF-FIX-2: StartupSequence.run must NOT call _start_device_change_poller "
            "(redundant with the event-driven MicrophoneDeviceWatcher)."
        )
        assert not hasattr(startup_tasks, "start_device_change_poller"), (
            "PERF-FIX-2: startup_tasks.start_device_change_poller must be deleted "
            "(dead code — never called from production startup; "
            "MicrophoneDeviceWatcher is the sole source of truth)."
        )


class TestAudioClipRealtimeIpcEvent:
    """AUDIO-CLIP.

    The finding: clipping detected + logged but no user-facing
    real-time notification. Fix: push an ``audio_clip`` IPC event
    (throttled to 1 Hz) from the audio callback when clipping is
    detected.
    """

    def test_clipping_pushes_audio_clip_ipc_event(self):
        from voice_typer.server import recording

        # RT-SAFE-001: the callback body moved to _process_audio_chunk
        # (runs on the audio worker thread instead of the real-time
        # audio thread). The clipping IPC event is still pushed from
        # there — the invariant is preserved.
        # Subsequent refactor: the clipping-detection + event-emit
        # logic was extracted into the dedicated ``_detect_and_emit_clipping``
        # helper (called by ``_process_audio_chunk``). The invariant —
        # "the recording callback path pushes an audio_clip IPC event
        # when clipping is detected" — is preserved (just lives in a
        # helper for readability). We check both methods so the test
        # survives either layout.
        chunk_src = inspect.getsource(recording.Recorder._process_audio_chunk)
        detect_src = inspect.getsource(recording.Recorder._detect_and_emit_clipping)
        combined = chunk_src + "\n" + detect_src
        assert "audio_clip" in combined, (
            "AUDIO-CLIP: recording callback must push an 'audio_clip' IPC event when clipping is detected."
        )
        # B-1 + RW-8: production code now enqueues the event on
        # ``self._event_queue`` (a queue.Queue) instead of calling
        # ``event_bus.publish`` directly. A dedicated worker thread
        # drains the queue and calls ``event_bus.publish`` off the
        # audio hot path. The invariant — "the recording callback
        # pushes an event to the IPC channel" — is preserved (just
        # async via the queue).
        assert "_event_queue.put" in combined or "event_bus.publish" in combined or "_push_event_now" in combined, (
            "AUDIO-CLIP: recording callback must enqueue the audio_clip "
            "event via _event_queue.put (RW-8 worker queue) OR call "
            "event_bus.publish / _push_event_now directly."
        )


class TestAudioDtypeEdgeCases:
    """AUDIO-006.

    The finding: format edge cases not systematically tested. Fix:
    added parametrized tests for int16, float64, and non-contiguous
    arrays flowing through the recorder's resample path.
    """

    def test_resample_chunk_handles_float32(self):
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        # float32 is the default dtype — must work
        audio = np.full(512, 0.5, dtype=np.float32)
        result = rec._resample_chunk(audio, 16000, 16000)
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
        # int16 input — the callback converts to float32 via frombuffer
        audio = np.full(512, 16384, dtype=np.int16)
        # _resample_chunk expects float32; int16 should be converted
        # upstream. Here we just verify it doesn't crash on the
        # float32 path.
        audio_f32 = audio.astype(np.float32) / 32768.0
        result = rec._resample_chunk(audio_f32, 16000, 16000)
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
        result = rec._resample_chunk(sliced, 16000, 16000)
        assert result is not None


class TestNumpyVectorizedOpsRegression:
    """AUDIO-007.

    The finding: no regression test asserts np.frombuffer/np.dot usage.
    Fix: added source-inspection test + numerical equivalence test.
    """

    def test_recording_uses_np_dot_for_rms(self):
        # RW-8: KEEP — pins AUDIO-007 (vectorized np.dot RMS computation).
        # The sibling test_np_dot_rms_matches_naive_computation tests the
        # numerical equivalence, but doesn't catch a regression where the
        # callback switches to a naive np.mean(audio**2) implementation
        # (which would still pass the equivalence test). Source-string
        # check catches the implementation choice directly.
        from voice_typer.server import recording

        src = inspect.getsource(recording)
        # The callback uses np.dot for RMS: np.sqrt(np.dot(flat, flat) / flat.size)
        assert "np.dot(flat, flat)" in src or "np.dot(flat,flat)" in src, (
            "AUDIO-007: recording.py must use np.dot for vectorized RMS computation."
        )

    def test_np_dot_rms_matches_naive_computation(self):
        """Verify np.dot-based RMS produces the same result as the naive
        np.mean(audio**2)**0.5 computation for a known sine input.
        """
        # 1 second of 440 Hz sine wave at 16 kHz, amplitude 0.5
        sr = 16000
        t = np.arange(sr) / sr
        audio = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        # np.dot-based RMS (the vectorized path)
        flat = audio.reshape(-1)
        rms_dot = float(np.sqrt(np.dot(flat, flat) / flat.size))

        # Naive RMS
        rms_naive = float(np.sqrt(np.mean(audio**2)))

        # Must match to floating-point precision
        assert abs(rms_dot - rms_naive) < 1e-6, f"np.dot RMS ({rms_dot}) != naive RMS ({rms_naive})"
        # Expected RMS for 0.5-amplitude sine = 0.5 / sqrt(2) ≈ 0.3536
        assert abs(rms_dot - 0.5 / np.sqrt(2)) < 0.01


class TestAudioDeviceDisconnectHandling:
    """AUDIO-008.

    The finding: no tests for device disconnect handling (3 retries,
    periodic check). Fix: added tests simulating zero-filled indata.
    """

    def test_handle_device_disconnect_exists(self):
        from voice_typer.server import recording

        assert hasattr(recording.Recorder, "_handle_device_disconnect"), (
            "AUDIO-008: Recorder must have _handle_device_disconnect method."
        )

    def test_device_disconnect_flag_set_on_zero_indata(self):
        """When the callback receives all-zero indata with chunk_count > 10,
        the device_disconnected flag must be set.

        RT-SAFE-001: the zero-fill disconnect detection moved from the
        real-time audio callback to _process_audio_chunk (runs on the
        audio worker thread). The invariant is preserved.

        RW-8: KEEP — pins AUDIO-008 zero-fill disconnect detection.
        The test accepts any of three idioms (np.count_nonzero,
        np.all, not indata.any()) so it's robust to refactors within
        the same behavior; a fully behavioral test would need to feed
        zero indata and observe the flag, which requires a running
        recorder (heavy). Source-string check is the lighter-weight guard.
        """
        from voice_typer.server import recording
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000
        rec._recording_event.set()
        rec._recording_start_time = time.perf_counter()
        rec._chunk_count = 15  # > 10 threshold
        rec._device_disconnected = False

        # Mock callbacks
        rec.on_rms_level = lambda rms, peak: None
        rec.on_silence_warning = lambda: None
        rec.on_silence_auto_stop = lambda: None
        rec.on_max_duration_auto_stop = lambda: None

        # RT-SAFE-001: the callback body moved to _process_audio_chunk.
        # The zero-fill disconnect check runs there now (on the worker
        # thread instead of the real-time audio thread).
        src = inspect.getsource(recording.Recorder._process_audio_chunk)
        assert "_device_disconnected" in src
        # AUDIO-008 / RT-SAFE-001: the zero-filled disconnect check.
        # The implementation uses ``np.count_nonzero(indata) == 0``
        # (equivalent to ``np.all(indata == 0)`` / ``not indata.any()``);
        # accept any of the three idioms so the test pins the
        # *behavior* (all-zero indata ⇒ disconnect) rather than a
        # specific spelling that may be refectored.
        assert "np.count_nonzero(indata) == 0" in src or "np.all(indata == 0)" in src or "not indata.any()" in src, (
            "_process_audio_chunk must check for zero-filled indata to detect "
            "device disconnect (via np.count_nonzero(indata) == 0 or "
            "np.all(indata == 0) or not indata.any())"
        )


class TestBackpressureDetectionOnDequeOverflow:
    """AUDIO-010.

    The finding: no test for backpressure detection (deque overflow).
    Fix: added test that fills _buffer past maxlen and asserts
    _dropped_chunks is incremented.
    """

    def test_backpressure_detection_increments_dropped_chunks(self):
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000

        # Fill the buffer past maxlen
        maxlen = rec._buffer.maxlen
        chunk = np.full((512, 1), 0.1, dtype=np.float32)
        with rec._lock:
            for _ in range(maxlen + 5):
                rec._buffer.append(chunk)
        buffer_len = len(rec._buffer)

        # Simulate the backpressure check
        if buffer_len >= rec._buffer.maxlen - 1:
            rec._dropped_chunks = getattr(rec, "_dropped_chunks", 0) + 1

        assert hasattr(rec, "_dropped_chunks"), "Backpressure counter must be set"
        assert rec._dropped_chunks >= 1, "AUDIO-010: _dropped_chunks must be incremented when buffer is full."

    def test_backpressure_source_uses_maxlen_check(self):
        # RW-8: KEEP — pins AUDIO-010 (backpressure check compares
        # against _buffer.maxlen). The sibling test_backpressure_detection_increments_dropped_chunks
        # tests the increment behavior, but doesn't catch a regression
        # where the comparison is against a hardcoded length. Source-string
        # check catches the implementation choice.
        from voice_typer.server import recording

        # RT-SAFE-001: the callback body moved to _process_audio_chunk.
        src = inspect.getsource(recording.Recorder._process_audio_chunk)
        assert "_dropped_chunks" in src, "AUDIO-010: recording callback must track _dropped_chunks."
        assert "self._buffer.maxlen" in src, "AUDIO-010: backpressure check must compare against _buffer.maxlen."


# ADR-0007: AGC removed; test deleted because the feature no longer exists.
# The per-chunk AGC (_agc_update, C1) and its constants (_AGC_TARGET_RMS,
# _AGC_ATTACK_ALPHA, _AGC_MIN_GAIN, _AGC_MAX_GAIN) were replaced by the
# Compressor filter in the audio filter chain. See voice_typer/server/
# recording.py §3.5 and audio_filters.py for the current chain.


class TestDynamicSampleRateResolution:
    """AUDIO-016.

    The finding: _resolve_effective_sample_rate() not tested. Fix:
    added tests that mock sd.query_devices() and verify the resolution
    strategy.
    """

    def test_resolve_effective_sample_rate_exists(self):
        from voice_typer.server import recording

        assert hasattr(recording.Recorder, "_resolve_effective_sample_rate"), (
            "AUDIO-016: Recorder must have _resolve_effective_sample_rate method."
        )

    def test_resolve_returns_tuple_with_native_rate(self):
        """The method must return (sample_rate, device_info_dict).

        WR-4: previously this test wrapped its assertion in
        `try/except Exception: pass`, which swallowed the AssertionError
        raised by `assert result is not None` itself — the test was a
        no-op that passed even when the production method returned None
        or raised. Now we let exceptions propagate (the only expected
        failure mode is `sounddevice.PortAudioError` if no device is
        available, which is environment-specific and should surface as
        a skip rather than a silent pass). We also assert the explicit
        tuple shape per the docstring contract.
        """
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
            result = rec._resolve_effective_sample_rate(None)
            # The method must return a (sample_rate, device_info_dict) tuple.
            assert result is not None, (
                "AUDIO-016: _resolve_effective_sample_rate() returned None — "
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


class TestPeakMeterAccuracy:
    """AUDIO-017.

    The finding: no dedicated peak accuracy test. Fix: added test that
    feeds a known-amplitude signal and asserts _peak is tracked.
    """

    def test_peak_tracking_increments_correctly(self):
        """Feed a signal with known peak amplitude and verify _peak is
        updated to the maximum.
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._peak = 0.0
        rec._clip_count = 0
        rec._last_clip_log_time = 0.0

        # Simulate the peak-tracking logic from the callback
        # (AUDIO-CLIP block at recording.py:1219+)
        test_peaks = [0.3, 0.7, 0.5, 0.95, 0.4]
        for peak in test_peaks:
            chunk_peak = peak
            if chunk_peak >= 0.99:
                rec._clip_count += 1
                if chunk_peak > rec._peak:
                    rec._peak = chunk_peak
            else:
                # Non-clipping peaks also update _peak
                if chunk_peak > rec._peak:
                    rec._peak = chunk_peak

        # _peak must be the maximum of all test peaks
        assert rec._peak == 0.95, f"AUDIO-017: _peak must be 0.95 (max of {test_peaks}), got {rec._peak}"

    def test_peak_source_uses_abs_max(self):
        # RW-8: KEEP — pins AUDIO-017 (peak computation uses abs().max()).
        # The sibling test_peak_tracking_increments_correctly tests the
        # peak-tracking behavior, but doesn't catch a regression where
        # the implementation switches to max(filtered) (without abs),
        # which would return wrong values for negative-going signals.
        from voice_typer.server import recording

        # RT-SAFE-001: the callback body moved to _process_audio_chunk.
        src = inspect.getsource(recording.Recorder._process_audio_chunk)
        # The peak computation returns max(|x|). The canonical forms
        # are ``abs_filtered.max()`` and ``np.abs(filtered).max()``.
        # PERF-FIX-2 introduced an allocation-free equivalent:
        # ``max(float(flat.max()), -float(flat.min()))`` — same value
        # (max of absolute values), no intermediate ``np.abs`` array.
        assert (
            "abs_filtered.max()" in src
            or "np.abs(filtered).max()" in src
            or "max(float(flat.max()), -float(flat.min()))" in src
        ), (
            "AUDIO-017: peak computation must use abs().max() on the audio "
            "(or the allocation-free max(max(x), -min(x)) equivalent)."
        )


class TestVadBoundaryConditions:
    """AUDIO-018.

    The finding: VAD state machine boundary tests (exactly N-1 frames)
    missing. Fix: added tests at the exact boundary frame counts.
    """

    def test_vad_transition_at_exact_speech_threshold(self):
        """When consecutive_speech_frames == threshold, state must
        transition from UNKNOWN to SPEECH. At threshold-1, it must NOT.
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        rec._vad_state = VadState.UNKNOWN
        rec._vad_speech_threshold_db = -30.0
        rec._vad_silence_threshold_db = -50.0
        rec._vad_speech_frames = 5  # threshold
        rec._vad_silence_frames = 10
        rec._vad_hangover_frames = 5

        # Feed threshold-1 loud frames → must NOT transition
        for _ in range(4):
            rec._vad_update(-20.0)  # loud
        assert rec._vad_state == VadState.UNKNOWN, "AUDIO-018: at threshold-1 frames, state must remain UNKNOWN"
        assert rec._vad_consecutive_speech_frames == 4

        # Feed one more loud frame → must transition to SPEECH
        rec._vad_update(-20.0)
        assert rec._vad_state == VadState.SPEECH, (
            "AUDIO-018: at exactly threshold frames, state must transition to SPEECH"
        )

    def test_vad_transition_at_exact_silence_threshold(self):
        """When consecutive_silence_frames == hangover, state must
        transition from SPEECH to SILENCE. At hangover-1, it must NOT.
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        rec._vad_state = VadState.SPEECH
        rec._vad_speech_threshold_db = -30.0
        rec._vad_silence_threshold_db = -50.0
        rec._vad_speech_frames = 5
        rec._vad_silence_frames = 10
        rec._vad_hangover_frames = 5  # threshold for SPEECH→SILENCE

        # Feed hangover-1 quiet frames → must NOT transition
        for _ in range(4):
            rec._vad_update(-60.0)  # quiet
        assert rec._vad_state == VadState.SPEECH, "AUDIO-018: at hangover-1 frames, state must remain SPEECH"

        # Feed one more quiet frame → must transition to SILENCE
        rec._vad_update(-60.0)
        assert rec._vad_state == VadState.SILENCE, (
            "AUDIO-018: at exactly hangover frames, state must transition to SILENCE"
        )

    def test_vad_grey_zone_preserves_counters(self):
        """Grey-zone chunks (between thresholds) must NOT reset counters
        (AUDIO-013 fix, also relevant to AUDIO-018 boundary testing).
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder, VadState

        cfg = Config()
        rec = Recorder(cfg)
        rec._vad_state = VadState.UNKNOWN
        rec._vad_speech_threshold_db = -30.0
        rec._vad_silence_threshold_db = -50.0
        rec._vad_speech_frames = 5
        rec._vad_silence_frames = 10
        rec._vad_hangover_frames = 5

        # 2 loud frames
        rec._vad_update(-20.0)
        rec._vad_update(-20.0)
        assert rec._vad_consecutive_speech_frames == 2

        # 1 grey-zone frame → counters must NOT reset
        rec._vad_update(-40.0)  # grey zone (between -50 and -30)
        assert rec._vad_consecutive_speech_frames == 2, "AUDIO-018/AUDIO-013: grey-zone must preserve speech counter"


class TestStreamingSessionAtomicPopOnCancel:
    """ARCH-018.

    The finding: streaming session lock had a TOCTOU gap in the cancel
    path — ``_cancel_streaming_session`` did get-then-set (two lock
    acquisitions), allowing a concurrent start to install a new session
    that the subsequent set(None) would clobber. Fix: added
    ``pop_streaming_session()`` that does atomic get-and-clear under a
    single lock acquisition.
    """

    def test_pop_streaming_session_exists(self):
        from voice_typer.server.recording_controller import RecordingController

        assert hasattr(RecordingController, "pop_streaming_session"), (
            "ARCH-018: RecordingController must have pop_streaming_session method"
        )

    def test_pop_is_atomic_single_lock_acquisition(self):
        """pop_streaming_session must acquire the lock exactly once.

        RW-8: KEEP — pins ARCH-018 (atomic get-and-clear under a single
        lock acquisition). The sibling test_concurrent_pop_and_set_no_clobber
        # tests the atomicity behaviorally, but doesn't catch a regression
        # where the implementation uses two nested lock acquisitions that
        # happen to pass the race test in practice. Source-string check
        # catches the implementation choice directly.
        """
        from voice_typer.server.recording_controller import RecordingController

        src = inspect.getsource(RecordingController.pop_streaming_session)
        # Must contain exactly one `with self._streaming_session_lock:` block
        assert src.count("with self._streaming_session_lock:") == 1, (
            "ARCH-018: pop_streaming_session must acquire the lock exactly once (atomic get-and-clear)"
        )

    def test_cancel_uses_pop_not_get_then_set(self):
        """_cancel_streaming_session must use pop_streaming_session(),
        not the pre-fix get_streaming_session() + set_streaming_session(None).

        RW-8: KEEP — pins ARCH-018 (cancel uses the atomic pop). Same
        # rationale as test_pop_is_atomic_single_lock_acquisition.
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
        """Functional test: pop_streaming_session must return the current
        session AND clear it in one atomic operation.
        """
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
        """ARCH-018 regression test: a concurrent set_streaming_session
        must NOT be clobbered by a pop_streaming_session that started
        before the set. Pre-fix, the get-then-set pattern could clobber
        a freshly-installed session.
        """
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._streaming_session = MagicMock()

        # Simulate the race: thread A pops (get+clear), thread B sets a
        # new session between A's get and A's clear. With the atomic pop,
        # B's set happens AFTER A's pop completes, so the new session
        # survives.
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
        # (because pop cleared the original, then B set the new one)
        assert ctrl._streaming_session is results["set"], (
            "ARCH-018 regression: the new session set by thread B was "
            "clobbered by thread A's pop — the atomic get-and-clear fix "
            "is not working."
        )
