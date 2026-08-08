"""Verify the ``TranscriptionEngine`` re-export
removal from ``voice_typer.server.app``.

The ``app.py`` test-seam re-export removal tracks the
multi-day, 65+-file refactor of migrating ~173 monkeypatch sites off
``voice_typer.server.app.<symbol>`` re-exports and onto the symbols'
canonical module paths. The full refactor is too large for one
sub-agent, but this test pins the one re-export that WAS migrated as a
proof-of-concept: ``TranscriptionEngine``.

Contracts pinned here:

1. ``voice_typer.server.app`` MUST NOT export ``TranscriptionEngine``
   (the re-export was removed; ``app.py`` is no longer a test-seam for
   this symbol).
2. ``voice_typer.server.transcription.TranscriptionEngine`` is the
   canonical class (the only correct patch target going forward).
3. ``monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", ...)``
   works (the canonical path is patchable, so migrated tests keep
   running).
4. The 5 monkeypatch sites that previously targeted
   ``voice_typer.server.app.TranscriptionEngine`` now target the
   canonical path (static check via grep — the migration is mechanical
   and should not regress).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


class TestTranscriptionEngineReExportRemoved:
    """``TranscriptionEngine`` re-export removed from
    ``voice_typer.server.app``.
    """

    def test_app_module_does_not_export_transcription_engine(self) -> None:
        """``voice_typer.server.app`` MUST NOT have a ``TranscriptionEngine``
        attribute — the re-export was removed as part of the migration.

        Production code in ``app.py`` does not instantiate
        ``TranscriptionEngine`` directly (the ASR registry in
        ``asr_registry._BACKEND_SPECS`` constructs backends via
        ``importlib.import_module("voice_typer.server.transcription")``),
        so the re-export was purely a test-seam. Tests that need to
        patch the class now target the canonical location.
        """
        import voice_typer.server.app as app_mod

        assert not hasattr(app_mod, "TranscriptionEngine"), (
            "Regression: voice_typer.server.app still re-exports "
            "TranscriptionEngine. The re-export should be removed — "
            "tests should patch voice_typer.server.transcription.TranscriptionEngine "
            "(the canonical location) instead."
        )

    def test_canonical_transcription_engine_class_exists(self) -> None:
        """The canonical ``voice_typer.server.transcription.TranscriptionEngine``
        class must exist and be a class (so migrated monkeypatch.setattr
        calls have a real target).
        """
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        assert inspect.isclass(TranscriptionEngine), (
            "voice_typer.server.transcription.TranscriptionEngine must be a "
            "class — migrated monkeypatch sites rely on patching it with a "
            "MagicMock."
        )

    def test_canonical_path_is_monkeypatchable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", ...)``
        must work without raising (the canonical path is the correct
        patch target for migrated tests).
        """
        from unittest.mock import MagicMock

        # This must not raise AttributeError — the canonical path exists.
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
        """Static check: no test file should still monkeypatch the removed
        ``voice_typer.server.app.TranscriptionEngine`` re-export. The 5
        migrated sites (tests/app/test_lifecycle.py ×4,
        tests/test_qwen_engine.py ×1) now target
        ``voice_typer.server.transcription.TranscriptionEngine``.

        Uses a source scan so a future regression (someone re-introduces
        a patch on the removed re-export) trips this test loudly. Pattern
        is scoped to actual ``monkeypatch.setattr`` call sites (not
        docstrings / comments) to avoid false positives from this test
        file's own narrative.
        """
        repo_root = Path(__file__).resolve().parent.parent
        pattern = re.compile(
            r'monkeypatch\.setattr\(\s*"voice_typer\.server\.app\.TranscriptionEngine"'
        )
        hits = []
        for path in sorted((repo_root / "tests").rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                hits.append(str(path.relative_to(repo_root)))
        assert not hits, (
            "Regression: found test(s) still monkeypatching the "
            "removed voice_typer.server.app.TranscriptionEngine re-export:\n"
            + "\n".join(hits)
        )
