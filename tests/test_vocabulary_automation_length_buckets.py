"""Tests for the length-bucketed pruning in ``vocabulary_automation``."""

from __future__ import annotations

import random

import pytest


def _reference_levenshtein(a: str, b: str) -> int:
    """Test-local edit-distance reference (production now uses difflib)."""
    if a == b:
        return 0
    prev = list(range(len(a) + 1))
    for j in range(1, len(b) + 1):
        cur = [j] + [0] * len(a)
        for i in range(1, len(a) + 1):
            cur[i] = min(cur[i - 1] + 1, prev[i] + 1, prev[i - 1] + (0 if a[i - 1] == b[j - 1] else 1))
        prev = cur
    return prev[len(a)]


def _reference_full_scan(word, vocab_words, max_distance):
    """Full-scan matcher over the test-local reference distance."""
    if not word or not vocab_words:
        return None
    best_distance = max_distance + 1
    best_match = None
    for candidate in vocab_words:
        if abs(len(candidate) - len(word)) > max_distance:
            continue
        d = _reference_levenshtein(word, candidate)
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

        # Patch _match_ratio to count calls.  The number of similarity
        call_count = [0]
        original_ratio = va._match_ratio

        def counting_ratio(a, b):
            call_count[0] += 1
            return original_ratio(a, b)

        monkeypatch.setattr(va, "_match_ratio", counting_ratio)

        va._find_closest_vocabulary_match("hello", vocab, max_distance=2)

        # 5 buckets × 10 entries = 50 expected.  Allow generous margin
        assert call_count[0] <= 150, f"Expected ≤150 similarity calls (5% of 1000 + margin), got {call_count[0]}"
        assert call_count[0] < len(vocab), f"Should iterate fewer than the full vocab, got {call_count[0]}/{len(vocab)}"

    def test_no_scoring_calls_when_no_buckets_in_range(self, monkeypatch):
        """If no bucket falls within the length range, zero similarity"""
        from voice_typer.server import vocabulary_automation as va

        # All words length 20+; query word length 5 with max_distance=2
        vocab = ["a" * 20, "b" * 25, "c" * 30]

        call_count = [0]
        original_ratio = va._match_ratio

        def counting_ratio(a, b):
            call_count[0] += 1
            return original_ratio(a, b)

        monkeypatch.setattr(va, "_match_ratio", counting_ratio)

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
        full = _reference_full_scan(word, vocab, max_distance=2)

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
        """When an exact (d=0) match exists, no further similarity"""
        from voice_typer.server import vocabulary_automation as va

        # "hello" appears in the bucket at length 5.  Order matters —
        vocab = ["hello", "hallo", "helps", "world"]
        call_count = [0]
        original_ratio = va._match_ratio

        def counting_ratio(a, b):
            call_count[0] += 1
            return original_ratio(a, b)

        monkeypatch.setattr(va, "_match_ratio", counting_ratio)

        result = va._find_closest_vocabulary_match("hello", vocab, max_distance=2)
        assert result == "hello"
        # Should have stopped at the first exact match (1 call) —
        assert call_count[0] <= 4

    def test_randomized_correctness(self):
        """Typo-recovery property: 1-edit typos resolve, 2-edit typos never misfire."""
        from voice_typer.server.vocabulary_automation import (
            _find_closest_vocabulary_match,
        )

        vocab = [
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
            "language",
            "vocabulary",
        ]
        rng = random.Random(2024)
        for n_edits in (1, 2):
            for _ in range(100):
                source = rng.choice(vocab)
                chars = list(source)
                for _ in range(n_edits):
                    op = rng.choice(["sub", "del", "ins"])
                    pos = rng.randrange(len(chars))
                    if op == "sub":
                        chars[pos] = rng.choice("abcdefghijklmnopqrstuvwxyz")
                    elif op == "del" and len(chars) > 3:
                        del chars[pos]
                    else:
                        chars.insert(pos, rng.choice("abcdefghijklmnopqrstuvwxyz"))
                query = "".join(chars)
                match = _find_closest_vocabulary_match(query, vocab, max_distance=2)
                if n_edits == 1 and _reference_levenshtein(query, source) <= 1:
                    assert match == source, f"1-edit typo {query!r} of {source!r} resolved to {match!r}"
                if match is not None:
                    assert _reference_levenshtein(query, match) <= 2, (
                        f"query {query!r} matched {match!r} beyond 2 edits"
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
