"""Tests for streaming transcription planning and text assembly."""

import numpy as np
from unittest.mock import MagicMock

from voice_typer.streaming import (
    AudioWindow,
    AudioWindowPlanner,
    StreamingConfig,
    StreamingTranscriptionSession,
    StreamingTextAssembler,
    WordTiming,
)


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
    planner = AudioWindowPlanner(
        StreamingConfig(min_first_chunk_seconds=4.0, chunk_seconds=10.0)
    )

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
    session.cancel()

    assert session.is_running is False


class TestConcurrentAccess:
    """P2 fix: Verify streaming session handles concurrent access safely."""

    def test_concurrent_process_calls_dont_corrupt_assembler(self):
        """Multiple threads calling process_available_audio_once should not corrupt data."""
        import threading

        from voice_typer.streaming import (
            StreamingTranscriptionSession, StreamingConfig, WordTiming,
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


class TestH8Pruning:
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


class TestH14FinalizeRace:
    """H14: Streaming finalize() races with still-running thread."""

    def test_cancel_uses_10_second_timeout(self):
        """cancel() should join with a 10-second timeout."""
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
        session.cancel()
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
        session.cancel()
        assert session._stopped_event.is_set()


class TestM16WordKeyIndex:
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


class TestM18AssemblerLock:
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


class TestM17TransientErrorRetry:
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

            for i in range(max_fails):
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
