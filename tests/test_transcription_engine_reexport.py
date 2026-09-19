"""Verify the ``TranscriptionEngine`` re-export"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


class TestTranscriptionEngineReExportRemoved:
    """``voice_typer.server.app``."""

    def test_app_module_does_not_export_transcription_engine(self) -> None:
        """``voice_typer.server.app`` MUST NOT have a ``TranscriptionEngine``"""
        import voice_typer.server.app as app_mod

        assert not hasattr(app_mod, "TranscriptionEngine"), (
            "Regression: voice_typer.server.app still re-exports "
            "TranscriptionEngine. The re-export should be removed, "
            "tests should patch voice_typer.server.transcription.TranscriptionEngine "
            "(the canonical location) instead."
        )

    def test_canonical_transcription_engine_class_exists(self) -> None:
        """The canonical ``voice_typer.server.transcription.TranscriptionEngine``"""
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        assert inspect.isclass(TranscriptionEngine), (
            "voice_typer.server.transcription.TranscriptionEngine must be a "
            "class, migrated monkeypatch sites rely on patching it with a "
            "MagicMock."
        )

    def test_canonical_path_is_monkeypatchable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``monkeypatch.setattr(\"voice_typer.server.transcription.TranscriptionEngine\", ...)``"""
        from unittest.mock import MagicMock

        # This must not raise AttributeError, the canonical path exists.
        monkeypatch.setattr(
            "voice_typer.server.transcription.TranscriptionEngine",
            MagicMock(),
        )

        # Verify the patch took effect.
        import voice_typer.server.transcription as transcription_mod

        assert (
            not isinstance(transcription_mod.TranscriptionEngine, type)
            or transcription_mod.TranscriptionEngine.__name__ == "Mock"
        ), "monkeypatch on canonical path did not take effect"

    def test_no_test_files_still_patch_app_transcription_engine(self) -> None:
        """Static check: no test file should still monkeypatch the removed"""
        repo_root = Path(__file__).resolve().parent.parent
        pattern = re.compile(r'monkeypatch\.setattr\(\s*"voice_typer\.server\.app\.TranscriptionEngine"')
        hits = []
        for path in sorted((repo_root / "tests").rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                hits.append(str(path.relative_to(repo_root)))
        assert not hits, (
            "Regression: found tests still monkeypatching the "
            "removed voice_typer.server.app.TranscriptionEngine re-export:\n" + "\n".join(hits)
        )
