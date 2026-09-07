"""Tests for corrections.json corruption recovery.

TEST-016: Test loading corrupted corrections.json files and verify
graceful recovery with defaults.
"""

from __future__ import annotations

import json

from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections


class TestCorrectionsCorruptionRecovery:
    """Verify the app recovers gracefully from corrupted corrections.json."""

    def test_truncated_json(self, tmp_path):
        """Truncated JSON should be handled gracefully."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text('{"misspellings": {"teh": "the", "rec', encoding="utf-8")
        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        # Should return an error message, not crash
        assert result is not None
        assert isinstance(result, str)
        # Cleanup should still work with defaults
        assert clean_transcribed_text("hello") == "Hello"

    def test_null_bytes_in_file(self, tmp_path):
        """Null bytes in the file should be handled gracefully."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_bytes(b'{"misspellings": \x00 {"teh": "the"}}')
        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        # Should not crash; either succeeds or returns error
        assert result is None or isinstance(result, str)

    def test_wrong_encoding_file(self, tmp_path):
        """File with wrong encoding (e.g. UTF-16) should be handled gracefully."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        # Write as UTF-16 LE with BOM
        corrections_file.write_bytes(b'\xff\xfe{"misspellings": {}}'.replace(b"{", b"{".decode().encode("utf-16-le")))
        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        # Should not crash
        assert result is None or isinstance(result, str)

    def test_binary_garbage_file(self, tmp_path):
        """Random binary data should be handled gracefully."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_bytes(bytes(range(256)))
        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        assert result is not None
        # Cleanup should still work
        assert clean_transcribed_text("hello world") == "Hello world"

    def test_empty_dict_corrections(self, tmp_path):
        """Empty dict should be valid (no corrections)."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text("{}", encoding="utf-8")
        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        assert result is None

    def test_corruption_recovery_preserves_basic_cleanup(self, tmp_path):
        """After loading corrupted corrections, basic cleanup should still work."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text("THIS IS NOT JSON AT ALL!!!", encoding="utf-8")
        text_cleanup.configure_corrections(config_dir=tmp_path)

        # Basic cleanup should still work (using fallback/built-in defaults)
        result = clean_transcribed_text("hello world")
        assert result == "Hello world"

    def test_missing_required_keys_in_corrections(self, tmp_path):
        """JSON with missing required keys should be handled gracefully."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        # Only misspellings, no phrase_corrections or extra_word_patterns
        corrections_file.write_text(json.dumps({"misspellings": {"teh": "the"}}), encoding="utf-8")
        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        assert result is None


class TestCorrectionsExplicitLoad:
    """TEST-039: corrections.json should be explicitly tested as loadable."""

    def test_bundled_corrections_json_loads(self):
        """The bundled corrections.json file must parse correctly."""
        from pathlib import Path

        bundled = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "corrections.json"
        assert bundled.exists(), f"Bundled corrections.json not found at {bundled}"
        with open(bundled, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "misspellings" in data
        assert isinstance(data["misspellings"], dict)

    def test_bundled_corrections_configure_no_error(self):
        """configure_corrections() with no user file should succeed."""
        result = configure_corrections()
        # Should return None (no error) when using bundled corrections
        assert result is None

    def test_bundled_corrections_produce_valid_cleanup(self):
        """Bundled corrections should produce valid cleanup results."""
        # Self-sufficient: under `--dist=loadgroup` unmarked tests in
        # this file may land on different workers, so a sibling test's
        # configure_corrections() cannot be relied upon. Restore the
        # bundled set explicitly before asserting on it.
        assert configure_corrections() is None
        # Test a known correction from the bundled file
        result = clean_transcribed_text("infestigate this")
        assert "Investigate" in result
