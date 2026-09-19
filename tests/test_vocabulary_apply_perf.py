"""Tests for the ``VocabularyManager.apply_to_text`` perf fixes."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch


def _make_vocab(tmp_path):
    from voice_typer.server.vocabulary import VocabularyManager

    return VocabularyManager(
        config_dir=Path(tmp_path),
        bundled_path=Path(tmp_path) / "nonexistent-bundled.json",
    )


def _apply_to_text_source() -> str:
    """Return the source code of ``VocabularyManager.apply_to_text``"""
    from voice_typer.server.vocabulary import VocabularyManager

    return inspect.getsource(VocabularyManager.apply_to_text)


class TestSingleTokenizationPass:
    """``str.split`` + ``\" \".join`` run at most once per"""

    def test_single_split_call_in_source(self) -> None:
        src = _apply_to_text_source()
        split_token = '.split(" ")'
        actual = src.count(split_token)
        assert actual == 1, f"apply_to_text must tokenize exactly once; found {actual} {split_token!r} calls in source"

    def test_single_join_call_in_source(self) -> None:
        src = _apply_to_text_source()
        # Pre-fix: 4 occurrences. Post-fix: exactly 1.
        join_token = '" ".join('
        actual = src.count(join_token)
        assert actual == 1, (
            f"apply_to_text must join tokens exactly once; found {actual} {join_token!r} calls in source"
        )

    def test_no_inline_re_sub_or_re_match(self, tmp_path) -> None:
        """``re.match`` directly, it must use the precompiled"""
        src = _apply_to_text_source()
        assert "_token_key(" in src, "apply_to_text must use text_cleanup's memoized _token_key normalizer"
        assert "_RE_MISSPELL_WRAP" in src, "apply_to_text must import _RE_MISSPELL_WRAP"
        assert "re.sub(" not in src, (
            "apply_to_text must use the precompiled/memoized text_cleanup helpers, "
            "not re.sub(), re.sub incurs a per-call re-cache lookup"
        )
        assert "re.match(" not in src, "apply_to_text must use _RE_MISSPELL_WRAP.match(), not re.match()"

        # Dynamic check: patch re.sub / re.match, run apply_to_text,
        import re

        vm = _make_vocab(tmp_path)
        vm.add_entry("misspellings", "teh", "the")
        vm.add_entry("technical_terms", "pyathon", "Python")

        call_count = {"sub": 0, "match": 0}
        real_sub = re.sub
        real_match = re.match

        def counting_sub(*args, **kwargs):
            call_count["sub"] += 1
            return real_sub(*args, **kwargs)

        def counting_match(*args, **kwargs):
            call_count["match"] += 1
            return real_match(*args, **kwargs)

        with patch("re.sub", side_effect=counting_sub), patch("re.match", side_effect=counting_match):
            vm.apply_to_text("I teh pyathon")
        assert call_count["sub"] == 0, (
            f"apply_to_text must use precompiled patterns, not re.sub; got {call_count['sub']} re.sub calls"
        )
        assert call_count["match"] == 0, (
            f"apply_to_text must use precompiled patterns, not re.match; got {call_count['match']} re.match calls"
        )


class TestNoSnapshotOverAllocation:
    """``apply_to_text`` must NOT allocate full copies of all 6"""

    def test_no_full_dict_snapshot_in_source(self) -> None:
        src = _apply_to_text_source()
        # Pre-fix: ``{cat: (list(v) if isinstance(v, list) else dict(v))
        assert "dict(v)" not in src, (
            "apply_to_text must not snapshot dict-based categories via dict(v); "
            "dict.get(key) is GIL-atomic and safe on live self._data"
        )
        assert "list(v)" not in src, (
            "apply_to_text must not snapshot list-based categories via list(v); "
            "phrase-level categories are handled by _get_compiled_patterns"
        )

    def test_empty_category_skipped(self, tmp_path) -> None:
        """Empty categories must be skipped via"""
        vm = _make_vocab(tmp_path)
        # Only misspellings has entries; names/products/technical_terms
        vm.add_entry("misspellings", "teh", "the")
        # No entries in technical_terms / names / products.

        # Must still apply misspellings correctly.
        result = vm.apply_to_text("I went teh wrong way")
        assert result == "I went the wrong way"

        # And the empty categories must not raise or alter behavior.
        result2 = vm.apply_to_text("nothing to correct here")
        assert result2 == "nothing to correct here"


class TestSequentialSemanticsPreserved:
    """Regression guard: the single-tokenization-pass rewrite"""

    def test_chained_correction_across_categories(self, tmp_path) -> None:
        """If misspellings maps ``teh`` → ``pyathon`` and technical_terms"""
        vm = _make_vocab(tmp_path)
        vm.add_entry("misspellings", "teh", "pyathon")
        vm.add_entry("technical_terms", "pyathon", "Python")

        result = vm.apply_to_text("teh")
        assert result == "Python", (
            f"sequential category semantics broken: 'teh' should chain through "
            f"misspellings→'pyathon'→technical_terms→'Python'; got {result!r}"
        )

    def test_punctuation_preserved(self, tmp_path) -> None:
        """Punctuation around the corrected token must be preserved"""
        vm = _make_vocab(tmp_path)
        vm.add_entry("misspellings", "teh", "the")

        # Leading + trailing punctuation must survive the correction.
        result = vm.apply_to_text("I said, 'teh' wrong way.")
        assert result == "I said, 'the' wrong way.", f"punctuation wrapping broken; got {result!r}"


class TestCombinedAlternationPhrasePass:
    """Phrase-level categories apply in ONE combined-alternation"""

    def test_single_subn_call_site_in_source(self) -> None:
        src = _apply_to_text_source()
        assert src.count("subn(") == 1, (
            "apply_to_text must run exactly ONE combined subn pass (per phrase category invocation), not one per entry"
        )
        assert "for pattern, good, original in compiled" not in src, "the per-entry full-text scan loop must not return"

    def test_no_intra_category_cascade(self, tmp_path) -> None:
        vm = _make_vocab(tmp_path)
        vm.add_phrase("phrase_corrections", "a b", "b c")
        vm.add_phrase("phrase_corrections", "b c", "x")
        # Old per-entry loop: "a b" → "b c" → (rescan) → "x". Single
        result = vm.apply_to_text("a b")
        assert result == "b c", f"intra-category cascade reintroduced; got {result!r}, expected 'b c'"

    def test_cross_category_chaining_still_applies(self, tmp_path) -> None:
        vm = _make_vocab(tmp_path)
        vm.add_phrase("phrase_corrections", "color qupx", "special thing")
        vm.add_phrase("extra_word_patterns", "special thing", "ultimate special thing")
        result = vm.apply_to_text("color qupx")
        assert result == "ultimate special thing", f"cross-category chaining broken; got {result!r}"

    def test_usage_tracking_counts_each_phrase(self, tmp_path) -> None:
        vm = _make_vocab(tmp_path)
        vm.add_phrase("phrase_corrections", "to 2", "to")
        vm.add_phrase("phrase_corrections", "for free", "free")
        out = vm.apply_to_text("to 2, twice, to 2 again and for free")
        assert out == "to, twice, to again and free"
        snap = vm.usage_tracker.get_snapshot()
        assert snap["entries"]["phrase_corrections"]["to 2"]["count"] == 2
        assert snap["entries"]["phrase_corrections"]["for free"]["count"] == 1

    def test_longer_phrase_wins_on_overlap(self, tmp_path) -> None:
        vm = _make_vocab(tmp_path)
        vm.add_phrase("phrase_corrections", "new york", "NYC")
        vm.add_phrase("phrase_corrections", "new york city", "New York City")
        result = vm.apply_to_text("I love new york city")
        assert result == "I love New York City", f"longer-first alternation broken; got {result!r}"

    def test_duplicate_original_case_insensitive_does_not_crash(self, tmp_path) -> None:
        vm = _make_vocab(tmp_path)
        vm.add_phrase("phrase_corrections", "Bad Phrase", "fix one")
        vm.add_phrase("phrase_corrections", "bad phrase", "fix two")
        result = vm.apply_to_text("A Bad Phrase here")
        assert result == "A fix one here"

    def test_case_insensitive_match_preserves_replacement_casing(self, tmp_path) -> None:
        vm = _make_vocab(tmp_path)
        vm.add_phrase("phrase_corrections", "hello world", "goodbye world")
        result = vm.apply_to_text("say HELLO WORLD loudly")
        assert result == "say goodbye world loudly"
