"""Tests for ``VocabularyManager._save_user`` retry behaviour and shared
vocabulary/text-cleanup constants.

Split out of the former ``tests/test_history_and_models.py`` catch-all
(Phase 4.5 / TC-15). Verbatim mechanical move — same test names +
assertions, only the file location changed.
"""

from __future__ import annotations

from unittest.mock import patch


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


class TestSharedVocabConstants:
    """text_cleanup imports BUNDLED_CORRECTIONS_PATH from vocabulary."""

    def test_bundled_corrections_path_is_same_object(self):
        from voice_typer.server import text_cleanup, vocabulary

        assert text_cleanup._BUNDLED_CORRECTIONS_PATH is vocabulary.BUNDLED_CORRECTIONS_PATH
