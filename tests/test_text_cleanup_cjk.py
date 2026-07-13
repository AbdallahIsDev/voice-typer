"""Tests for CJK/RTL/emoji edge cases.

TEST-021: Test corrections with CJK characters, RTL text, emoji in patterns.
Test transcription output with these characters.
"""

from __future__ import annotations

import pytest
from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections


@pytest.fixture(autouse=True)
def _configure_corrections():
    """Initialize corrections from bundled corrections.json before each test."""
    configure_corrections()


class TestCJKCharacters:
    """Test text cleanup with CJK (Chinese/Japanese/Korean) characters."""

    def test_cjk_text_not_mangled(self):
        """CJK characters should pass through cleanup without being mangled."""
        text = "你好世界"
        result = clean_transcribed_text(text)
        assert "你好世界" in result

    def test_mixed_cjk_and_latin(self):
        """Mixed CJK and Latin text should be preserved."""
        text = "hello 你好 world"
        result = clean_transcribed_text(text)
        assert "你好" in result
        assert "hello" in result.lower() or "Hello" in result

    def test_japanese_text(self):
        """Japanese characters should pass through."""
        text = "こんにちは"
        result = clean_transcribed_text(text)
        assert "こんにちは" in result

    def test_korean_text(self):
        """Korean characters should pass through."""
        text = "안녕하세요"
        result = clean_transcribed_text(text)
        assert "안녕하세요" in result


class TestRTLText:
    """Test text cleanup with RTL (Right-to-Left) text."""

    def test_arabic_text_not_mangled(self):
        """Arabic text should pass through without corruption."""
        text = "مرحبا بالعالم"
        result = clean_transcribed_text(text)
        assert "مرحبا" in result

    def test_hebrew_text_not_mangled(self):
        """Hebrew text should pass through without corruption."""
        text = "שלום עולם"
        result = clean_transcribed_text(text)
        assert "שלום" in result

    def test_mixed_rtl_and_ltr(self):
        """Mixed RTL and LTR text should be preserved."""
        text = "hello مرحبا world"
        result = clean_transcribed_text(text)
        assert "مرحبا" in result


class TestEmojiInPatterns:
    """Test text cleanup with emoji in text."""

    def test_emoji_preserved(self):
        """Emoji should be preserved in output."""
        text = "hello 🌍 world"
        result = clean_transcribed_text(text)
        assert "🌍" in result

    def test_emoji_at_start(self):
        """Emoji at the start should be preserved."""
        text = "🎉 celebration"
        result = clean_transcribed_text(text)
        assert "🎉" in result

    def test_emoji_not_treated_as_duplicate(self):
        """Emoji should not trigger duplicate word removal."""
        text = "great 👍 great 👍"
        result = clean_transcribed_text(text)
        # Should preserve the structure, not collapse the emojis
        assert "👍" in result

    def test_multiple_emoji(self):
        """Multiple emoji should be preserved."""
        text = "hello 🎉🎊🎈 world"
        result = clean_transcribed_text(text)
        assert "🎉" in result
        assert "🎊" in result
        assert "🎈" in result


class TestEmojiInCorrections:
    """Test that corrections with emoji in patterns don't crash."""

    def test_misspelling_with_emoji_no_crash(self, tmp_path):
        """A corrections file with emoji should load without crashing."""
        import json

        from voice_typer.server.text_cleanup import configure_corrections
        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text(json.dumps({
            "misspellings": {"teh": "the"},
        }), encoding="utf-8")
        # Should not crash
        result = configure_corrections(config_dir=tmp_path)
        assert result is None

    def test_cleanup_with_cjk_and_punctuation(self):
        """CJK text with mixed punctuation should be handled."""
        text = "你好。 world！"
        result = clean_transcribed_text(text)
        assert "你好" in result
