"""Tests for voice_typer.server.ai_enhancement — P4 AI grammar/punctuation/capitalization.

These tests cover the four public functions:
  * auto_capitalize
  * auto_punctuate
  * fix_grammar_basics
  * enhance_transcription (dispatcher)

The dispatcher tests use a real ``Config`` instance so they also
verify the config fields are present and default correctly.
"""

from __future__ import annotations

from voice_typer.server.ai_enhancement import (
    auto_capitalize,
    auto_punctuate,
    enhance_transcription,
    fix_grammar_basics,
)
from voice_typer.server.config import Config

# ─── auto_capitalize ────────────────────────────────────────────────────────


class TestAutoCapitalize:
    def test_auto_capitalize_capitalizes_sentence_start(self):
        """The first letter of the text should be capitalized."""
        result = auto_capitalize("hello world")
        assert result == "Hello world"

    def test_auto_capitalize_handles_existing_capitalization(self):
        """Already-capitalized text should be left unchanged."""
        result = auto_capitalize("Hello World")
        assert result == "Hello World"

    def test_auto_capitalize_after_sentence_boundary(self):
        """Letters following `. `, `! `, `? ` should be capitalized."""
        result = auto_capitalize("hello world. this is a test")
        assert result == "Hello world. This is a test"

        result = auto_capitalize("wow! that is great")
        assert result == "Wow! That is great"

        result = auto_capitalize("what? i don't know")
        assert result == "What? I don't know"

    def test_auto_capitalize_preserves_leading_punctuation(self):
        """Opening quotes / parens before the first letter should be kept."""
        result = auto_capitalize('"hello world"')
        assert result == '"Hello world"'

        result = auto_capitalize("(hello world)")
        assert result == "(Hello world)"

    def test_auto_capitalize_proper_nouns(self):
        """Weekday / month names should be capitalized."""
        result = auto_capitalize("i will see you on monday")
        assert result == "I will see you on Monday"

        result = auto_capitalize("my birthday is in july")
        assert result == "My birthday is in July"

    def test_auto_capitalize_empty_string(self):
        """Empty input should return empty output."""
        assert auto_capitalize("") == ""

    def test_auto_capitalize_idempotent(self):
        """Running twice should produce the same output as running once."""
        text = "hello world. this is a test. i like monday"
        once = auto_capitalize(text)
        twice = auto_capitalize(once)
        assert once == twice


# ─── auto_punctuate ─────────────────────────────────────────────────────────


class TestAutoPunctuate:
    def test_auto_punctuate_adds_periods(self):
        """A sentence with no terminal punctuation should get a period."""
        result = auto_punctuate("this is a longer sentence")
        assert result.endswith(".")

    def test_auto_punctuate_preserves_existing_punctuation(self):
        """Text that already ends with `.`, `!`, or `?` should not be re-punctuated."""
        assert auto_punctuate("this is a sentence.") == "this is a sentence."
        assert auto_punctuate("wow!") == "wow!"
        assert auto_punctuate("what?") == "what?"

    def test_auto_punctuate_short_text_no_period(self):
        """Very short text (<=3 words) should not get terminal punctuation.

        This mirrors the existing ``_add_safe_terminal_punctuation``
        heuristic: short text is likely a fragment, not a sentence.
        """
        result = auto_punctuate("hello world")
        assert result == "hello world"

    def test_auto_punctuate_question_mark_for_questions(self):
        """Question-shaped sentences should get a question mark."""
        result = auto_punctuate("can you help me with this")
        assert result.endswith("?")

    def test_auto_punctuate_skips_urls(self):
        """URLs should not get terminal punctuation."""
        result = auto_punctuate("https://example.com this is a very long url string here")
        # The URL safety pattern should match — no punctuation added.
        assert not result.endswith(".")

    def test_auto_punctuate_empty_string(self):
        """Empty input should return empty output."""
        assert auto_punctuate("") == ""

    def test_auto_punctuate_inserts_comma_at_conjunction_break(self):
        """`X and I ...` should get a comma before the conjunction."""
        result = auto_punctuate("i went to the store and i bought milk")
        assert ", and" in result

    def test_auto_punctuate_no_comma_for_simple_list(self):
        """`apples and oranges` (no pronoun after `and`) should not get a comma."""
        result = auto_punctuate("i bought apples and oranges at the store today")
        assert ", and" not in result


# ─── fix_grammar_basics ────────────────────────────────────────────────────


class TestFixGrammarBasics:
    def test_fix_grammar_basics_fixes_i(self):
        """The bare pronoun `i` should be capitalized to `I`."""
        result = fix_grammar_basics("i went to the store")
        assert result == "I went to the store"

    def test_fix_grammar_basics_fixes_contractions(self):
        """Common contractions should get their apostrophe back."""
        result = fix_grammar_basics("i dont know what you cant do")
        assert "don't" in result
        assert "can't" in result
        assert "I " in result  # pronoun also fixed

    def test_fix_grammar_basics_fixes_double_spaces(self):
        """Runs of 2+ spaces should be collapsed to one."""
        result = fix_grammar_basics("hello  world   with    spaces")
        assert result == "hello world with spaces"

    def test_fix_grammar_basics_preserves_word_boundaries(self):
        """`i` inside another word should NOT be touched."""
        result = fix_grammar_basics("input implicit")
        assert result == "input implicit"

    def test_fix_grammar_basics_preserves_case_pattern(self):
        """Title-case contractions should remain title-case."""
        result = fix_grammar_basics("Dont Dont")
        # Both halves should be "Don't" (title-case preservation).
        assert result == "Don't Don't"

    def test_fix_grammar_basics_empty_string(self):
        """Empty input should return empty output."""
        assert fix_grammar_basics("") == ""

    def test_fix_grammar_basics_no_apostrophe_i(self):
        """`i` after an apostrophe (e.g. in a contraction we just fixed) should not be re-capitalized."""
        # "don't" contains `t` after `'` — the regex's negative
        # lookbehind for `[A-Za-z']` prevents us from matching the
        # `t` or any letter adjacent to an apostrophe.
        result = fix_grammar_basics("don't i know you")
        # The standalone `i` (between spaces) should be capitalized.
        assert "I" in result


# ─── enhance_transcription (dispatcher) ────────────────────────────────────


class TestEnhanceTranscription:
    def test_enhance_transcription_respects_disabled_flags(self):
        """When the master toggle is OFF, the text should be returned unchanged."""
        cfg = Config()
        # Sanity-check the defaults.
        assert cfg.ai_enhancement_enabled is False

        text = "i cant dont wont"
        result = enhance_transcription(text, cfg)
        assert result == text  # unchanged

    def test_enhance_transcription_applies_enabled_flags(self):
        """When the master toggle is ON, the enabled sub-features should fire."""
        cfg = Config()
        cfg.ai_enhancement_enabled = True
        # All three sub-toggles default to True.

        text = "i cant dont wont"
        result = enhance_transcription(text, cfg)
        # Grammar fixes (i → I, cant → can't, dont → don't, wont → won't)
        # plus terminal punctuation (the result is now 4 "words"
        # counting the contractions as one word each, so a period is
        # added by auto_punctuate).
        assert "I" in result
        assert "can't" in result
        assert "don't" in result
        assert "won't" in result
        assert result.endswith(".")

    def test_enhance_transcription_sub_toggle_disables_grammar(self):
        """When fix_grammar_basics is OFF, contractions should NOT be fixed."""
        cfg = Config()
        cfg.ai_enhancement_enabled = True
        cfg.fix_grammar_basics = False

        text = "i dont know"
        result = enhance_transcription(text, cfg)
        # Grammar is off — `dont` stays `dont`, `i` stays `i`.
        assert "dont" in result
        # But auto_capitalize still ran (capitalized the first letter).
        assert result.startswith("I")

    def test_enhance_transcription_sub_toggle_disables_punctuate(self):
        """When auto_punctuate is OFF, no terminal punctuation should be added."""
        cfg = Config()
        cfg.ai_enhancement_enabled = True
        cfg.auto_punctuate = False

        text = "this is a longer sentence"
        result = enhance_transcription(text, cfg)
        assert not result.endswith(".")
        assert not result.endswith("?")

    def test_enhance_transcription_sub_toggle_disables_capitalize(self):
        """When auto_capitalize is OFF, sentence starts should NOT be capitalized."""
        cfg = Config()
        cfg.ai_enhancement_enabled = True
        cfg.auto_capitalize = False
        # auto_punctuate still runs, so a period gets added.
        text = "hello world this is a test sentence"
        result = enhance_transcription(text, cfg)
        # First letter should NOT be capitalized.
        assert result.startswith("h")

    def test_enhance_transcription_empty_string(self):
        """Empty input should return empty output regardless of toggles."""
        cfg = Config()
        cfg.ai_enhancement_enabled = True
        assert enhance_transcription("", cfg) == ""

    def test_enhance_transcription_full_sentence(self):
        """End-to-end: a typical transcription should come out polished."""
        cfg = Config()
        cfg.ai_enhancement_enabled = True

        text = "i went to the store and i bought milk"
        result = enhance_transcription(text, cfg)
        # Capitalized first letter.
        assert result.startswith("I")
        # Comma before the conjunction (subject + pronoun pattern).
        assert ", and" in result
        # Terminal period.
        assert result.endswith(".")

    def test_enhance_transcription_does_not_break_url(self):
        """URLs should not get mangled by the enhancement pass."""
        cfg = Config()
        cfg.ai_enhancement_enabled = True

        text = "https://example.com is a great website"
        result = enhance_transcription(text, cfg)
        # The URL should still be intact.
        assert "https://example.com" in result
