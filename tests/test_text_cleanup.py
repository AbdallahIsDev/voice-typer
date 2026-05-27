"""Tests for lightweight post-transcription text cleanup."""

from voice_typer.text_cleanup import clean_transcribed_text


class TestCleanTranscribedText:
    def test_removes_adjacent_duplicate_words(self):
        text = "this this is is a test test message"

        assert clean_transcribed_text(text) == "This is a test test message."

    def test_preserves_intentional_repeated_words(self):
        assert clean_transcribed_text("no no no") == "No no no"
        assert clean_transcribed_text("very very good") == "Very very good"
        assert clean_transcribed_text("test test one two") == "Test test one two"

    def test_collapses_high_confidence_whisper_duplicates(self):
        assert clean_transcribed_text("hello hello world") == "Hello world"
        assert (
            clean_transcribed_text("open settings open settings now")
            == "Open settings now"
        )

    def test_removes_adjacent_duplicate_short_phrases(self):
        text = "right now right now I want to test this"

        assert clean_transcribed_text(text) == "Right now I want to test this."

    def test_fixes_punctuation_spacing(self):
        text = "hello , world ! this is working ?"

        assert clean_transcribed_text(text) == "Hello, world! This is working?"

    def test_capitalizes_sentence_starts_and_pronoun_i(self):
        text = "i tested this. it works and i like it"

        assert clean_transcribed_text(text) == "I tested this. It works and I like it."

    def test_adds_question_mark_for_question_openers(self):
        text = "can we make this faster"

        assert clean_transcribed_text(text) == "Can we make this faster?"

    def test_adds_question_mark_for_question_final_sentence(self):
        text = "i want to make this faster. can we do that"

        assert clean_transcribed_text(text) == "I want to make this faster. Can we do that?"

    def test_does_not_force_question_mark_for_plain_statement(self):
        text = "we can make this faster"

        assert clean_transcribed_text(text) == "We can make this faster."

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
        assert clean_transcribed_text("convert the attic is a voice to text") == "Convert the attic is a voice to text."

    def test_corrects_whisper_phrase_to_2(self):
        assert clean_transcribed_text("voice to 2 text") == "Voice to text"

    def test_corrects_whisper_phrase_they_working(self):
        assert clean_transcribed_text("looks like they working") == "Looks like it's working"

    def test_removes_extra_word_without_whether(self):
        assert clean_transcribed_text("Without whether it is a problem") == "Whether it is a problem."

    def test_removes_extra_word_didnt_and(self):
        assert clean_transcribed_text("it didn't and catch everything") == "It didn't catch everything"

    def test_preserves_intentional_repetition_with_near_duplicates(self):
        assert clean_transcribed_text("no no no stay repeated") == "No no no stay repeated."

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

    def test_cleanup_skipped_when_disabled(self):
        """When enabled=False, text should only be stripped."""
        text = "this is a test of the cleanup"
        result = clean_transcribed_text(text, enabled=False)
        # No capitalization, no punctuation added
        assert result == "this is a test of the cleanup"

    def test_cleanup_enabled_is_default(self):
        """By default, cleanup should be enabled."""
        text = "can we make this faster"
        result = clean_transcribed_text(text)
        assert result == "Can we make this faster?"
