"""Tests that ``voice_typer/__main__.py`` documents its console-script role.

Split out of the former ``tests/test_history_and_models.py`` catch-all
(Phase 4.5). Verbatim mechanical move — same test names +
assertions, only the file location changed. ``REPO_ROOT`` is recomputed
relative to this file's new ``tests/app/`` location.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestMainModuleDocumentsConsoleScriptRole:
    """__main__.py and console script serve different purposes."""

    def test_main_has_clarifying_docstring(self):
        main_path = REPO_ROOT / "voice_typer" / "__main__.py"
        source = main_path.read_text()
        assert "different purposes" in source.lower() or "NOT a duplicate" in source
