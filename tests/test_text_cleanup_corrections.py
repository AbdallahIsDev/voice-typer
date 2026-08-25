"""Tests for ``CorrectionsLoadError`` raised on malformed corrections file.

Split out of the former ``tests/test_history_and_models.py`` catch-all
(Phase 4.5 / TC-15). Verbatim mechanical move — same test names +
assertions, only the file location changed.
"""

from __future__ import annotations

import pytest


class TestCorrectionsLoadError:
    """CorrectionsLoadError for malformed corrections file."""

    def test_corrections_load_error_is_runtime_error(self):
        from voice_typer.server.text_cleanup import CorrectionsLoadError

        assert issubclass(CorrectionsLoadError, RuntimeError)

    def test_corrections_load_error_raised_on_malformed_file(self, tmp_path, monkeypatch):
        from voice_typer.server.text_cleanup import (
            CorrectionsLoadError,
            _load_external_corrections,
        )

        path = tmp_path / "voice-typer-corrections.json"
        path.write_text("{not valid json", encoding="utf-8")
        import voice_typer.server.text_cleanup._corrections_data as tc

        monkeypatch.setattr(tc, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        with pytest.raises(CorrectionsLoadError):
            _load_external_corrections(config_dir=tmp_path)
