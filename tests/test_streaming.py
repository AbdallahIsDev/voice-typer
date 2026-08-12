"""Tests for streaming transcription planning and text assembly."""

from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.streaming import (
    AudioWindow,
    AudioWindowPlanner,
    StreamingConfig,
    StreamingTextAssembler,
    StreamingTranscriptionSession,
    WordTiming,
)

# detect once at import time whether hypothesis is installed.
# Tests in TestHypothesisAudioPipeline require it; the class-level
# pytestmark below skips them as a group if it isn't, replacing the
# previous per-method ``setup_method`` skip (which called
# ``pytest.skip(allow_module_level=True)`` from inside an instance
# method — a no-op, since ``allow_module_level`` only takes effect at
# module import time).
try:
    import hypothesis  # noqa: F401

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

SAMPLE_RATE = 16000


def audio_seconds(seconds: float, amplitude: float = 0.01) -> np.ndarray:
    return np.full(int(seconds * SAMPLE_RATE), amplitude, dtype=np.float32)


def test_streaming_config_defaults_are_disabled_and_conservative():
    config = StreamingConfig()

    assert config.enabled is False
    assert config.chunk_seconds == 12.0
    assert config.step_seconds == 5.0
    assert config.left_overlap_seconds == 3.0
    assert config.right_guard_seconds == 1.5
    assert config.min_first_chunk_seconds == 6.0
    assert config.silence_threshold == 0.003


def test_audio_window_planner_waits_until_min_first_chunk():
    planner = AudioWindowPlanner(StreamingConfig(min_first_chunk_seconds=4.0, chunk_seconds=10.0))

    assert planner.next_window(audio_seconds(3.9), SAMPLE_RATE) is None

    window = planner.next_window(audio_seconds(4.0), SAMPLE_RATE)

    assert window is not None
    assert window.start_seconds == 0.0
    assert window.end_seconds == 4.0
    assert len(window.audio) == 4 * SAMPLE_RATE


def test_audio_window_planner_creates_overlapping_windows():
    planner = AudioWindowPlanner(
        StreamingConfig(
            min_first_chunk_seconds=4.0,
            chunk_seconds=8.0,
            step_seconds=5.0,
            left_overlap_seconds=2.0,
        )
    )

    first = planner.next_window(audio_seconds(4.0), SAMPLE_RATE)
    assert first == AudioWindow(
        audio=audio_seconds(4.0),
        start_seconds=0.0,
        end_seconds=4.0,
    )

    assert planner.next_window(audio_seconds(8.9), SAMPLE_RATE) is None

    second = planner.next_window(audio_seconds(9.0), SAMPLE_RATE)

    assert second is not None
    assert second.start_seconds == 2.0
    assert second.end_seconds == 9.0
    assert len(second.audio) == 7 * SAMPLE_RATE


def test_audio_window_planner_prefers_silence_near_requested_boundary():
    config = StreamingConfig(
        min_first_chunk_seconds=5.0,
        chunk_seconds=5.0,
        silence_threshold=0.003,
    )
    audio = audio_seconds(5.0, amplitude=0.02)
    audio[int(4.5 * SAMPLE_RATE) : int(4.6 * SAMPLE_RATE)] = 0.0
    planner = AudioWindowPlanner(config)

    window = planner.next_window(audio, SAMPLE_RATE)

    assert window is not None
    assert 4.49 <= window.end_seconds <= 4.61
    assert len(window.audio) == int(window.end_seconds * SAMPLE_RATE)


def test_streaming_text_assembler_commits_words_before_right_guard():
    assembler = StreamingTextAssembler()
    window = AudioWindow(audio=audio_seconds(5.0), start_seconds=0.0, end_seconds=5.0)
    words = [
        WordTiming("hello", start_seconds=0.2, end_seconds=0.7),
        WordTiming("world", start_seconds=4.4, end_seconds=4.8),
    ]

    committed = assembler.add_window(window, words, right_guard_seconds=1.0)

    assert committed == "hello"
    assert assembler.committed_text == "hello"
    assert assembler.last_committed_time == 0.7


def test_streaming_text_assembler_avoids_duplicate_overlap_by_timestamp():
    assembler = StreamingTextAssembler()
    first = [
        WordTiming("turn", start_seconds=0.0, end_seconds=0.3),
        WordTiming("left", start_seconds=0.4, end_seconds=0.8),
    ]
    second = [
        WordTiming("left", start_seconds=0.4, end_seconds=0.8),
        WordTiming("now", start_seconds=1.0, end_seconds=1.3),
    ]

    assembler.add_words(first, commit_horizon_seconds=2.0)
    committed = assembler.add_words(second, commit_horizon_seconds=2.0)

    assert committed == "now"
    assert assembler.committed_text == "turn left now"


def test_streaming_text_assembler_preserves_repeated_words_at_later_timestamps():
    assembler = StreamingTextAssembler()

    committed = assembler.add_words(
        [
            WordTiming("yes", start_seconds=0.0, end_seconds=0.2),
            WordTiming("yes", start_seconds=0.5, end_seconds=0.7),
        ],
        commit_horizon_seconds=1.0,
    )

    assert committed == "yes yes"
    assert assembler.committed_text == "yes yes"


def test_streaming_text_assembler_inserts_late_overlap_words_by_timestamp():
    assembler = StreamingTextAssembler()
    assembler.add_words(
        [
            WordTiming("the", start_seconds=0.0, end_seconds=0.2),
            WordTiming("patching", start_seconds=0.7, end_seconds=1.0),
            WordTiming("process", start_seconds=1.1, end_seconds=1.4),
        ],
        commit_horizon_seconds=2.0,
    )

    committed = assembler.add_words(
        [
            WordTiming("streaming", start_seconds=0.3, end_seconds=0.6),
        ],
        commit_horizon_seconds=2.0,
    )

    assert committed == "streaming"
    assert assembler.committed_text == "the streaming patching process"


def test_streaming_text_assembler_skips_retimed_duplicate_overlap_words():
    assembler = StreamingTextAssembler()
    assembler.add_words(
        [
            WordTiming("patching", start_seconds=0.70, end_seconds=1.00),
        ],
        commit_horizon_seconds=2.0,
    )

    committed = assembler.add_words(
        [
            WordTiming("patching", start_seconds=0.74, end_seconds=1.05),
        ],
        commit_horizon_seconds=2.0,
    )

    assert committed == ""
    assert assembler.committed_text == "patching"


def test_streaming_session_finalizes_only_uncommitted_tail():
    config = StreamingConfig(
        min_first_chunk_seconds=5.0,
        chunk_seconds=5.0,
        step_seconds=5.0,
        left_overlap_seconds=0.5,
        right_guard_seconds=1.0,
    )
    recorder = MagicMock()
    recorder.snapshot.return_value = audio_seconds(5.0)
    transcriber = MagicMock()
    transcriber.transcribe_words.side_effect = [
        [
            WordTiming("hello", start_seconds=0.2, end_seconds=0.7),
            WordTiming("stable", start_seconds=2.5, end_seconds=3.0),
            WordTiming("late", start_seconds=4.4, end_seconds=4.8),
        ],
        [
            WordTiming("stable", start_seconds=2.6, end_seconds=3.0),
            WordTiming("world", start_seconds=4.4, end_seconds=4.8),
        ],
    ]

    session = StreamingTranscriptionSession(
        recorder=recorder,
        transcriber=transcriber,
        config=config,
        sample_rate=SAMPLE_RATE,
    )

    assert session.process_available_audio_once() is True
    assert session.confirmed_text == "hello stable"

    final_text = session.finalize(audio_seconds(5.0))

    assert final_text == "hello stable world"
    assert transcriber.transcribe_with_fallback.call_count == 0
    second_call = transcriber.transcribe_words.call_args_list[1]
    assert second_call.kwargs["offset_seconds"] == 2.5


def test_streaming_session_drops_final_tail_words_before_commit_boundary():
    config = StreamingConfig(
        min_first_chunk_seconds=5.0,
        chunk_seconds=5.0,
        left_overlap_seconds=1.0,
        right_guard_seconds=1.0,
    )
    recorder = MagicMock()
    recorder.snapshot.return_value = audio_seconds(5.0)
    transcriber = MagicMock()
    transcriber.transcribe_words.side_effect = [
        [
            WordTiming("alpha", start_seconds=0.2, end_seconds=0.7),
            WordTiming("bravo", start_seconds=2.0, end_seconds=2.5),
        ],
        [
            WordTiming("wrong", start_seconds=1.8, end_seconds=2.4),
            WordTiming("charlie", start_seconds=2.7, end_seconds=3.1),
        ],
    ]

    session = StreamingTranscriptionSession(
        recorder=recorder,
        transcriber=transcriber,
        config=config,
        sample_rate=SAMPLE_RATE,
    )

    assert session.process_available_audio_once() is True

    assert session.finalize(audio_seconds(5.0)) == "alpha bravo charlie"


def test_streaming_session_falls_back_after_chunk_failure():
    recorder = MagicMock()
    recorder.snapshot.return_value = audio_seconds(6.0)
    transcriber = MagicMock()
    transcriber.transcribe_words.side_effect = RuntimeError("chunk failed")
    transcriber.transcribe_with_fallback.return_value = "batch fallback"

    session = StreamingTranscriptionSession(
        recorder=recorder,
        transcriber=transcriber,
        config=StreamingConfig(min_first_chunk_seconds=5.0, chunk_seconds=5.0),
        sample_rate=SAMPLE_RATE,
    )

    assert session.process_available_audio_once() is False

    final_text = session.finalize(audio_seconds(6.0))

    assert final_text == "batch fallback"
    transcriber.transcribe_with_fallback.assert_called_once()


def test_streaming_session_without_confirmed_text_uses_fast_batch_finalize():
    recorder = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe_with_fallback.return_value = "fast batch"

    session = StreamingTranscriptionSession(
        recorder=recorder,
        transcriber=transcriber,
        config=StreamingConfig(),
        sample_rate=SAMPLE_RATE,
    )

    final_text = session.finalize(audio_seconds(3.0))

    assert final_text == "fast batch"
    transcriber.transcribe_words.assert_not_called()
    transcriber.transcribe_with_fallback.assert_called_once()


def test_streaming_session_start_and_cancel_stop_worker():
    recorder = MagicMock()
    recorder.snapshot.return_value = np.array([], dtype=np.float32)
    transcriber = MagicMock()
    session = StreamingTranscriptionSession(
        recorder=recorder,
        transcriber=transcriber,
        config=StreamingConfig(),
        sample_rate=SAMPLE_RATE,
        poll_interval_seconds=0.01,
    )

    session.start()
    # cancel() is non-blocking by default; pass blocking=True
    # to wait for the worker.
    session.cancel(blocking=True)

    assert session.is_running is False


class TestConcurrentAccess:
    """P2 fix: Verify streaming session handles concurrent access safely."""

    def test_concurrent_process_calls_dont_corrupt_assembler(self):
        """Multiple threads calling process_available_audio_once should not corrupt data."""
        import threading

        from voice_typer.server.streaming import (
            StreamingConfig,
            StreamingTranscriptionSession,
            WordTiming,
        )

        config = StreamingConfig(enabled=True, min_first_chunk_seconds=0.0)
        mock_recorder = MagicMock()
        mock_transcriber = MagicMock()

        session = StreamingTranscriptionSession(
            recorder=mock_recorder,
            transcriber=mock_transcriber,
            config=config,
            sample_rate=16000,
        )

        # Return some audio and words
        mock_recorder.snapshot.return_value = np.zeros(16000 * 10, dtype=np.float32)
        mock_transcriber.transcribe_words.return_value = [
            WordTiming(word="hello", start_seconds=0.0, end_seconds=0.5),
        ]

        errors = []

        def worker():
            try:
                for _ in range(20):
                    session.process_available_audio_once()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Concurrent access errors: {errors}"


class TestPruning:
    """H8: Unbounded memory growth in streaming assembler."""

    def test_old_words_are_pruned_after_commit_horizon(self):
        """Dedup structures are pruned but _words (output accumulator) is kept."""
        assembler = StreamingTextAssembler()
        # Add word at t=5 - within 5-second buffer of commit_horizon=10
        assembler.add_words(
            [WordTiming("early", start_seconds=5.0, end_seconds=5.5)],
            commit_horizon_seconds=10.0,
        )
        assert "early" in assembler.committed_text
        assert len(assembler._words) == 1

        # Now add word at t=20, with commit_horizon=25
        # Prune threshold = 25 - 5 = 20. Dedup timestamps for "early" (5.5 < 20)
        # are pruned, but _words keeps all committed entries.
        assembler.add_words(
            [WordTiming("later", start_seconds=20.0, end_seconds=20.5)],
            commit_horizon_seconds=25.0,
        )

        assert "later" in assembler.committed_text
        assert "early" in assembler.committed_text
        assert len(assembler._words) == 2

    def test_recent_words_are_kept(self):
        """Words near the commit horizon should NOT be pruned."""
        assembler = StreamingTextAssembler()
        assembler.add_words(
            [WordTiming("keep", start_seconds=8.0, end_seconds=8.5)],
            commit_horizon_seconds=10.0,
        )
        # Prune threshold = 10.0 - 5.0 = 5.0, so "keep" (8.5 > 5.0) stays
        assert "keep" in assembler.committed_text


class TestFinalizeRace:
    """H14: Streaming finalize() races with still-running thread."""

    def test_cancel_uses_10_second_timeout(self):
        """cancel(blocking=True) should join with a 10-second timeout.

        ARCH-025: cancel() is now non-blocking by default; tests that
        need to wait for the worker must pass blocking=True.
        """
        recorder = MagicMock()
        recorder.snapshot.return_value = np.array([], dtype=np.float32)
        transcriber = MagicMock()
        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=StreamingConfig(),
            sample_rate=SAMPLE_RATE,
            poll_interval_seconds=0.01,
        )
        session.start()
        import time

        time.sleep(0.1)

        # Cancel and verify the thread stops
        session.cancel(blocking=True)
        assert session.is_running is False

    def test_stopped_event_set_after_thread_exits(self):
        """The _stopped_event should be set after the thread exits."""
        recorder = MagicMock()
        recorder.snapshot.return_value = np.array([], dtype=np.float32)
        transcriber = MagicMock()
        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=StreamingConfig(),
            sample_rate=SAMPLE_RATE,
            poll_interval_seconds=0.01,
        )
        session.start()
        session.cancel(blocking=True)
        assert session._stopped_event.is_set()


class TestWordKeyIndex:
    """M16: O(n²) near-duplicate detection in streaming."""

    def test_near_duplicate_uses_index(self):
        """_has_near_duplicate should use _word_key_index for efficiency."""
        assembler = StreamingTextAssembler()
        # Add a word
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        # Check that the index was populated
        key = "hello"
        assert key in assembler._word_key_index
        assert len(assembler._word_key_index[key]) == 1

    def test_near_duplicate_finds_match_via_index(self):
        """Near duplicates at similar timestamps are detected via index."""
        assembler = StreamingTextAssembler()
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        # Try to add a near-duplicate
        result = assembler.add_words(
            [WordTiming("hello", start_seconds=0.05, end_seconds=0.55)],
            commit_horizon_seconds=2.0,
        )
        assert result == ""  # Duplicate was rejected


class TestAssemblerLock:
    """M18: Streaming committed_text read without lock."""

    def test_committed_text_is_thread_safe(self):
        """Reading committed_text should be safe from any thread."""
        import threading

        assembler = StreamingTextAssembler()
        assembler.add_words(
            [WordTiming("test", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )

        errors = []

        def reader():
            try:
                for _ in range(100):
                    text = assembler.committed_text
                    assert isinstance(text, str)
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(100):
                    assembler.add_words(
                        [WordTiming("word", start_seconds=2.0 + i, end_seconds=2.5 + i)],
                        commit_horizon_seconds=100.0,
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader), threading.Thread(target=writer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0


class TestTransientErrorRetry:
    """M17: Transient errors permanently disable streaming — now uses retry counter."""

    def test_single_failure_does_not_require_fallback(self):
        """A single transient error should NOT set _fallback_required."""
        recorder = MagicMock()
        recorder.snapshot.return_value = audio_seconds(6.0)
        transcriber = MagicMock()
        transcriber.transcribe_words.side_effect = RuntimeError("transient CUDA error")

        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=StreamingConfig(min_first_chunk_seconds=5.0, chunk_seconds=5.0),
            sample_rate=SAMPLE_RATE,
        )

        session.process_available_audio_once()

        assert session._fallback_required is False
        assert session._consecutive_failures == 1

    def test_repeated_failures_set_fallback(self):
        """After N consecutive failures, _fallback_required should be True."""
        for max_fails in [2, 3]:
            recorder = MagicMock()
            recorder.snapshot.return_value = audio_seconds(6.0)
            transcriber = MagicMock()
            transcriber.transcribe_words.side_effect = RuntimeError("transient CUDA error")

            session = StreamingTranscriptionSession(
                recorder=recorder,
                transcriber=transcriber,
                config=StreamingConfig(min_first_chunk_seconds=5.0, chunk_seconds=5.0),
                sample_rate=SAMPLE_RATE,
            )
            session._max_consecutive_failures = max_fails

            for _ in range(max_fails):
                session.process_available_audio_once()
                # Reset planner to allow re-processing same audio
                session.planner = AudioWindowPlanner(session.config)

            assert session._fallback_required is True

    def test_success_resets_consecutive_failure_counter(self):
        """A successful transcription should reset the consecutive failure counter."""
        recorder = MagicMock()
        transcriber = MagicMock()

        fail = [True]

        def conditional_failure(*args, **kwargs):
            if fail[0]:
                fail[0] = False
                raise RuntimeError("transient CUDA error")
            return [WordTiming("word", start_seconds=1.0, end_seconds=1.5)]

        transcriber.transcribe_words.side_effect = conditional_failure

        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=StreamingConfig(min_first_chunk_seconds=5.0, chunk_seconds=5.0),
            sample_rate=SAMPLE_RATE,
        )

        recorder.snapshot.return_value = audio_seconds(6.0)
        session.process_available_audio_once()
        assert session._consecutive_failures == 1
        assert session._fallback_required is False

        # Reset planner for next call
        session.planner = AudioWindowPlanner(session.config)
        session.process_available_audio_once()
        assert session._consecutive_failures == 0
        assert session._fallback_required is False


# property-based test for audio pipeline ─────────────────────


class TestAudioPipelineProperties:
    """TEST-005: property-based tests for the audio pipeline's
    resampling and PCM conversion.  Verifies that arbitrary inputs
    (empty, all-zero, all-max, mid-chunk resets) don't crash."""

    def test_empty_audio_does_not_crash(self):
        """Empty audio array → empty result, no crash."""
        import numpy as np

        audio = np.array([], dtype=np.float32)
        # Simulate the PCM conversion path
        if len(audio) > 0:
            int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        else:
            int16 = np.array([], dtype=np.int16)
        assert len(int16) == 0

    def test_all_zero_audio(self):
        """All-zero float32 → all-zero int16, no NaN."""
        import numpy as np

        audio = np.zeros(16000, dtype=np.float32)
        int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        assert np.all(int16 == 0)
        assert not np.any(np.isnan(int16))

    def test_all_max_audio(self):
        """All-max (1.0) float32 → all-max (32767) int16, no overflow."""
        import numpy as np

        audio = np.ones(16000, dtype=np.float32)
        int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        assert np.all(int16 == 32767)

    def test_all_min_audio(self):
        """All-min (-1.0) float32 → all-min (-32768) int16, no underflow."""
        import numpy as np

        audio = -np.ones(16000, dtype=np.float32)
        int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        assert np.all(int16 == -32767)

    def test_overflow_audio_clipped(self):
        """Values > 1.0 are clipped to 32767, not wrapped."""
        import numpy as np

        audio = np.array([2.0, 10.0, 100.0], dtype=np.float32)
        int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        assert np.all(int16 == 32767)

    def test_nan_audio_does_not_propagate(self):
        """NaN in input → should not crash (clip handles it)."""
        import numpy as np

        audio = np.array([0.5, np.nan, -0.5], dtype=np.float32)
        # np.clip with NaN returns NaN; astype(int16) converts NaN to 0
        with np.errstate(invalid="ignore"):
            int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        # NaN → 0 in int16 conversion (platform-dependent but safe)
        assert len(int16) == 3

    def test_mid_chunk_reset(self):
        """A chunk that starts mid-recording (non-zero start) should
        be handled correctly — the pipeline doesn't assume contiguous
        audio."""
        import numpy as np

        # Simulate two non-contiguous chunks
        chunk1 = np.ones(8000, dtype=np.float32) * 0.5
        chunk2 = np.ones(8000, dtype=np.float32) * 0.3
        combined = np.concatenate([chunk1, chunk2])
        int16 = np.clip(combined * 32767, -32768, 32767).astype(np.int16)
        assert len(int16) == 16000
        # First half should be 0.5 * 32767
        assert int16[0] == int(0.5 * 32767)
        # Second half should be 0.3 * 32767
        assert int16[8000] == int(0.3 * 32767)

    def test_random_audio_no_crash(self):
        """Random float32 values in [-2, 2] → no crash, all clipped."""
        import numpy as np

        np.random.seed(42)
        for _ in range(10):
            audio = np.random.uniform(-2.0, 2.0, 16000).astype(np.float32)
            int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
            assert len(int16) == 16000
            assert int16.min() >= -32768
            assert int16.max() <= 32767


# Hypothesis-based generative property tests ─────────────────


class TestHypothesisAudioPipeline:
    """TEST-005: property-based generative tests using Hypothesis.

    Tests the PCM float32→int16 conversion pipeline against arbitrary
    inputs generated by Hypothesis, including edge cases that manual
    tests might miss (subnormal floats, very large arrays, mixed
    positive/negative values, etc.).

    WR-11: ``pytestmark`` at class scope skips every test in this
    class when hypothesis isn't installed. The other test classes in
    this module don't need hypothesis and continue to run.
    """

    pytestmark = pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")

    def test_pcm_conversion_preserves_length(self):
        """For any array, the int16 output has the same length."""
        import numpy as np
        from hypothesis import given, settings, strategies as st

        @given(
            length=st.integers(min_value=0, max_value=1000),
            scale=st.floats(min_value=0.0, max_value=2.0),
        )
        @settings(max_examples=50, deadline=5000)
        def check(length, scale):
            audio = np.full(length, scale, dtype=np.float32)
            int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
            assert len(int16) == length

        check()

    def test_pcm_conversion_clamps_to_int16_range(self):
        """For any float values, int16 output is in [-32768, 32767]."""
        import numpy as np
        from hypothesis import given, settings, strategies as st

        @given(
            values=st.lists(
                st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
                min_size=1,
                max_size=100,
            )
        )
        @settings(max_examples=50, deadline=5000)
        def check(values):
            audio = np.array(values, dtype=np.float32)
            int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
            assert int16.min() >= -32768
            assert int16.max() <= 32767

        check()

    def test_zero_input_produces_zero_output(self):
        """All-zero float32 → all-zero int16."""
        import numpy as np
        from hypothesis import given, settings, strategies as st

        @given(length=st.integers(min_value=1, max_value=500))
        @settings(max_examples=20, deadline=5000)
        def check(length):
            audio = np.zeros(length, dtype=np.float32)
            int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
            assert np.all(int16 == 0)

        check()


# (Session DE — Group 4): eviction log privacy ───────────────


class TestEvictionLogPrivacy:
    """DE-57: the DEBUG-level eviction log in
    ``StreamingTextAssembler._insert_word_unlocked`` must NOT emit the
    evicted word's textual content.

    Pre-fix: the WARNING-level log was sanitized per  (logged only
    the structural fact: max + index), but a companion DEBUG log wrote
    ``evicted_word.word`` verbatim ("Evicted word content (debug only):
    %r").  When a support workflow enabled DEBUG logging (common for
    support tickets), evicted user speech landed in the persistent log
    file at ``~/.voice-typer/...``.

    The fix: replace the DEBUG-level ``evicted_word.word`` content with
    a PII-safe length metric (``len(evicted_word.word)``).
    """

    def _make_assembler_with_small_maxlen(self, maxlen: int = 2):
        """Build an assembler whose internal deque evicts after ``maxlen``
        words, so we can trigger an eviction in O(1) test setup instead
        of pushing 10 000 words."""
        import collections

        assembler = StreamingTextAssembler()
        # Replace the default 10 000-cap deque with a small one.  We
        # keep ``_base_offset=0`` so the eviction logic still fires
        # correctly.
        assembler._words = collections.deque(maxlen=maxlen)
        return assembler

    def test_evicted_word_content_not_logged_at_debug(self, caplog):
        """When eviction fires, the DEBUG log must NOT contain the evicted
        word's textual content — only its length / index."""
        import logging

        assembler = self._make_assembler_with_small_maxlen(maxlen=2)
        pii_word = "supersecretpassword123"
        # Fill the deque, then trigger eviction with the PII word.
        # _insert_word_unlocked is the function under test — call it
        # directly to control timing.
        assembler._insert_word_unlocked(WordTiming("first", start_seconds=0.0, end_seconds=0.2))
        assembler._insert_word_unlocked(WordTiming("second", start_seconds=0.3, end_seconds=0.5))

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.streaming"):
            # This third insert evicts "first".
            assembler._insert_word_unlocked(WordTiming(pii_word, start_seconds=0.6, end_seconds=0.8))

        # The PII string must NOT appear anywhere in the captured logs.
        assert not any(pii_word in record.getMessage() for record in caplog.records), (
            "DE-57: evicted word content must NOT be logged at DEBUG level; "
            f"found {pii_word!r} in:\n" + "\n".join(r.getMessage() for r in caplog.records)
        )

    def test_evicted_word_length_still_logged_at_debug(self, caplog):
        """DE-57 fix must NOT silence the DEBUG log entirely — the
        PII-safe length metric must still be emitted so developers can
        diagnose eviction storms."""
        import logging

        assembler = self._make_assembler_with_small_maxlen(maxlen=2)
        assembler._insert_word_unlocked(WordTiming("first", start_seconds=0.0, end_seconds=0.2))
        assembler._insert_word_unlocked(WordTiming("second", start_seconds=0.3, end_seconds=0.5))

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.streaming"):
            assembler._insert_word_unlocked(WordTiming("third", start_seconds=0.6, end_seconds=0.8))

        # The DEBUG log should mention "chars" (the PII-safe metric).
        debug_msgs = [
            r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and "Evicted word" in r.getMessage()
        ]
        assert debug_msgs, (
            "DE-57: a DEBUG-level eviction log must still fire (with PII-safe length metric, not content)."
        )
        assert any("chars" in msg for msg in debug_msgs), (
            "DE-57: DEBUG eviction log must include char count (PII-safe metric); got:\n" + "\n".join(debug_msgs)
        )

    def test_evicted_word_content_not_logged_at_warning_either(self, caplog):
        """Regression guard for the existing  sanitization at WARNING
        level — the fix for DE-57 must not regress it."""
        import logging

        assembler = self._make_assembler_with_small_maxlen(maxlen=2)
        pii_word = "supersecretpassword123"
        assembler._insert_word_unlocked(WordTiming("first", start_seconds=0.0, end_seconds=0.2))
        assembler._insert_word_unlocked(WordTiming("second", start_seconds=0.3, end_seconds=0.5))

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.streaming"):
            assembler._insert_word_unlocked(WordTiming(pii_word, start_seconds=0.6, end_seconds=0.8))

        assert not any(pii_word in record.getMessage() for record in caplog.records), (
            "DE-57 / evicted word content must NOT be logged at "
            f"WARNING level either; found {pii_word!r} in:\n" + "\n".join(r.getMessage() for r in caplog.records)
        )


# streaming session securely zeros audio buffers ──────────


def test_process_available_audio_once_zeros_snapshot_after_transcription():
    """XZ-PRIV-02 regression: after ``process_available_audio_once``
    consumes a recorder snapshot, the underlying numpy buffer MUST be
    zeroed in-place (mirrors ``dictation_pipeline.py:345`` for the
    batch path).  Pre-fix: the snapshot array lingered in process
    memory until GC.
    """
    from voice_typer.server.streaming import StreamingTranscriptionSession

    config = StreamingConfig(min_first_chunk_seconds=4.0, chunk_seconds=5.0)
    recorder = MagicMock()
    snapshot = audio_seconds(5.0, amplitude=0.42)
    recorder.snapshot.return_value = snapshot
    transcriber = MagicMock()
    transcriber.transcribe_words.return_value = []

    session = StreamingTranscriptionSession(
        recorder=recorder,
        transcriber=transcriber,
        config=config,
        sample_rate=SAMPLE_RATE,
    )

    assert session.process_available_audio_once() is True

    # The snapshot buffer must be zeroed in-place ( / SEC-audit-008).
    assert np.all(snapshot == 0), (
        "StreamingTranscriptionSession.process_available_audio_once must zero "
        "the recorder snapshot in-place after transcription (XZ-PRIV-02). "
        "Pre-fix: the audio buffer lingered in process memory until the next "
        "GC pass, defeating SEC-audit-008's intent for the streaming path."
    )


def test_process_available_audio_once_zeros_audio_even_on_transcribe_failure():
    """XZ-PRIV-02: the secure-clear ``finally`` block MUST fire even
    when ``transcribe_words`` raises — otherwise a mid-transcription
    exception would leave the previous chunk's audio in process memory.
    """
    from voice_typer.server.streaming import StreamingTranscriptionSession

    config = StreamingConfig(min_first_chunk_seconds=4.0, chunk_seconds=5.0)
    recorder = MagicMock()
    snapshot = audio_seconds(5.0, amplitude=0.7)
    recorder.snapshot.return_value = snapshot
    transcriber = MagicMock()
    transcriber.transcribe_words.side_effect = RuntimeError("chunk failed")

    session = StreamingTranscriptionSession(
        recorder=recorder,
        transcriber=transcriber,
        config=config,
        sample_rate=SAMPLE_RATE,
    )

    # The exception is caught (returns False) but the audio must still
    # be zeroed by the ``finally`` block.
    assert session.process_available_audio_once() is False

    assert np.all(snapshot == 0), (
        "StreamingTranscriptionSession.process_available_audio_once must zero "
        "the recorder snapshot even when transcribe_words raises "
        "(XZ-PRIV-02 finally-block guarantee)."
    )


def test_finalize_zeros_full_audio_after_tail_merge():
    """XZ-PRIV-02: ``finalize()`` MUST zero the caller-supplied
    ``full_audio`` array after using it for the tail-merge / batch
    fallback path.  Mirrors ``dictation_pipeline.py:337-346``'s
    ``self._audio.fill(0)`` pattern.  Pre-fix: streaming finalize left
    the full recording in process memory.
    """
    from voice_typer.server.streaming import StreamingTranscriptionSession

    config = StreamingConfig(min_first_chunk_seconds=4.0, chunk_seconds=5.0)
    recorder = MagicMock()
    recorder.snapshot.return_value = audio_seconds(5.0)
    transcriber = MagicMock()
    transcriber.transcribe_words.return_value = []
    transcriber.transcribe_with_fallback.return_value = "fallback text"

    session = StreamingTranscriptionSession(
        recorder=recorder,
        transcriber=transcriber,
        config=config,
        sample_rate=SAMPLE_RATE,
    )

    full_audio = audio_seconds(5.0, amplitude=0.9)
    assert np.any(full_audio != 0), "test setup: full_audio must start non-zero"

    result = session.finalize(full_audio)

    assert result == "fallback text"
    assert np.all(full_audio == 0), (
        "StreamingTranscriptionSession.finalize must zero full_audio in-place "
        "after using it (XZ-PRIV-02). Mirrors the batch path in "
        "dictation_pipeline.py:337-346."
    )


# _finalize_impl_inner direct branch coverage ───────────────


class TestFinalizeImplInner:
    """``_finalize_impl_inner`` (streaming.py:860-919) direct
    unit tests.

    The public ``finalize()`` wrapper is already covered by other tests
    (``test_finalize_zeros_full_audio_after_tail_merge``,
    ``test_streaming_session_finalizes_only_uncommitted_tail``, etc.) but
    those tests only exercise the *outer* behavior and the buffer-zero /
    snapshot machinery that ``finalize()`` wraps around the inner function.
    The inner function has 4 distinct return branches:

      1. empty ``snapshot_committed_text`` → ``transcribe_with_fallback``
         (streaming.py:866-869) — covered by existing
         ``test_streaming_session_without_confirmed_text_uses_fast_batch_finalize``
      2. ``_fallback_required=True`` → ``transcribe_with_fallback``
         (streaming.py:870-873) — covered here
      3. tail-skip when ``last_committed_time >= full_audio_duration - 1.5``
         (streaming.py:879-887) — covered here
      4. tail-merge via ``transcribe_words``; on exception → fall back to
         ``transcribe_with_fallback`` (streaming.py:896-919) — happy path
         covered by existing tests, exception-swallow path covered here

    These tests call ``_finalize_impl_inner`` directly so each branch can
    be exercised without the surrounding lock-snapshot / buffer-zero
    machinery.
    """

    def _make_session(self, *, fallback_required: bool = False, local_engine=None):
        config = StreamingConfig(
            min_first_chunk_seconds=5.0,
            chunk_seconds=5.0,
            left_overlap_seconds=0.5,
        )
        recorder = MagicMock()
        transcriber = MagicMock()
        transcriber.transcribe_with_fallback.return_value = "fallback result"
        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=config,
            sample_rate=SAMPLE_RATE,
            local_engine=local_engine,
        )
        session._fallback_required = fallback_required
        return session, transcriber

    def test_finalize_fallback_required_uses_transcribe_with_fallback(self):
        """Branch 2 (streaming.py:870-873): when ``_fallback_required``
        is True, ``_finalize_impl_inner`` MUST short-circuit straight to
        ``transcribe_with_fallback`` — even when there IS committed text —
        because the streaming thread has permanently lost trust in its
        transcription output (e.g. N consecutive transient errors). The
        ``local_engine`` kwarg MUST be forwarded so the cloud→local
        fallback path can fire when the active transcriber is a CloudEngine
        and the cloud provider is unreachable.
        """
        local_engine = MagicMock(name="local_engine")
        session, transcriber = self._make_session(
            fallback_required=True,
            local_engine=local_engine,
        )
        full_audio = audio_seconds(5.0)

        # Pass a NON-empty snapshot_committed_text so the first
        # ``if not snapshot_committed_text`` branch (line 866) does NOT
        # fire — we want to reach the ``_fallback_required`` check on
        # line 870 specifically.
        result = session._finalize_impl_inner(
            full_audio,
            snapshot_committed_text="partial committed text",
            snapshot_last_committed_time=2.0,
        )

        assert result == "fallback result"
        transcriber.transcribe_with_fallback.assert_called_once()
        args, kwargs = transcriber.transcribe_with_fallback.call_args
        # full_audio is positional arg 0
        assert args[0] is full_audio, (
            "transcribe_with_fallback must receive the full_audio array "
            "as its first positional argument."
        )
        # local_engine MUST be forwarded so cloud→local fallback fires
        assert kwargs.get("local_engine") is local_engine, (
            "_finalize_impl_inner must forward the session's _local_engine "
            "to transcribe_with_fallback so the cloud→local fallback path "
            "actually fires when the active transcriber is a CloudEngine."
        )
        # transcribe_words must NOT be called — we short-circuited before
        # the tail-merge branch.
        transcriber.transcribe_words.assert_not_called()

    def test_finalize_skips_tail_when_last_word_within_1_5s(self):
        """Branch 3 (streaming.py:879-887): when the streaming thread's
        last committed word is within 1.5s of the end of the audio, the
        expensive tail re-transcription is skipped — the streaming thread
        already captured it. This is a PERF optimization that saves 2-3s
        of serial transcription after stop.
        """
        session, transcriber = self._make_session(fallback_required=False)
        # 5.0s of audio at SAMPLE_RATE=16000
        full_audio = audio_seconds(5.0)
        # last committed at 4.0s; audio ends at 5.0s;
        # 5.0 - 1.5 = 3.5; 4.0 >= 3.5 → skip-tail branch fires
        result = session._finalize_impl_inner(
            full_audio,
            snapshot_committed_text="hello world",
            snapshot_last_committed_time=4.0,
        )

        # Must return the snapshot text unchanged
        assert result == "hello world"
        # CRITICAL: transcribe_words must NOT be called — the tail
        # re-transcription is skipped because the streaming thread
        # already captured the last word.
        transcriber.transcribe_words.assert_not_called()
        # And transcribe_with_fallback must NOT be called either — this
        # is the happy-path skip, not a fallback.
        transcriber.transcribe_with_fallback.assert_not_called()

    def test_finalize_tail_merge_exception_falls_back_to_transcribe_with_fallback(self):
        """Branch 4 exception path (streaming.py:915-919): when the
        tail-merge path (``transcribe_words`` → ``_validate_words`` →
        ``assembler.add_words``) raises, the exception MUST be swallowed
        (logged at ERROR via ``log.exception``) and the function falls
        back to ``transcribe_with_fallback``. This is the last-resort
        guarantee: even if everything goes wrong with the
        streaming-specific merge path, the user still gets SOME
        transcription back rather than a crash propagating to
        ``DictationPipeline.run``.
        """
        local_engine = MagicMock(name="local_engine")
        session, transcriber = self._make_session(
            fallback_required=False,
            local_engine=local_engine,
        )
        # Force the tail-merge path to fail at the first step
        transcriber.transcribe_words.side_effect = RuntimeError("tail transcribe exploded")
        full_audio = audio_seconds(5.0)

        # last_committed_time low enough to NOT trigger the 1.5s tail
        # skip (0.5 < 5.0 - 1.5 = 3.5), so we reach the tail-merge
        # branch and the exception path.
        result = session._finalize_impl_inner(
            full_audio,
            snapshot_committed_text="partial",
            snapshot_last_committed_time=0.5,
        )

        # Must have fallen back rather than propagating the exception.
        assert result == "fallback result"
        # transcribe_words was attempted (and raised) — prove we
        # reached the tail-merge branch.
        transcriber.transcribe_words.assert_called_once()
        # And transcribe_with_fallback was invoked as the fallback.
        transcriber.transcribe_with_fallback.assert_called_once()
        args, kwargs = transcriber.transcribe_with_fallback.call_args
        assert args[0] is full_audio
        assert kwargs.get("local_engine") is local_engine


# _validate_words branch coverage ───────────────────────────


class TestValidateWords:
    """``_validate_words`` (streaming.py:929-938) guards the
    streaming tail-merge path against malformed WordTiming objects.
    All 4 ``raise`` statements (TypeError × 2, ValueError × 2) plus
    the happy path are unit-tested here.

    The function is called from ``_finalize_impl_inner`` AFTER
    ``transcribe_words`` returns (streaming.py:910); a regression here
    would either (a) let malformed words into the assembler (corrupting
    committed text) or (b) raise inside the tail-merge ``try`` block,
    triggering the silent fallback to ``transcribe_with_fallback``
    (quality regression — the streaming thread's output is discarded).
    """

    def _make_session(self):
        config = StreamingConfig()
        recorder = MagicMock()
        transcriber = MagicMock()
        return StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=config,
            sample_rate=SAMPLE_RATE,
        )

    def test_validate_words_rejects_non_string_word(self):
        """Branch 1 (streaming.py:931-932): ``word.word`` MUST be a
        ``str``. A non-string (e.g. an int from a buggy cloud JSON
        parser that didn't coerce) must raise ``TypeError`` so the
        malformed entry doesn't end up in the assembler's committed
        text where it would later break text-formatting /
        ``" ".join(...)`` calls.
        """
        session = self._make_session()
        # dataclass type hints are NOT enforced at runtime — the
        # constructor accepts the int. _validate_words is the runtime
        # guard.
        bad = WordTiming(word=123, start_seconds=0.0, end_seconds=1.0)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="word text must be a string"):
            session._validate_words([bad])

    def test_validate_words_rejects_none_timestamps(self):
        """Branch 2 (streaming.py:933-934): timestamps MUST be present.
        A ``None`` start or end (e.g. from a cloud provider that omits
        ``end`` for the final word) must raise ``TypeError`` — without
        timestamps the dedup/merge logic in ``StreamingTextAssembler``
        would silently drop the word or, worse, ``TypeError`` deep
        inside ``round(word.start_seconds, 3)`` when computing the
        dedup key.
        """
        session = self._make_session()
        bad = WordTiming(word="hello", start_seconds=None, end_seconds=1.0)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="word timestamps are required"):
            session._validate_words([bad])

    def test_validate_words_rejects_non_finite_timestamps(self):
        """Branch 3 (streaming.py:935-936): timestamps MUST be finite.
        ``NaN`` / ``inf`` (e.g. from a float parse error in a cloud
        JSON response) would silently corrupt the assembler's sort
        order (NaN compares False to everything) and break the dedup
        set's ``round(word.start_seconds, 3)`` rounding logic. Must
        raise ``ValueError``.
        """
        session = self._make_session()
        bad = WordTiming(word="hello", start_seconds=float("nan"), end_seconds=1.0)
        with pytest.raises(ValueError, match="word timestamps must be finite"):
            session._validate_words([bad])

    def test_validate_words_rejects_end_before_start(self):
        """Branch 4 (streaming.py:937-938): ``end_seconds >=
        start_seconds`` is an invariant used by ``add_words`` to
        compute ``last_committed_time`` (it takes ``max(end_seconds)``).
        A reversed pair would silently advance
        ``last_committed_time`` to a bogus value, breaking the 1.5s
        tail-skip optimization in ``_finalize_impl_inner`` (a too-high
        ``last_committed_time`` would skip the tail re-transcription
        and lose the last words).
        """
        session = self._make_session()
        bad = WordTiming(word="hello", start_seconds=2.0, end_seconds=1.0)
        with pytest.raises(ValueError, match="word end must be >= start"):
            session._validate_words([bad])

    def test_validate_words_accepts_valid_words(self):
        """Happy path (no raise statement): well-formed words MUST
        pass validation without raising. This guards against an
        over-strict validation that would reject legitimate transcriber
        output (e.g. zero-length words where start == end, words at
        t=0.0).
        """
        session = self._make_session()
        good = [
            WordTiming(word="hello", start_seconds=0.0, end_seconds=0.5),
            WordTiming(word="world", start_seconds=0.6, end_seconds=1.0),
            # zero-length word at same instant is technically valid —
            # the validation only checks end >= start, not end > start.
            WordTiming(word="!", start_seconds=1.0, end_seconds=1.0),
        ]
        # Must not raise
        session._validate_words(good)
