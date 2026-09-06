"""Tests for the ``VocabularyManager.apply_to_text`` perf fixes.

* Pre-fix, the 4 word-level category loop called
  ``text.split(" ")`` and ``" ".join(output)`` INSIDE the loop body,
  so a 50-word transcript was split + joined 4 times per dictation.
  Inline ``re.sub(r"^\\W+|\\W+$", "")`` / ``re.match(r"^(\\W*)(\\w+)(\\W*)$")``
  calls also incurred ~200 re-cache lookups per dictation. Fix:
  precompile the two regexes (imported from text_cleanup) and
  tokenize once across all 4 categories.

* Pre-fix, ``apply_to_text`` snapshotted ALL 6 categories
  (``{cat: (list(v) if isinstance(v, list) else dict(v)) for cat, v in self._data.items()}``)
  on every invocation, allocating 6 new containers with up to 5000
  reference-copies each per dictation. Only the 4 dict-based
  categories are actually read; the 2 list-based categories are
  handled by ``_get_combined_phrase_pattern`` (which locks separately).
  Fix: snapshot only the 4 dict references (not contents) under the
  lock; per-token ``dict.get(key)`` lookups are GIL-atomic and safe
  on the live ``self._data`` dicts.
"""

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
    """Return the source code of ``VocabularyManager.apply_to_text``
    as a single string (for static-assertion tests)."""
    from voice_typer.server.vocabulary import VocabularyManager

    return inspect.getsource(VocabularyManager.apply_to_text)


class TestSingleTokenizationPass:
    """``str.split`` + ``" ".join`` run at most once per
    ``apply_to_text`` call (not 4 times — once per word-level
    category).

    Built-in ``str.split`` / ``str.join`` cannot be monkeypatched
    (immutable type), so the assertion is a static source check:
    the body of ``apply_to_text`` must contain exactly ONE
    ``text.split(" ")`` call and exactly ONE ``" ".join(...)`` call.
    """

    def test_single_split_call_in_source(self) -> None:
        src = _apply_to_text_source()
        # Pre-fix: 4 occurrences (one per word-level category in the
        # loop body). Post-fix: exactly 1 (single tokenization pass).
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
        """The word-level path must NOT call ``re.sub`` or
        ``re.match`` directly — it must use the precompiled
        ``_RE_TOKEN_KEY`` / ``_RE_MISSPELL_WRAP`` patterns imported
        from text_cleanup. Inline ``re.sub``/``re.match`` incur a
        per-call re-cache lookup (200 lookups/dictation for 50 words ×
        4 categories).

        Static source check + dynamic behavioral check (patch re.sub /
        re.match; ensure neither is called).
        """
        src = _apply_to_text_source()
        assert "_RE_TOKEN_KEY" in src, "apply_to_text must import _RE_TOKEN_KEY"
        assert "_RE_MISSPELL_WRAP" in src, "apply_to_text must import _RE_MISSPELL_WRAP"
        # No bare re.sub / re.match calls in the source — the
        # precompiled patterns' .sub() / .match() methods are used
        # instead (no re-cache lookup).
        assert "re.sub(" not in src, (
            "apply_to_text must use _RE_TOKEN_KEY.sub(), not re.sub() — re.sub incurs a per-call re-cache lookup"
        )
        assert "re.match(" not in src, "apply_to_text must use _RE_MISSPELL_WRAP.match(), not re.match()"

        # Dynamic check: patch re.sub / re.match, run apply_to_text,
        # verify neither is called.
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
    """``apply_to_text`` must NOT allocate full copies of all 6
    categories. Only the 4 dict-based category references are captured
    (no entry-level copies).

    Static source check: the body must not contain ``dict(v)`` or
    ``list(v)`` (the pre-fix snapshot comprehension pattern) and must
    not call ``.copy()`` on self._data values.
    """

    def test_no_full_dict_snapshot_in_source(self) -> None:
        src = _apply_to_text_source()
        # Pre-fix: ``{cat: (list(v) if isinstance(v, list) else dict(v))
        # for cat, v in self._data.items()}`` allocates a full copy of
        # every category. Post-fix: only dict references are captured.
        assert "dict(v)" not in src, (
            "apply_to_text must not snapshot dict-based categories via dict(v); "
            "dict.get(key) is GIL-atomic and safe on live self._data"
        )
        assert "list(v)" not in src, (
            "apply_to_text must not snapshot list-based categories via list(v); "
            "phrase-level categories are handled by _get_compiled_patterns"
        )

    def test_empty_category_skipped(self, tmp_path) -> None:
        """Empty categories must be skipped via
        ``if not entries: continue`` — no per-token lookup overhead
        for categories with no entries (common for the bundled
        defaults where ``names`` and ``products`` are typically empty)."""
        vm = _make_vocab(tmp_path)
        # Only misspellings has entries; names/products/technical_terms
        # are empty.
        vm.add_entry("misspellings", "teh", "the")
        # No entries in technical_terms / names / products.

        # Must still apply misspellings correctly.
        result = vm.apply_to_text("I went teh wrong way")
        assert result == "I went the wrong way"

        # And the empty categories must not raise or alter behavior.
        result2 = vm.apply_to_text("nothing to correct here")
        assert result2 == "nothing to correct here"


class TestSequentialSemanticsPreserved:
    """Regression guard: the single-tokenization-pass rewrite
    must preserve the original sequential semantics — a misspelling
    corrected to a term that's then in technical_terms is further
    corrected by the technical_terms pass."""

    def test_chained_correction_across_categories(self, tmp_path) -> None:
        """If misspellings maps ``teh`` → ``pyathon`` and technical_terms
        maps ``pyathon`` → ``Python``, ``apply_to_text("teh")`` must
        yield ``Python`` (the second pass sees the first pass's
        output)."""
        vm = _make_vocab(tmp_path)
        vm.add_entry("misspellings", "teh", "pyathon")
        vm.add_entry("technical_terms", "pyathon", "Python")

        result = vm.apply_to_text("teh")
        assert result == "Python", (
            f"sequential category semantics broken: 'teh' should chain through "
            f"misspellings→'pyathon'→technical_terms→'Python'; got {result!r}"
        )

    def test_punctuation_preserved(self, tmp_path) -> None:
        """Punctuation around the corrected token must be preserved
        (the ``_RE_MISSPELL_WRAP`` regex wraps the correction with the
        original leading/trailing non-word chars)."""
        vm = _make_vocab(tmp_path)
        vm.add_entry("misspellings", "teh", "the")

        # Leading + trailing punctuation must survive the correction.
        result = vm.apply_to_text("I said, 'teh' wrong way.")
        assert result == "I said, 'the' wrong way.", f"punctuation wrapping broken; got {result!r}"


class TestCombinedAlternationPhrasePass:
    """Phrase-level categories apply in ONE combined-alternation
    ``subn`` pass per category (the text_cleanup design), replacing the
    per-entry ``pattern.subn`` full-text loop (M entries = M full-text
    scans).

    Pinned contracts:
    * one ``subn`` call site in ``apply_to_text`` (no per-entry loop);
    * no intra-category cascade (a replacement that introduces a
      sibling entry's original is NOT re-substituted — single pass
      scans the original text);
    * cross-category order preserved (phrase_corrections before
      extra_word_patterns, then the word-level dict pass);
    * usage tracking still records per-phrase counts.
    """

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
        # pass: the second entry never sees the first entry's output.
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
        # Longest-first sort makes the two length-equal entries
        # order-stable; the dedup keeps the first. Either way the apply
        # pass must not raise and must produce a consistent result.
        result = vm.apply_to_text("A Bad Phrase here")
        assert result == "A fix one here"

    def test_case_insensitive_match_preserves_replacement_casing(self, tmp_path) -> None:
        vm = _make_vocab(tmp_path)
        vm.add_phrase("phrase_corrections", "hello world", "goodbye world")
        result = vm.apply_to_text("say HELLO WORLD loudly")
        assert result == "say goodbye world loudly"
