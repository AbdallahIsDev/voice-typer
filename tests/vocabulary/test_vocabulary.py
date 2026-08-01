"""Vocabulary / corrections tests split out of the former ``tests/test_history_and_models.py``.

Domain: vocabulary management — atomic save retry on
PermissionError, corrections loader error type for malformed JSON,
and the shared BUNDLED_CORRECTIONS_PATH constant between
``vocabulary`` and ``text_cleanup``.

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestVocabularySaveRetry:
    """_save_user retries on PermissionError."""

    def test_save_retries_on_permission_error(self, tmp_path):
        import os

        from voice_typer.server.vocabulary import VocabularyManager

        vocab = VocabularyManager(config_dir=tmp_path)
        attempt = {"n": 0}
        real_replace = os.replace

        def flaky_replace(src, dst):
            attempt["n"] += 1
            if attempt["n"] < 3:
                raise PermissionError(f"Simulated lock (attempt {attempt['n']})")
            real_replace(src, dst)

        with patch("os.replace", side_effect=flaky_replace):
            vocab._save_user()

        assert attempt["n"] == 3
        assert (tmp_path / "voice-typer-vocabulary.json").exists()


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
        import voice_typer.server.text_cleanup as tc

        monkeypatch.setattr(tc, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        with pytest.raises(CorrectionsLoadError):
            _load_external_corrections(config_dir=tmp_path)


class TestSharedVocabConstants:
    """text_cleanup imports BUNDLED_CORRECTIONS_PATH from vocabulary."""

    def test_bundled_corrections_path_is_same_object(self):
        from voice_typer.server import text_cleanup, vocabulary

        assert text_cleanup._BUNDLED_CORRECTIONS_PATH is vocabulary.BUNDLED_CORRECTIONS_PATH
