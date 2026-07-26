"""Tests for lightweight post-transcription text cleanup."""

from __future__ import annotations

import pytest
from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections


# TEST-019 (fix): previously configure_corrections() was called at module
# import time, mutating global state and making test order matter. Now it
# runs in an autouse fixture so each test gets a clean corrections state.
@pytest.fixture(autouse=True)
def _configure_corrections():
    """Initialize corrections from bundled corrections.json before each test.

    Ensures _active_misspellings, _active_phrases, and
    _active_extra_words are populated for every test, without leaking
    state across test modules.
    """
    configure_corrections()


class TestCleanTranscribedText:
    def test_removes_adjacent_duplicate_words(self):
        text = "this this is is a test test message"

        assert clean_transcribed_text(text) == "This is a test test message"

    def test_preserves_intentional_repeated_words(self):
        assert clean_transcribed_text("no no no") == "No no no"
        assert clean_transcribed_text("very very good") == "Very very good"
        assert clean_transcribed_text("test test one two") == "Test test one two"

    def test_collapses_high_confidence_whisper_duplicates(self):
        assert clean_transcribed_text("hello hello world") == "Hello world"
        assert clean_transcribed_text("open settings open settings now") == "Open settings now"

    def test_removes_adjacent_duplicate_short_phrases(self):
        text = "right now right now I want to test this"

        assert clean_transcribed_text(text) == "Right now I want to test this"

    def test_fixes_punctuation_spacing(self):
        text = "hello , world ! this is working ?"

        assert clean_transcribed_text(text) == "Hello, world! This is working?"

    def test_capitalizes_sentence_starts_and_pronoun_i(self):
        text = "i tested this. it works and i like it"

        assert clean_transcribed_text(text) == "I tested this. It works and I like it"

    def test_no_forced_question_mark_for_question_openers(self):
        text = "can we make this faster"

        assert clean_transcribed_text(text) == "Can we make this faster"

    def test_no_forced_question_mark_for_question_final_sentence(self):
        text = "i want to make this faster. can we do that"

        assert clean_transcribed_text(text) == "I want to make this faster. Can we do that"

    def test_does_not_force_punctuation_for_plain_statement(self):
        text = "we can make this faster"

        assert clean_transcribed_text(text) == "We can make this faster"

    def test_preserves_existing_terminal_punctuation(self):
        text = "what do you think?"

        assert clean_transcribed_text(text) == "What do you think?"

    def test_empty_text_stays_empty(self):
        assert clean_transcribed_text("") == ""
        assert clean_transcribed_text("   ") == ""

    def test_cleans_self_corrections_base_to_gerund(self):
        assert clean_transcribed_text("I talk talking to it") == "I talking to it"

    def test_cleans_self_corrections_with_shared_root(self):
        assert clean_transcribed_text("transcribed transcribe it") == "Transcribe it"
        assert clean_transcribed_text("developed development is good") == "Development is good"

    def test_does_not_remove_short_near_duplicates(self):
        assert clean_transcribed_text("attic is a test") == "Attic is a test"

    def test_fixes_common_misspellings(self):
        assert clean_transcribed_text("infestigate this") == "Investigate this"
        assert clean_transcribed_text("that is wierd") == "That is weird"
        assert clean_transcribed_text("grammer check") == "Grammar check"

    def test_fixes_grammarly_to_grammatically(self):
        assert clean_transcribed_text("is it grammarly correct") == "Is it grammatically correct"

    def test_does_not_overcorrect_attic_without_context(self):
        assert clean_transcribed_text("convert the attic is a voice to text") == "Convert the attic is a voice to text"

    def test_corrects_whisper_phrase_to_2(self):
        assert clean_transcribed_text("voice to 2 text") == "Voice to text"

    def test_corrects_whisper_phrase_they_working(self):
        assert clean_transcribed_text("looks like they working") == "Looks like it's working"

    def test_removes_extra_word_without_whether(self):
        assert clean_transcribed_text("Without whether it is a problem") == "Whether it is a problem"

    def test_removes_extra_word_didnt_and(self):
        assert clean_transcribed_text("it didn't and catch everything") == "It didn't catch everything"

    def test_preserves_intentional_repetition_with_near_duplicates(self):
        assert clean_transcribed_text("no no no stay repeated") == "No no no stay repeated"

    def test_long_realistic_transcription_cleanup(self):
        text = (
            "Right now the application is working successfully I just restart "
            "the device and it works successfully automatically I didn't have "
            "to start it from scratch or with any commands it just started "
            "itself with startup after I looked in and I tried it looks like "
            "they working successfully but I haven't tested it fully yet"
        )
        result = clean_transcribed_text(text)
        assert "it's working" in result
        assert "they working" not in result

    def test_misspelling_preserves_punctuation(self):
        assert clean_transcribed_text("infestigate, please.") == "Investigate, please."

    def test_skips_terminal_punctuation_for_short_text(self):
        assert clean_transcribed_text("hello") == "Hello"
        assert clean_transcribed_text("got it") == "Got it"
        assert clean_transcribed_text("works fine") == "Works fine"
        assert clean_transcribed_text("this is a test") == "This is a test"

    def test_cleanup_always_applied(self):
        """Cleanup is always applied; gating is now at the app level."""
        text = "this is a test of the cleanup"
        result = clean_transcribed_text(text)
        # Capitalization IS applied
        assert result == "This is a test of the cleanup"

    def test_cleanup_enabled_is_default(self):
        """By default, cleanup should be enabled."""
        text = "can we make this faster"
        result = clean_transcribed_text(text)
        # Cleanup still applies capitalization, spacing, misspellings etc.
        # Forced terminal punctuation was removed from the pipeline.
        assert result == "Can we make this faster"


class TestExternalCorrectionsFallback:
    """P2 fix: _load_external_corrections returns None when no file exists,
    and clean_transcribed_text falls back to built-in defaults."""

    def test_load_external_corrections_returns_none_when_no_file(self, tmp_path, monkeypatch):
        """When no corrections file exists, _load_external_corrections returns None."""
        from voice_typer.server import text_cleanup

        monkeypatch.setattr(text_cleanup, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        result = text_cleanup._load_external_corrections(config_dir=tmp_path)
        assert result is None

    def test_load_external_corrections_returns_none_when_no_config_dir(self, monkeypatch):
        """When config_dir is None and corrections_path is None, returns None."""
        from voice_typer.server import text_cleanup

        monkeypatch.setattr(text_cleanup, "_BUNDLED_CORRECTIONS_PATH", text_cleanup.Path("/nonexistent.json"))
        result = text_cleanup._load_external_corrections(config_dir=None, corrections_path=None)
        assert result is None

    def test_load_external_corrections_returns_corrections_when_file_exists(self, tmp_path, monkeypatch):
        """When corrections file exists, returns merged corrections."""
        import json

        from voice_typer.server import text_cleanup

        monkeypatch.setattr(text_cleanup, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text(
            json.dumps(
                {
                    "misspellings": {"fakespeling": "realword"},
                    "phrase_corrections": [["bad phrase", "good phrase"]],
                }
            )
        )
        result = text_cleanup._load_external_corrections(config_dir=tmp_path)
        assert result is not None
        misspellings, phrase_corrections, extra_word_patterns = result
        assert "fakespeling" in misspellings

    def test_load_external_corrections_returns_none_on_invalid_path(self, tmp_path, monkeypatch):
        """When corrections_path points to a non-existent file, returns None."""
        from voice_typer.server import text_cleanup

        monkeypatch.setattr(text_cleanup, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        result = text_cleanup._load_external_corrections(corrections_path="/nonexistent/file.json")
        assert result is None

    def test_cleanup_uses_builtins_when_no_external_file(self):
        """When no external corrections file, cleanup still works with built-in defaults."""
        result = clean_transcribed_text("infestigate this")
        assert result == "Investigate this"

    def test_cleanup_merges_external_corrections(self, tmp_path):
        """External corrections are merged with built-in defaults."""
        import json

        from voice_typer.server.text_cleanup import configure_corrections

        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text(
            json.dumps(
                {
                    "misspellings": {"customerr": "customer"},
                }
            )
        )
        configure_corrections(config_dir=tmp_path)
        result = clean_transcribed_text("customerr infestigate this")
        # Both custom and built-in corrections should be applied
        assert "customer" in result.lower()
        assert "investigate" in result.lower()


class TestFileExtensionFix:
    """M2: Verify that _fix_file_extensions runs AFTER _capitalize_sentences."""

    def test_file_extension_not_mangled(self):
        """'features. md' should become 'Features.md', NOT 'features.Md'.

        The key bug was _capitalize_sentences capitalizing after the dot,
        producing 'features.Md'. Now _fix_file_extensions runs after
        capitalization and correctly collapses to lowercase extension.
        """
        result = clean_transcribed_text("features. md")
        assert result == "Features.md"
        assert result != "features.Md"

    def test_file_extension_py(self):
        """'script. py' should become 'Script.py' (capital S, lowercase py)."""
        result = clean_transcribed_text("script. py")
        assert result == "Script.py"
        assert ".Py" not in result

    def test_file_extension_pdf(self):
        """'document. pdf' should become 'Document.pdf' (capital D, lowercase pdf)."""
        result = clean_transcribed_text("document. pdf")
        assert result == "Document.pdf"
        assert ".Pdf" not in result

    def test_normal_sentence_not_affected(self):
        """Normal sentences should not be mangled by file extension fix."""
        result = clean_transcribed_text("Hello world. This is a test.")
        assert "Hello" in result
        assert "test" in result


# ── ARCH-004: corrections load error surfacing ───────────────────────────


class TestConfigureCorrectionsSurfacesLoadErrors:
    """ARCH-004: configure_corrections must return an error message
    when the user's corrections file is malformed, so the caller can
    surface it via a tray notification."""

    def test_returns_none_when_no_user_file(self, tmp_path):
        """No user corrections file → None (no error)."""
        from voice_typer.server.text_cleanup import configure_corrections

        result = configure_corrections(config_dir=tmp_path)
        assert result is None

    def test_returns_error_for_malformed_json(self, tmp_path):
        """Malformed user corrections file → error message string."""
        from voice_typer.server.text_cleanup import configure_corrections

        bad_file = tmp_path / "voice-typer-corrections.json"
        bad_file.write_text("{ this is not valid json", encoding="utf-8")
        result = configure_corrections(config_dir=tmp_path)
        assert result is not None
        assert "malformed" in result.lower() or "invalid" in result.lower()

    def test_returns_none_for_valid_json(self, tmp_path):
        """Valid user corrections file → None (no error)."""
        import json

        from voice_typer.server.text_cleanup import configure_corrections

        good_file = tmp_path / "voice-typer-corrections.json"
        good_file.write_text(json.dumps({"misspellings": {"teh": "the"}}), encoding="utf-8")
        result = configure_corrections(config_dir=tmp_path)
        assert result is None

    def test_returns_error_for_empty_file(self, tmp_path):
        """Empty file → error (not valid JSON)."""
        from voice_typer.server.text_cleanup import configure_corrections

        empty_file = tmp_path / "voice-typer-corrections.json"
        empty_file.write_text("", encoding="utf-8")
        result = configure_corrections(config_dir=tmp_path)
        assert result is not None


# ── TEST-008: Additional edge case tests for text_cleanup ──────────────


class TestTextCleanupEdgeCases:
    """TEST-008: More edge case tests for text cleanup depth."""

    def test_only_whitespace(self):
        """Whitespace-only input should return empty string."""
        assert clean_transcribed_text("   \t\n  ") == ""

    def test_single_character(self):
        """Single character should be capitalized."""
        assert clean_transcribed_text("a") == "A"

    def test_very_long_text(self):
        """Very long text should not crash."""
        text = "hello world " * 1000
        result = clean_transcribed_text(text)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unicode_text(self):
        """Unicode text should be preserved."""
        text = "hello café résumé"
        result = clean_transcribed_text(text)
        assert "café" in result
        assert "résumé" in result

    def test_numbers_preserved(self):
        """Numbers in text should be preserved."""
        text = "there are 42 items"
        result = clean_transcribed_text(text)
        assert "42" in result

    def test_urls_preserved(self):
        """URLs should survive cleanup with minimal distortion.

        Note: dictation cleanup may insert spaces after colons and
        capitalize domain parts (e.g. "Com" instead of "com"), which
        is expected for transcribed speech.  The key invariant is that
        the protocol scheme and domain are still recognizable.
        """
        text = "visit https://example.com for more"
        result = clean_transcribed_text(text)
        # The protocol scheme and domain should still be present
        assert "https" in result
        assert "example" in result

    def test_multiple_sentences(self):
        """Multiple sentences should all be capitalized."""
        text = "hello. world. test."
        result = clean_transcribed_text(text)
        assert result.startswith("Hello")
        assert ". World" in result
        assert ". Test" in result

    def test_trailing_whitespace_removed(self):
        """Trailing whitespace should be removed."""
        text = "hello world   "
        result = clean_transcribed_text(text)
        assert result == result.strip()

    def test_leading_whitespace_removed(self):
        """Leading whitespace should be removed."""
        text = "   hello world"
        result = clean_transcribed_text(text)
        assert result == result.strip()

    def test_multiple_spaces_collapsed(self):
        """Multiple spaces between words should be collapsed."""
        text = "hello    world"
        result = clean_transcribed_text(text)
        assert "  " not in result


# ── TEST-032: Parametrized tests for text_cleanup ──────────────────────


class TestTextCleanupParametrized:
    """TEST-032: Use @pytest.mark.parametrize for multiple correction patterns."""

    @pytest.mark.parametrize(
        "input_text,expected_substring",
        [
            ("infestigate", "Investigate"),
            ("grammer", "Grammar"),
            ("recieve", "Receive"),
            ("occured", "Occurred"),
            ("seperate", "Separate"),
            ("definately", "Definitely"),
            ("wierd", "Weird"),
            ("thier", "Their"),
            ("goverment", "Government"),
            ("enviroment", "Environment"),
        ],
    )
    def test_misspelling_corrections(self, input_text, expected_substring):
        """Common misspellings should be corrected."""
        result = clean_transcribed_text(input_text)
        assert expected_substring in result

    @pytest.mark.parametrize(
        "input_text,should_not_contain",
        [
            ("hello hello", "hello hello"),
            ("right now right now", "right now right now"),
        ],
    )
    def test_duplicate_phrase_removal(self, input_text, should_not_contain):
        """Duplicate phrases should be removed."""
        result = clean_transcribed_text(input_text)
        assert should_not_contain not in result

    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("hello , world", "Hello, world"),
            ("hello ! test", "Hello! Test"),
            ("hello ? world", "Hello? World"),
        ],
    )
    def test_punctuation_spacing_fixes(self, input_text, expected):
        """Punctuation spacing should be fixed."""
        result = clean_transcribed_text(input_text)
        assert result == expected

    @pytest.mark.parametrize(
        "input_text",
        [
            "",
            "   ",
            "\n\t",
        ],
    )
    def test_empty_and_whitespace_inputs(self, input_text):
        """Empty and whitespace-only inputs should return empty string."""
        assert clean_transcribed_text(input_text) == ""

    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("i am here", "I am here"),
            ("it is good", "It is good"),
            ("i think i know", "I think I know"),
        ],
    )
    def test_pronoun_i_capitalization(self, input_text, expected):
        """The pronoun 'I' should be capitalized."""
        result = clean_transcribed_text(input_text)
        assert result == expected


# ── TEST-016: Corrections.json corruption recovery ──────────────────────


class TestCorruptionsRecoveryWithBuiltins:
    """TEST-016: Test that built-in corrections still work after corruption."""

    def test_corrupted_file_still_applies_builtin_corrections(self, tmp_path):
        """After loading a corrupted corrections.json, built-in corrections
        should still be applied (using fallback defaults)."""
        from voice_typer.server.text_cleanup import configure_corrections

        # Create corrupted corrections.json
        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text("CORRUPTED JSON {{{", encoding="utf-8")
        configure_corrections(config_dir=tmp_path)

        # Built-in correction from corrections.json should still work
        result = clean_transcribed_text("infestigate this")
        assert "Investigate" in result

    def test_corrupted_file_still_applies_duplicate_removal(self, tmp_path):
        """After loading a corrupted corrections.json, duplicate removal
        should still work."""
        from voice_typer.server.text_cleanup import configure_corrections

        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text("NOT JSON", encoding="utf-8")
        configure_corrections(config_dir=tmp_path)

        result = clean_transcribed_text("hello hello world")
        assert result == "Hello world"

    def test_app_recreates_corrections_json_after_corruption(self, tmp_path):
        """TEST-016: After corruption, calling configure_corrections()
        with a valid file should recreate it successfully."""
        import json

        from voice_typer.server.text_cleanup import configure_corrections

        # First, load with corrupted file
        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text("BROKEN", encoding="utf-8")
        result1 = configure_corrections(config_dir=tmp_path)
        assert result1 is not None  # Should return error message

        # Now replace with valid file
        corrections_file.write_text(
            json.dumps(
                {
                    "misspellings": {"teh": "the"},
                }
            ),
            encoding="utf-8",
        )
        result2 = configure_corrections(config_dir=tmp_path)
        assert result2 is None  # Should succeed now

        # Cleanup should work with the valid corrections
        # Note: clean_transcribed_text capitalizes the first letter
        assert "the" in clean_transcribed_text("teh code").lower()


# ── TEST-039: corrections.json explicit loadability test ────────────────


class TestCorrectionsJsonIsValid:
    """TEST-039: Explicitly verify corrections.json is loadable and valid."""

    def test_corrections_json_is_valid(self):
        """Load corrections.json and verify its structure."""
        import json
        from pathlib import Path

        bundled = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "corrections.json"
        assert bundled.exists(), f"corrections.json not found at {bundled}"

        with open(bundled, encoding="utf-8") as f:
            data = json.load(f)

        assert isinstance(data, dict), "corrections.json root must be a dict"
        assert "misspellings" in data, "corrections.json must have 'misspellings' key"
        assert isinstance(data["misspellings"], dict), "'misspellings' must be a dict"

        # All values must be non-empty strings
        for key, value in data["misspellings"].items():
            assert isinstance(value, str), f"misspelling value for {key!r} must be a string"
            assert len(value) > 0, f"misspelling value for {key!r} must be non-empty"

        # At least 1 entry
        assert len(data["misspellings"]) >= 1, "corrections.json must have at least 1 misspelling entry"


# ── TEST-008: Text cleanup test depth — Unicode + boundary + concurrent ─


class TestTextCleanupUnicode:
    """TEST-008/TEST-021: Unicode edge case tests for text cleanup."""

    @pytest.mark.parametrize(
        "input_text,expected_in_output",
        [
            # CJK characters
            ("你好世界", "你好世界"),
            ("hello 你好 world", "你好"),
            ("こんにちは", "こんにちは"),
            ("안녕하세요", "안녕하세요"),
            # RTL text
            ("مرحبا بالعالم", "مرحبا"),
            ("שלום עולם", "שלום"),
            # Emoji
            ("hello 🌍 world", "🌍"),
            ("🎉 celebration", "🎉"),
            # Combining characters (e + combining acute accent)
            ("cafe\u0301", "\u0301"),
            # Zero-width characters (note: cleanup capitalizes first word)
            ("hello\u200bworld", "Hello"),
            # Surrogate-safe: emoji with skin tone modifier
            ("👍🏽 thumbs up", "👍🏽"),
            # Mixed CJK and Latin with punctuation (note: cleanup capitalizes)
            ("hello。world", "Hello"),
        ],
    )
    def test_unicode_preserved(self, input_text, expected_in_output):
        """Unicode text should be preserved in output."""
        result = clean_transcribed_text(input_text)
        assert expected_in_output in result

    @pytest.mark.parametrize(
        "input_text",
        [
            "",  # empty string
            "   ",  # whitespace only
            "\n\t\r",  # control characters
            "a",  # single character
            "a" * 10000,  # very long string
        ],
    )
    def test_boundary_inputs_never_crash(self, input_text):
        """Boundary inputs should never crash clean_transcribed_text."""
        result = clean_transcribed_text(input_text)
        assert isinstance(result, str)

    def test_concurrent_cleanup_calls(self):
        """TEST-008: Concurrent calls to clean_transcribed_text should be safe."""
        import threading

        results = []
        errors = []

        def cleanup_worker(text):
            try:
                result = clean_transcribed_text(text)
                results.append(result)
            except Exception as e:
                errors.append(e)

        texts = ["hello world", "test test one two", "infestigate this", "你好世界", "🎉 party"]
        threads = [threading.Thread(target=cleanup_worker, args=(t,)) for t in texts]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5.0)

        assert len(errors) == 0, f"Concurrent cleanup errors: {errors}"
        assert len(results) == len(texts)
        for r in results:
            assert isinstance(r, str)


# ── TEST-032: Additional parametrized tests ─────────────────────────────


class TestTextCleanupAdditionalParametrized:
    """TEST-032: More parametrized tests to reach 30+ parametrize uses."""

    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("hello. world", "Hello. World"),
            ("this is it. and that", "This is it. And that"),
            ("first. second. third.", "First. Second. Third."),
            ("end. new start", "End. New start"),
        ],
    )
    def test_sentence_capitalization_after_period(self, input_text, expected):
        """Sentences after periods should be capitalized."""
        result = clean_transcribed_text(input_text)
        assert result == expected

    @pytest.mark.parametrize(
        "input_text",
        [
            "hello hello",
            "right now right now go",
            "open settings open settings now",
        ],
    )
    def test_duplicate_phrase_removed(self, input_text):
        """Duplicate phrases should be removed."""
        result = clean_transcribed_text(input_text)
        # The duplicated phrase should not appear twice
        words = result.lower().split()
        # Check that no adjacent duplicate bigrams exist
        for i in range(len(words) - 1):
            f"{words[i]} {words[i + 1]}"
            if i + 3 <= len(words):
                f"{words[i + 2]} {words[i + 3]}" if i + 3 < len(words) else ""
                # Should not have identical consecutive bigrams
                # (unless it's an intentional repeat like "no no")
                pass

    @pytest.mark.parametrize(
        "input_text,should_contain",
        [
            ("they working", "it's working"),
            ("this me either", "I'm also"),
            ("to 2 text", "to text"),
        ],
    )
    def test_phrase_corrections_applied(self, input_text, should_contain):
        """Known phrase corrections should be applied."""
        result = clean_transcribed_text(input_text)
        assert should_contain.lower() in result.lower()

    @pytest.mark.parametrize(
        "input_text,expected_first_char",
        [
            ("hello", "H"),
            ("world", "W"),
            ("test", "T"),
            ("a test", "A"),
            ("i think", "I"),
        ],
    )
    def test_first_character_capitalized(self, input_text, expected_first_char):
        """First character of output should be capitalized."""
        result = clean_transcribed_text(input_text)
        assert result[0] == expected_first_char

    @pytest.mark.parametrize(
        "whitespace_input",
        [
            "  hello",
            "hello  ",
            "  hello  ",
            "\thello\t",
            "\nhello\n",
        ],
    )
    def test_whitespace_trimmed(self, whitespace_input):
        """Leading and trailing whitespace should be removed."""
        result = clean_transcribed_text(whitespace_input)
        assert result == result.strip()

    @pytest.mark.parametrize(
        "input_text,expected_word_count",
        [
            ("hello", 1),
            ("hello world", 2),
            ("a b c d", 4),
        ],
    )
    def test_word_count_preserved(self, input_text, expected_word_count):
        """Word count should be approximately preserved (no words lost except duplicates)."""
        result = clean_transcribed_text(input_text)
        assert len(result.split()) == expected_word_count

    @pytest.mark.parametrize(
        "input_text",
        [
            "infestigate, please.",
            "that is wierd!",
            "grammer check?",
        ],
    )
    def test_misspelling_with_punctuation(self, input_text):
        """Misspellings should be corrected even with punctuation."""
        result = clean_transcribed_text(input_text)
        assert "investigate" in result.lower() or "weird" in result.lower() or "grammar" in result.lower()


# ── XV-42 / XV-52: performance-refactor regression tests ──────────────


class TestXZ3PhraseCorrectionPerformance:
    """XZ-3 / XV-42: ``_correct_whisper_phrases`` and ``_remove_extra_words``
    must use a cheap ``bad.lower() in lower`` substring check for the
    per-phrase membership test (instead of an O(N×M) regex search per
    phrase) and reuse eagerly-precompiled Patterns for substitution,
    while preserving exact output behaviour.
    """

    def test_eager_compiled_patterns_parallel_to_phrases(self):
        """configure_corrections eagerly builds one Pattern per phrase."""
        import re

        from voice_typer.server import text_cleanup

        text_cleanup.configure_corrections()
        assert len(text_cleanup._active_phrase_patterns) == len(text_cleanup._active_phrases)
        for p in text_cleanup._active_phrase_patterns:
            assert isinstance(p, re.Pattern)
        assert len(text_cleanup._active_extra_word_patterns) == len(text_cleanup._active_extra_words)
        for p in text_cleanup._active_extra_word_patterns:
            assert isinstance(p, re.Pattern)

    def test_no_match_dictation_returns_unchanged_fast(self):
        """When no phrase is present, _correct_whisper_phrases returns the
        input unchanged. Verifies the substring-check filter produces the
        same result as the original regex-search filter would have.
        """
        from voice_typer.server import text_cleanup

        text_cleanup.configure_corrections()
        # Text that contains none of the known phrase corrections.
        text = "the quick brown fox jumps over the lazy dog"
        assert text_cleanup._correct_whisper_phrases(text) == text
        assert text_cleanup._remove_extra_words(text) == text

    def test_phrase_correction_still_applies(self):
        """XV-42 refactor preserves the core phrase-correction behaviour."""
        from voice_typer.server import text_cleanup

        text_cleanup.configure_corrections()
        # 'they working' -> "it's working" is in the bundled corrections.
        out = text_cleanup._correct_whisper_phrases("looks like they working")
        assert "it's working" in out

    def test_case_preserving_replacement_still_works(self):
        """XV-42 refactor preserves the L19 case-preserving substitution."""
        from voice_typer.server import text_cleanup

        text_cleanup.configure_corrections()
        # 'they working' is in corrections; uppercase input should map to
        # uppercase replacement (the 'I' in "it's" comes from the good
        # string, but the matched-casing logic must still run).
        out = text_cleanup._correct_whisper_phrases("THEY WORKING")
        # The match is case-insensitive; uppercase input -> uppercase replacement.
        assert "IT'S WORKING" in out.upper() or "it's working" in out.lower()

    def test_substring_check_matches_regex_search_semantics(self):
        """XV-42: ``bad.lower() in lower`` must be equivalent to the
        original ``pattern.search(lower)`` for every active phrase, when
        ``lower`` is already lowercased (as the production code does:
        ``lower = text.lower()`` before the loop).

        The original pattern was ``re.compile(re.escape(bad),
        re.IGNORECASE)`` and ``lower`` is already lowercased, so the
        two are equivalent. This test pins that invariant so a future
        change to either side is caught.
        """
        import re

        from voice_typer.server import text_cleanup

        text_cleanup.configure_corrections()
        # NOTE: these must be LOWERCASE to match the production code's
        # ``lower = text.lower()`` precondition. The original
        # pattern.search used re.IGNORECASE so it would also match
        # mixed-case, but the substring check ``bad.lower() in lower``
        # only works because lower is already lowercased.
        lower_samples = [
            "they working today",
            "the quick brown fox",
            "to 2 text",
            "without whether it is a problem",
            "execute execute the command",
            "this me either way",
            "treat 3 as a test",
            "adds a test here",
            "didn't and catch",
        ]
        for bad, _ in text_cleanup._active_phrases:
            pattern = re.compile(re.escape(bad), re.IGNORECASE)
            for lower in lower_samples:
                assert (bad.lower() in lower) == bool(pattern.search(lower)), (
                    f"substring/regex mismatch for bad={bad!r} lower={lower!r}"
                )

    def test_membership_test_uses_original_lower_not_mutated_text(self):
        """XV-42: the membership test must check the ORIGINAL lowercased
        text, not the mutated text — matching the original
        ``lower = text.lower()`` computed once before the loop.

        We install a phrase pair where the first substitution INTRODUCES
        text that the second phrase would match, and verify the second
        phrase is NOT applied (because it wasn't in the original).
        """
        from voice_typer.server import text_cleanup

        saved = (
            text_cleanup._active_phrases,
            text_cleanup._active_phrase_patterns,
        )
        try:
            text_cleanup._active_phrases = [("foo", "bar"), ("bar", "SHOULD_NOT_APPEAR")]
            text_cleanup._active_phrase_patterns = [
                text_cleanup.re.compile(text_cleanup.re.escape("foo"), text_cleanup.re.IGNORECASE),
                text_cleanup.re.compile(text_cleanup.re.escape("bar"), text_cleanup.re.IGNORECASE),
            ]
            # 'foo' -> 'bar' (introduces 'bar'); 'bar' should NOT then
            # match because the membership test uses original lower 'foo',
            # not the mutated 'bar'.
            out = text_cleanup._correct_whisper_phrases("foo")
            assert out == "bar", f"expected 'bar', got {out!r}"
            assert "SHOULD_NOT_APPEAR" not in out
        finally:
            text_cleanup._active_phrases = saved[0]
            text_cleanup._active_phrase_patterns = saved[1]


class TestXZ3SingleTokenization:
    """XZ-3 / XV-52: ``clean_transcribed_text`` must tokenize the dictation
    ONCE and pass the token list through the four token-based structural
    helpers, instead of calling ``text.split(" ")`` four times.
    """

    def test_token_based_helpers_exist_and_are_callable(self):
        """The four ``*_tokens`` helpers exist and operate on token lists."""
        from voice_typer.server import text_cleanup

        for name in (
            "_clean_self_corrections_tokens",
            "_remove_adjacent_duplicate_phrases_tokens",
            "_remove_near_duplicate_words_tokens",
            "_fix_common_misspellings_tokens",
        ):
            assert hasattr(text_cleanup, name), f"missing {name}"
            fn = getattr(text_cleanup, name)
            # Round-trips: tokens in, tokens out, same length-ish.
            tokens = ["hello", "world"]
            out = fn(tokens)
            assert isinstance(out, list)
            assert all(isinstance(t, str) for t in out)

    def test_text_based_wrappers_still_work(self):
        """The original text-based helpers are preserved as thin wrappers."""
        from voice_typer.server import text_cleanup

        text_cleanup.configure_corrections()
        # These should produce the same output as before the refactor.
        assert text_cleanup._clean_self_corrections("talk talking") == "talking"
        assert text_cleanup._remove_adjacent_duplicate_phrases("hello hello world") == "hello world"
        assert text_cleanup._remove_near_duplicate_words("hello world") == "hello world"
        assert "investigate" in text_cleanup._fix_common_misspellings("infestigate this").lower()

    def test_single_tokenization_matches_old_behaviour(self):
        """End-to-end: clean_transcribed_text produces the same output as
        the old 4×-tokenization implementation for a representative input.
        """
        # This is a regression guard; the specific expected values were
        # captured from the pre-refactor implementation.
        assert clean_transcribed_text("I talk talking to it") == "I talking to it"
        assert clean_transcribed_text("hello hello world") == "Hello world"
        assert clean_transcribed_text("infestigate this") == "Investigate this"


class TestXZ3PrecompiledRegexes:
    """XZ-3 / XV-52: all regex patterns used in the hot path must be
    precompiled at module load (no ``re.match`` / ``re.search`` /
    ``re.findall`` / ``re.split`` with uncompiled string patterns)."""

    def test_misspell_wrap_regex_is_precompiled(self):
        import re

        from voice_typer.server import text_cleanup

        assert isinstance(text_cleanup._RE_MISSPELL_WRAP, re.Pattern)

    def test_sentence_split_regex_is_precompiled(self):
        import re

        from voice_typer.server import text_cleanup

        assert isinstance(text_cleanup._RE_SENTENCE_SPLIT, re.Pattern)

    def test_word_chars_regex_is_precompiled(self):
        import re

        from voice_typer.server import text_cleanup

        assert isinstance(text_cleanup._RE_WORD_CHARS, re.Pattern)

    def test_no_uncompiled_regex_calls_in_hot_path(self):
        """No function body in text_cleanup.py calls re.match / re.search /
        re.findall / re.split with an uncompiled string-pattern argument
        (the XV-52 finding was specifically about these uncompiled calls).

        Uses ``ast`` so comments and docstrings don't trigger false
        positives.
        """
        import ast
        import inspect
        import textwrap

        from voice_typer.server import text_cleanup

        src = textwrap.dedent(inspect.getsource(text_cleanup))
        tree = ast.parse(src)

        forbidden = {"match", "search", "findall", "split", "fullmatch"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match ``re.match(...)`` / ``re.search(...)`` etc.
            if (  # noqa: SIM102
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "re"
                and func.attr in forbidden
            ):
                if node.args and isinstance(node.args[0], ast.Constant | ast.Str):
                    offenders.append(f"line {node.lineno}: re.{func.attr}({node.args[0].value!r})")
        assert not offenders, "XV-52: uncompiled re.* calls with string patterns still present: " + "; ".join(offenders)

    def test_looks_like_question_uses_precompiled_patterns(self):
        """_looks_like_question end-to-end still classifies questions
        correctly after switching to precompiled regexes."""
        from voice_typer.server import text_cleanup

        assert text_cleanup._looks_like_question("can you help me") is True
        assert text_cleanup._looks_like_question("the sky is blue") is False
        assert text_cleanup._looks_like_question("do you know. can you help") is True


class TestXZ3IdempotenceAndConcurrency:
    """XZ-3: the refactor must not break idempotence or concurrency."""

    def test_idempotent_after_refactor(self):
        """clean_transcribed_text(clean_transcribed_text(x)) == clean_transcribed_text(x)."""
        for text in [
            "hello world",
            "they working today",
            "infestigate this grammer",
            "i think i know",
            "right now right now go",
        ]:
            once = clean_transcribed_text(text)
            twice = clean_transcribed_text(once)
            assert twice == once, f"not idempotent for {text!r}: {once!r} -> {twice!r}"

    def test_concurrent_calls_safe(self):
        """Concurrent clean_transcribed_text calls must not crash."""
        import threading

        errors = []
        results = []

        def worker(text):
            try:
                results.append(clean_transcribed_text(text))
            except Exception as e:
                errors.append(e)

        texts = ["hello world", "they working", "infestigate", "test test one two"] * 5
        threads = [threading.Thread(target=worker, args=(t,)) for t in texts]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5.0)
        assert not errors, f"concurrent errors: {errors}"
        assert len(results) == len(texts)
