"""Equivalence tests for the ASCII fast path in the language filter."""

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
        """For all 128 ASCII code points, ``is_latin_char(ch)`` is False"""
        for cp in range(0x80):
            ch = chr(cp)
            expected = cp not in _ASCII_NON_LATIN_TRANSLATION
            assert _is_latin_char(ch) is expected, (
                f"U+{cp:04X} {ch!r}: is_latin_char={_is_latin_char(ch)}, fast-path model expects Latin={expected}"
            )

    def test_delete_table_covers_exactly_the_33_control_chars(self) -> None:
        """The table is U+0000–U+001F plus DEL, nothing more, nothing less."""
        assert set(_ASCII_NON_LATIN_TRANSLATION) == set(range(0x20)) | {0x7F}

    def test_ratio_limit_matches_canonical_constant(self) -> None:
        """The fast path compares against the threshold the canonical"""
        from voice_typer.server.asr_utils import NON_LATIN_RATIO_LIMIT

        assert _LIKELY_ENGLISH_RATIO_LIMIT == NON_LATIN_RATIO_LIMIT


class TestFastPathEquivalence:
    """The wrapped filter must agree with the canonical implementation."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # Pure-ASCII English, hits the fast path.
            ("Hello world, this is a test.", True),
            ("The year is 2026 and the temperature is 23.5 degrees.", True),
            ("Hello, world! How are you? (I'm fine.)", True),
            # Empty / whitespace-only, canonical strip() short-circuit.
            ("", True),
            ("   ", True),
            ("\t\n", True),
            # Pure CJK, non-ASCII, delegates to the canonical loop, rejects.
            ("你好世界", False),
            # Mixed below / above the 30% threshold.
            ("Hello 你 world", True),
            ("你好abc", False),
            # Boundary: exactly 30% non-Latin → canonical check is ``>`` → True.
            ("abcdefghi你好世", True),
            # Just above the boundary (36% non-Latin) → False.
            ("abcdefghi你好世界", False),
            # Non-ASCII but Latin-script (é), delegates, accepts.
            ("café au lait", True),
            # Emoji / symbols, symbol category counts as Latin.
            ("great 👍 work", True),
            # ASCII boundary cases driven by CONTROL characters (the only
            ("abcdefg\n\t\x00", True),
            # 4/11 ≈ 36% → above the limit → delegates to the canonical
            ("abcdefg\n\t\x00\x01", False),
            # All-control ASCII → 100% non-Latin → reject.
            ("\x00\x01\x02\x03", False),
        ],
    )
    def test_matches_canonical_impl(self, text: str, expected: bool) -> None:
        assert _is_likely_english(text) is expected, f"text={text!r}"
        # And the wrapper agrees with the canonical implementation —
        assert _is_likely_english(text) is _is_likely_english_impl(text), f"text={text!r}"

    def test_exhaustive_ascii_single_chars_agree_with_impl(self) -> None:
        """Every single-character ASCII string gets the same verdict from"""
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
        """Every REJECT decision (and therefore the PII-safe hallucination"""
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
        assert _is_likely_english("abcdefg\n\t\x00\x01") is False
        assert delegated[-1] == "abcdefg\n\t\x00\x01"
