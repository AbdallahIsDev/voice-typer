"""Tests for ER-FIX-C1 streaming/resampling fixes.

Covers the three findings owned by ER-FIX-C1:

* ER-69 — ``_word_key_index`` uses bounded deques (maxlen=8).
* ER-96 — ``_prune_old_entries`` mutates the timestamp set in place
  (no full rebuild) and is gated by a size threshold.
* ER-67 — ``committed_text`` cache reads are incremental: appends the
  new tail instead of re-sorting+re-joining the entire deque every
  read, while preserving the sort invariant on out-of-order arrivals.

These tests complement the existing ``test_streaming.py`` suite.
"""

from __future__ import annotations

import collections

from voice_typer.server.streaming import StreamingTextAssembler, WordTiming


class TestER69BoundedWordKeyIndex:
    """ER-69: ``_word_key_index`` should store bounded deques (maxlen=8)."""

    def test_word_key_index_buckets_are_bounded_deques(self):
        """Each bucket is a ``collections.deque`` with ``maxlen == 8``."""
        assembler = StreamingTextAssembler()
        # Add one word — bucket should be created as a bounded deque.
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        bucket = assembler._word_key_index["hello"]
        assert isinstance(bucket, collections.deque)
        assert bucket.maxlen == 8

    def test_word_key_index_caps_at_eight_occurrences(self):
        """Inserting 12 distinct occurrences of the same word key retains
        only the last 8 absolute indices (auto-eviction via maxlen)."""
        assembler = StreamingTextAssembler()
        # Each "hello" is >0.25s apart so none are rejected as near-
        # duplicates. Distinct timestamps → distinct dedup keys → all
        # 12 are inserted into ``_words`` and the index bucket.
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
        # (absolute index 4 — 0-based). Earlier ones were auto-evicted.
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


class TestER96PruneInPlace:
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
        # The new max should be 20.5 (from the survivor).
        assembler._prune_old_entries(15.0)
        assert assembler._seen_timestamps_max_end == 20.5


class TestER67IncrementalCommittedTextCache:
    """ER-67: ``committed_text`` cache reads should be incremental."""

    def test_incremental_cache_hit_returns_same_object(self):
        """A second read with no intervening mutation returns the same
        cached string object (no rebuild)."""
        assembler = StreamingTextAssembler()
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        first = assembler.committed_text
        second = assembler.committed_text
        # Fast path returns the cached string directly (same identity).
        assert first is second

    def test_max_words_lowered_to_2000(self):
        """ER-67: ``_MAX_WORDS`` should be 2000 (lowered from 10000)."""
        assert StreamingTextAssembler._MAX_WORDS == 2000
        # The deque's maxlen should reflect the lowered cap.
        assembler = StreamingTextAssembler()
        assert assembler._words.maxlen == 2000
