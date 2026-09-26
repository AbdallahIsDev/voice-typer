"""Parity tests for the difflib vocabulary matcher (replaces the DP Levenshtein)."""

from __future__ import annotations

import pytest
from voice_typer.server.vocabulary_automation import (
    MAX_LEVENSHTEIN_DISTANCE,
    _find_closest_vocabulary_match,
    _match_ratio,
    _ratio_cutoff,
)


def _reference_levenshtein(a: str, b: str) -> int:
    """Independent edit-distance reference kept in the test only."""
    if a == b:
        return 0
    prev = list(range(len(a) + 1))
    for j in range(1, len(b) + 1):
        cur = [j] + [0] * len(a)
        for i in range(1, len(a) + 1):
            cur[i] = min(cur[i - 1] + 1, prev[i] + 1, prev[i - 1] + (0 if a[i - 1] == b[j - 1] else 1))
        prev = cur
    return prev[len(a)]


TYPO_PAIRS = [
    ("helo", "hello"),
    ("definately", "definitely"),
    ("recieve", "receive"),
    ("teh", "the"),
    ("pyathon", "python"),
    ("jonh", "john"),
    ("qucik", "quick"),
    ("seperate", "separate"),
    ("occured", "occurred"),
    ("grammer", "grammar"),
    ("monito", "monitor"),
    ("keyboad", "keyboard"),
    ("algoritm", "algorithm"),
    ("pythn", "python"),
    ("compute", "computer"),
    ("cat", "cut"),
    ("cat", "cats"),
]

FAR_PAIRS = [
    ("xyz", "hello"),
    ("xyzab", "computer"),
    ("suspicious", "word"),
    ("kitten", "sitting"),
]


class TestThresholdConstant:
    def test_max_distance_stays_two(self):
        assert MAX_LEVENSHTEIN_DISTANCE == 2


class TestRatioMatchesReferenceDecisions:
    @pytest.mark.parametrize(("typo", "correct"), TYPO_PAIRS)
    def test_close_typo_accepted(self, typo, correct):
        assert _reference_levenshtein(typo, correct) <= MAX_LEVENSHTEIN_DISTANCE
        assert _match_ratio(typo, correct) >= _ratio_cutoff(len(typo), len(correct), MAX_LEVENSHTEIN_DISTANCE)

    @pytest.mark.parametrize(("word", "other"), FAR_PAIRS)
    def test_far_pair_rejected(self, word, other):
        assert _reference_levenshtein(word, other) > MAX_LEVENSHTEIN_DISTANCE
        if abs(len(word) - len(other)) <= MAX_LEVENSHTEIN_DISTANCE:
            assert _match_ratio(word, other) < _ratio_cutoff(len(word), len(other), MAX_LEVENSHTEIN_DISTANCE)


class TestMatcherParityEndToEnd:
    def test_typo_resolves_to_vocab_word(self):
        vocab = {"cat", "dog", "hello", "world", "python", "definitely"}
        assert _find_closest_vocabulary_match("helo", vocab, max_distance=2) == "hello"
        assert _find_closest_vocabulary_match("definately", vocab, max_distance=2) == "definitely"

    def test_no_match_returns_none(self):
        assert _find_closest_vocabulary_match("xyz", {"hello", "world"}, max_distance=2) is None
        assert _find_closest_vocabulary_match("hello", set(), max_distance=2) is None

    def test_exact_match_short_circuits(self):
        assert _find_closest_vocabulary_match("hello", ["hello", "hallo"], max_distance=2) == "hello"

    def test_insertion_order_breaks_ties(self):
        assert _find_closest_vocabulary_match("abcde", ["abcfe", "abcdf"], max_distance=2) == "abcfe"
        assert _find_closest_vocabulary_match("abcde", ["abcdf", "abcfe"], max_distance=2) == "abcdf"

    def test_length_bucket_prefilter_intact(self, monkeypatch):
        from voice_typer.server import vocabulary_automation as va

        calls = []
        real = va._match_ratio

        def _counting(a, b):
            calls.append((a, b))
            return real(a, b)

        monkeypatch.setattr(va, "_match_ratio", _counting)
        assert va._find_closest_vocabulary_match("hello", ["a" * 20, "b" * 25], max_distance=2) is None
        assert calls == []
