"""Tests for SEC-010/011: Corrections validation."""
import json
import tempfile
from pathlib import Path


def test_corrections_capped_at_5000():
    """Misspellings beyond 5000 are truncated."""
    from voice_typer.server.text_cleanup import _load_external_corrections
    with tempfile.TemporaryDirectory() as tmp:
        # Create a corrections file with 6000 misspellings
        data = {
            "misspellings": {f"wrong{i}": f"right{i}" for i in range(6000)},
            "phrase_corrections": [],
            "extra_word_patterns": [],
        }
        path = Path(tmp) / "corrections.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        result = _load_external_corrections(
            corrections_path=str(path)
        )
        assert result is not None
        misspellings, _, _ = result
        assert len(misspellings) <= 5000


def test_long_strings_filtered():
    """Corrections with strings >200 chars are filtered."""
    from voice_typer.server.text_cleanup import _load_external_corrections
    with tempfile.TemporaryDirectory() as tmp:
        long_str = "a" * 201
        data = {
            "misspellings": {long_str: "short"},
            "phrase_corrections": [[long_str, "short"]],
            "extra_word_patterns": [[long_str, "short"]],
        }
        path = Path(tmp) / "corrections.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        result = _load_external_corrections(
            corrections_path=str(path)
        )
        assert result is not None
        misspellings, phrases, extra = result
        assert long_str not in misspellings
        assert not any(b == long_str for b, g in phrases)
        assert not any(b == long_str for b, g in extra)
