"""Equivalence tests for the ASCII fast path in the language filter.

``voice_typer.server.parakeet_engine._helpers._is_likely_english`` wraps
the canonical :func:`asr_utils.is_likely_english` with an ASCII-only
fast path: pure-ASCII text whose control-character ratio is at or below
``NON_LATIN_RATIO_LIMIT`` returns ``True`` without the per-character
``unicodedata`` loop (every ASCII code point classifies as Latin /
punct / symbol / separator / digit EXCEPT the 33 Cc control characters,
so the non-Latin count for ASCII text is just the control count, which
``str.translate`` computes at C speed).

These tests pin the two contracts that make the fast path safe:

1. **ASCII classification invariant** — the fast path's model of "which
   ASCII characters are non-Latin" matches the canonical
   ``is_latin_char`` for ALL 128 ASCII code points.
2. **End-to-end equivalence** — the wrapped filter returns the SAME
   verdict as the canonical implementation for ASCII / CJK / mixed /
   boundary samples, and every REJECT decision (plus its PII-safe
   hallucination logging) still flows through the canonical
   implementation.
"""

from __future__ import annotations

import pytest
from voice_typer.server.parakeet_engine._helpers import (
    _ASCII_NON_LATIN_TRANSLATION,
    _LIKELY_ENGLISH_RATIO_LIMIT,
    _is_latin_char,
    _is_likely_english,
    _is_likely_english_impl,
)


class TestAsciiClassificationInvariant:
    """The fast path assumes: ASCII ⟹ Latin except the Cc control block."""

    def test_every_ascii_char_classifies_as_expected(self) -> None:
        """For all 128 ASCII code points, ``is_latin_char(ch)`` is False
        exactly for the code points in the fast path's delete table.

        If a future Python changes the ``unicodedata`` classification of
        any ASCII character (e.g. a control character reclassified as a
        separator), this test fails BEFORE the fast path can silently
        diverge from the canonical filter.
        """
        for cp in range(0x80):
            ch = chr(cp)
            expected = cp not in _ASCII_NON_LATIN_TRANSLATION
            assert _is_latin_char(ch) is expected, (
                f"U+{cp:04X} {ch!r}: is_latin_char={_is_latin_char(ch)}, fast-path model expects Latin={expected}"
            )

    def test_delete_table_covers_exactly_the_33_control_chars(self) -> None:
        """The table is U+0000–U+001F plus DEL — nothing more, nothing less."""
        assert set(_ASCII_NON_LATIN_TRANSLATION) == set(range(0x20)) | {0x7F}

    def test_ratio_limit_matches_canonical_constant(self) -> None:
        """The fast path compares against the threshold the canonical
        implementation actually uses (same float → ``<=`` is the exact
        complement of the canonical ``>`` check)."""
        from voice_typer.server.asr_utils import NON_LATIN_RATIO_LIMIT

        assert _LIKELY_ENGLISH_RATIO_LIMIT == NON_LATIN_RATIO_LIMIT


class TestFastPathEquivalence:
    """The wrapped filter must agree with the canonical implementation."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # Pure-ASCII English — hits the fast path.
            ("Hello world, this is a test.", True),
            ("The year is 2026 and the temperature is 23.5 degrees.", True),
            ("Hello, world! How are you? (I'm fine.)", True),
            # Empty / whitespace-only — canonical strip() short-circuit.
            ("", True),
            ("   ", True),
            ("\t\n", True),
            # Pure CJK — non-ASCII, delegates to the canonical loop, rejects.
            ("你好世界", False),
            # Mixed below / above the 30% threshold.
            ("Hello 你 world", True),
            ("你好abc", False),
            # Boundary: exactly 30% non-Latin → canonical check is ``>`` → True.
            ("abcdefghi你好世", True),
            # Just above the boundary (36% non-Latin) → False.
            ("abcdefghi你好世界", False),
            # Non-ASCII but Latin-script (é) — delegates, accepts.
            ("café au lait", True),
            # Emoji / symbols — symbol category counts as Latin.
            ("great 👍 work", True),
            # ASCII boundary cases driven by CONTROL characters (the only
            # non-Latin ASCII chars): 3/10 = 30% exactly → True (fast path).
            ("abcdefg\n\t\x00", True),
            # 4/11 ≈ 36% → above the limit → delegates to the canonical
            # implementation, which rejects.
            ("abcdefg\n\t\x00\x01", False),
            # All-control ASCII → 100% non-Latin → reject.
            ("\x00\x01\x02\x03", False),
        ],
    )
    def test_matches_canonical_impl(self, text: str, expected: bool) -> None:
        assert _is_likely_english(text) is expected, f"text={text!r}"
        # And the wrapper agrees with the canonical implementation —
        # the fast path may only short-circuit verdicts the canonical
        # implementation would also produce.
        assert _is_likely_english(text) is _is_likely_english_impl(text), f"text={text!r}"

    def test_exhaustive_ascii_single_chars_agree_with_impl(self) -> None:
        """Every single-character ASCII string gets the same verdict from
        the wrapper and the canonical implementation."""
        for cp in range(0x80):
            text = chr(cp)
            assert _is_likely_english(text) is _is_likely_english_impl(text), f"U+{cp:04X}"

    def test_fast_path_skips_impl_for_plain_english(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Plain-ASCII English must NOT reach the canonical implementation."""
        calls: list[str] = []

        def _fail_impl(text: str) -> bool:
            calls.append(text)
            return False

        monkeypatch.setattr(
            "voice_typer.server.parakeet_engine._helpers._is_likely_english_impl",
            _fail_impl,
        )
        assert _is_likely_english("Hello world, this is a test.") is True
        assert calls == [], "ASCII fast path must short-circuit without the canonical impl"

    def test_rejection_still_delegates_to_impl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every REJECT decision (and therefore the PII-safe hallucination
        logging inside the canonical implementation) still flows through
        the canonical implementation."""
        delegated: list[str] = []

        def _spy_impl(text: str) -> bool:
            delegated.append(text)
            return _is_likely_english_impl(text)

        monkeypatch.setattr(
            "voice_typer.server.parakeet_engine._helpers._is_likely_english_impl",
            _spy_impl,
        )
        # CJK-heavy text → reject via the canonical path.
        assert _is_likely_english("你好世界") is False
        assert delegated == ["你好世界"], "rejection must be decided by the canonical impl"

        # Control-character-heavy ASCII (above the limit) → also rejected
        # via the canonical path, exercising the ASCII-delegation branch.
        assert _is_likely_english("abcdefg\n\t\x00\x01") is False
        assert delegated[-1] == "abcdefg\n\t\x00\x01"
