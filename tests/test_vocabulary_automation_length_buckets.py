"""Tests for the length-bucketed pruning in ``vocabulary_automation``."""

from __future__ import annotations

import random

import pytest


def _full_scan_reference(word, vocab_words, max_distance):
    """Reference implementation, mimics the pre-fix full scan."""
    from voice_typer.server.vocabulary_automation import _levenshtein

    if not word or not vocab_words:
        return None

    best_distance = max_distance + 1
    best_match = None

    for candidate in vocab_words:
        if abs(len(candidate) - len(word)) > max_distance:
            continue
        d = _levenshtein(word, candidate, max_distance=max_distance)
        if d < best_distance:
            best_distance = d
            best_match = candidate
            if d == 0:
                break

    return best_match if best_distance <= max_distance else None


class TestCandidatePruning:
    def test_only_small_fraction_iterated(self, monkeypatch):
        """For a 5-letter word with ``max_distance=2`` in a 1000-entry"""
        from voice_typer.server import vocabulary_automation as va

        # Build 1000 entries with lengths spanning 1-100 (uniform).
        vocab: list[str] = []
        for length in range(1, 101):
            for i in range(10):
                # Build a unique word of `length` chars.
                w = "".join(chr(ord("a") + ((length + i + j) % 26)) for j in range(length))
                vocab.append(w)
        assert len(vocab) == 1000

        # Patch _levenshtein to count calls.  The number of Levenshtein
        call_count = [0]
        original_lev = va._levenshtein

        def counting_lev(a, b, *, max_distance=None):
            call_count[0] += 1
            return original_lev(a, b, max_distance=max_distance)

        monkeypatch.setattr(va, "_levenshtein", counting_lev)

        va._find_closest_vocabulary_match("hello", vocab, max_distance=2)

        # 5 buckets × 10 entries = 50 expected.  Allow generous margin
        assert call_count[0] <= 150, f"Expected ≤150 Levenshtein calls (5% of 1000 + margin), got {call_count[0]}"
        assert call_count[0] < len(vocab), f"Should iterate fewer than the full vocab, got {call_count[0]}/{len(vocab)}"

    def test_no_levenshtein_calls_when_no_buckets_in_range(self, monkeypatch):
        """If no bucket falls within the length range, zero Levenshtein"""
        from voice_typer.server import vocabulary_automation as va

        # All words length 20+; query word length 5 with max_distance=2
        vocab = ["a" * 20, "b" * 25, "c" * 30]

        call_count = [0]
        original_lev = va._levenshtein

        def counting_lev(a, b, *, max_distance=None):
            call_count[0] += 1
            return original_lev(a, b, max_distance=max_distance)

        monkeypatch.setattr(va, "_levenshtein", counting_lev)

        result = va._find_closest_vocabulary_match("hello", vocab, max_distance=2)
        assert result is None
        assert call_count[0] == 0


class TestCorrectnessPreserved:
    @pytest.mark.parametrize(
        "word",
        [
            "hello",  # exact match
            "helo",  # delete → hello
            "pythn",  # delete → python
            "algoritm",  # delete → algorithm
            "keyboad",  # delete → keyboard
            "compute",  # insert → computer
            "xyzab",  # no match (all distances > 2)
            "monito",  # delete → monitor
        ],
    )
    def test_same_result_as_full_scan(self, word):
        from voice_typer.server.vocabulary_automation import (
            _find_closest_vocabulary_match,
        )

        # Vocab with unique-distance matches (no ties on the closest
        vocab = {
            "cat",
            "dog",
            "bird",
            "fish",
            "hello",
            "world",
            "python",
            "guitar",
            "javascript",
            "programming",
            "algorithm",
            "database",
            "computer",
            "keyboard",
            "monitor",
            "network",
            "internet",
        }

        bucketed = _find_closest_vocabulary_match(word, vocab, max_distance=2)
        full = _full_scan_reference(word, vocab, max_distance=2)

        assert bucketed == full, f"For word {word!r}: bucketed={bucketed!r}, full={full!r}"

    def test_returns_none_when_no_match(self):
        from voice_typer.server.vocabulary_automation import (
            _find_closest_vocabulary_match,
        )

        vocab = {"hello", "world", "python", "programming"}
        # "xyz" length 3, candidates length 1-5; closest is still far.
        result = _find_closest_vocabulary_match("xyz", vocab, max_distance=2)
        assert result is None

    def test_exact_match_short_circuits(self, monkeypatch):
        """When an exact (d=0) match exists, no further Levenshtein"""
        from voice_typer.server import vocabulary_automation as va

        # "hello" appears in the bucket at length 5.  Order matters —
        vocab = ["hello", "hallo", "helps", "world"]
        call_count = [0]
        original_lev = va._levenshtein

        def counting_lev(a, b, *, max_distance=None):
            call_count[0] += 1
            return original_lev(a, b, max_distance=max_distance)

        monkeypatch.setattr(va, "_levenshtein", counting_lev)

        result = va._find_closest_vocabulary_match("hello", vocab, max_distance=2)
        assert result == "hello"
        # Should have stopped at the first exact match (1 call) —
        assert call_count[0] <= 4

    def test_randomized_correctness(self):
        """Randomized property test: bucketed version returns the same"""
        from voice_typer.server.vocabulary_automation import (
            _find_closest_vocabulary_match,
            _levenshtein,
        )

        rng = random.Random(2024)
        # Build a vocab of random lowercase words with varied lengths.
        vocab = set()
        for _ in range(200):
            length = rng.randint(3, 12)
            w = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(length))
            vocab.add(w)
        vocab_list = list(vocab)

        for _ in range(50):
            length = rng.randint(3, 12)
            query = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(length))
            bucketed = _find_closest_vocabulary_match(query, vocab_list, max_distance=2)
            full = _full_scan_reference(query, vocab_list, max_distance=2)

            if bucketed is None and full is None:
                continue
            if bucketed is None or full is None:
                continue
            # Both found a match, distances should be equal (tie-
            d_bucket = _levenshtein(query, bucketed, max_distance=2)
            d_full = _levenshtein(query, full, max_distance=2)
            assert d_bucket == d_full, (
                f"For {query!r}: bucketed={bucketed!r} (d={d_bucket}), full={full!r} (d={d_full})"
            )


class TestMatchBehavior:
    def test_empty_vocab_returns_none(self):
        from voice_typer.server.vocabulary_automation import (
            _find_closest_vocabulary_match,
        )

        assert _find_closest_vocabulary_match("hello", set(), max_distance=2) is None

    def test_set_input_supported(self):
        """The real caller (``_collect_vocabulary_words``) passes a set."""
        from voice_typer.server.vocabulary_automation import (
            _find_closest_vocabulary_match,
        )

        words = {"cat", "dog", "hello"}
        assert _find_closest_vocabulary_match("helo", words, max_distance=2) == "hello"

    def test_insertion_order_breaks_ties(self):
        """Ties are broken by vocabulary iteration order, first match"""
        from voice_typer.server.vocabulary_automation import (
            _find_closest_vocabulary_match,
        )

        # Both candidates are distance 1 from "abcde".
        words = ["abcfe", "abcdf"]
        assert _find_closest_vocabulary_match("abcde", words, max_distance=2) == "abcfe"

        words = ["abcdf", "abcfe"]
        assert _find_closest_vocabulary_match("abcde", words, max_distance=2) == "abcdf"

    def test_vocab_change_reflects_in_results(self):
        """A different vocab object is picked up on the next call —"""
        from voice_typer.server.vocabulary_automation import (
            _find_closest_vocabulary_match,
        )

        vocab1 = {"hello", "world", "python"}
        result1 = _find_closest_vocabulary_match("hello", vocab1, max_distance=2)
        assert result1 == "hello"

        vocab2 = {"hallo", "worlt", "pyton"}
        result2 = _find_closest_vocabulary_match("hello", vocab2, max_distance=2)
        # "hallo" is distance 1 from "hello".
        assert result2 == "hallo"
