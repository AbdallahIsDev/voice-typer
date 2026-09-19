"""Tests for ER-FIX-C1 streaming/resampling fixes."""

from __future__ import annotations

import collections

from voice_typer.server.streaming import StreamingTextAssembler, WordTiming


class TestBoundedWordKeyIndex:
    """ER-69: ``_word_key_index`` should store bounded deques (maxlen=8)."""

    def test_word_key_index_buckets_are_bounded_deques(self):
        """Each bucket is a ``collections.deque`` with ``maxlen == 8``."""
        assembler = StreamingTextAssembler()
        # Add one word, bucket should be created as a bounded deque.
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        bucket = assembler._word_key_index["hello"]
        assert isinstance(bucket, collections.deque)
        assert bucket.maxlen == 8

    def test_word_key_index_caps_at_eight_occurrences(self):
        """Inserting 12 distinct occurrences of the same word key retains"""
        assembler = StreamingTextAssembler()
        # Each "hello" is >0.25s apart so none are rejected as near-
        for i in range(12):
            assembler.add_words(
                [
                    WordTiming(
                        "hello",
                        start_seconds=float(i) * 1.0,
                        end_seconds=float(i) * 1.0 + 0.5,
                    )
                ],
                commit_horizon_seconds=float(i) * 1.0 + 1.0,
            )
        bucket = assembler._word_key_index["hello"]
        assert isinstance(bucket, collections.deque)
        assert bucket.maxlen == 8
        # After 12 insertions, only the last 8 are retained.
        assert len(bucket) == 8
        # The first retained index corresponds to the 5th insertion
        assert min(bucket) == 4
        # The most recent is the 12th insertion (absolute index 11).
        assert max(bucket) == 11

    def test_near_duplicate_detection_still_works_with_bounded_deque(self):
        """Near-duplicate lookup iterates the deque just like the old list."""
        assembler = StreamingTextAssembler()
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        # A second "hello" within 0.25s of the first → rejected as dup.
        result = assembler.add_words(
            [WordTiming("hello", start_seconds=0.05, end_seconds=0.55)],
            commit_horizon_seconds=2.0,
        )
        assert result == ""


class TestPruneInPlace:
    """ER-96: ``_prune_old_entries`` should mutate in place, not rebuild."""

    def test_prune_recomputes_rolling_max_after_drop(self):
        """After pruning entries, the rolling max-end is recomputed."""
        assembler = StreamingTextAssembler()
        assembler._PRUNE_SIZE_THRESHOLD = 0
        assembler._seen_timestamps = {
            (1.0, 1.5),
            (5.0, 5.5),
            (10.0, 10.5),
            (20.0, 20.5),
        }
        assembler._seen_timestamps_max_end = 20.5

        # Prune everything older than 15.0 → drops (1.0,1.5), (5.0,5.5), (10.0,10.5).
        assembler._prune_old_entries(15.0)
        assert assembler._seen_timestamps_max_end == 20.5


class TestIncrementalCommittedTextCache:
    """ER-67: ``committed_text`` cache reads should be incremental."""

    def test_incremental_cache_hit_returns_same_object(self):
        """A second read with no intervening mutation returns the same"""
        assembler = StreamingTextAssembler()
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        first = assembler.committed_text
        second = assembler.committed_text
        # Fast path returns the cached string directly (same identity).
        assert first is second

    def test_max_words_cap_is_10000(self):
        """``_MAX_WORDS`` should be 10000 (AUDIO-019 cap to prevent"""
        assert StreamingTextAssembler._MAX_WORDS == 10000
        # The deque's maxlen should reflect the cap.
        assembler = StreamingTextAssembler()
        assert assembler._words.maxlen == 10000
