"""Regression tests for NEW-DEAD-002: scripts/diagnostics/* were broken
against the refactored code.

Previously:
1. ``runtime_proof.py`` imported from ``voice_typer.config``,
   ``voice_typer.transcription``, and ``voice_typer.tray`` — all of
   which were moved to ``voice_typer.server.*`` during the package
   reorganization.  The script would crash on import.
2. ``runtime_test_runner.py`` grepped the production log for Flet-era
   markers (``_busy reset to False``, ``FORCE RECOVER``, ``HOTKEY
   FIRED``) that no longer exist.  The script would always report
   failure even when the production code worked correctly.

The fix:
- Updated imports in ``runtime_proof.py`` to use the canonical paths.
- Updated grep patterns in ``runtime_test_runner.py`` to use the
  current production markers (``[TRANSCRIBE] Transcription complete``,
  ``Audio too short, skipping transcription``, ``FORCE RECOVER``).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "diagnostics"


class TestRuntimeProofImports:
    """NEW-DEAD-002: runtime_proof.py must import from the right paths."""

    def test_script_parses_without_syntax_error(self):
        """The script must parse cleanly (no syntax errors)."""
        script_path = SCRIPTS_DIR / "runtime_proof.py"
        source = script_path.read_text()
        ast.parse(source)

    def test_script_imports_from_server_package(self):
        """The script must import from ``voice_typer.server.*`` not the
        legacy top-level ``voice_typer.*`` paths.
        """
        script_path = SCRIPTS_DIR / "runtime_proof.py"
        source = script_path.read_text()
        # The legacy broken imports would say "from voice_typer.config",
        # "from voice_typer.transcription", "from voice_typer.tray".
        # The fix uses "from voice_typer.server.config", etc.
        assert "from voice_typer.server.config import" in source, (
            "runtime_proof.py must import Config from voice_typer.server.config"
        )
        assert "from voice_typer.server.transcription import" in source, (
            "runtime_proof.py must import TranscriptionEngine from "
            "voice_typer.server.transcription"
        )
        assert "from voice_typer.server.tray_types import" in source, (
            "runtime_proof.py must import AppState from "
            "voice_typer.server.tray_types"
        )
        # The legacy paths must NOT appear.
        assert "from voice_typer.config import" not in source, (
            "runtime_proof.py still imports from the legacy voice_typer.config path"
        )
        assert "from voice_typer.transcription import" not in source, (
            "runtime_proof.py still imports from the legacy voice_typer.transcription path"
        )
        assert "from voice_typer.tray import" not in source, (
            "runtime_proof.py still imports from the legacy voice_typer.tray path"
        )


class TestRuntimeTestRunnerMarkers:
    """NEW-DEAD-002: runtime_test_runner.py must grep for current markers."""

    def test_script_parses_without_syntax_error(self):
        script_path = SCRIPTS_DIR / "runtime_test_runner.py"
        source = script_path.read_text()
        ast.parse(source)

    def test_uses_current_transcription_complete_marker(self):
        """The runner must look for ``[TRANSCRIBE] Transcription complete``
        (the actual marker emitted by dictation_pipeline.py:98), not
        the legacy ``_busy reset to False``.
        """
        source = (SCRIPTS_DIR / "runtime_test_runner.py").read_text()
        assert "[TRANSCRIBE] Transcription complete" in source, (
            "runtime_test_runner.py must grep for "
            "'[TRANSCRIBE] Transcription complete' (current production marker)"
        )

    def test_does_not_rely_on_legacy_busy_reset_marker(self):
        """The legacy ``_busy reset to False`` marker is no longer
        emitted by the production code; the runner must not depend on
        it as the primary success signal.
        """
        source = (SCRIPTS_DIR / "runtime_test_runner.py").read_text()
        # The old wait_for_log call looked for "_busy reset to False".
        # The new code looks for "[TRANSCRIBE] Transcription complete".
        # We allow the legacy string to appear in comments/docstrings
        # but NOT as the argument to wait_for_log.
        # Easiest check: the wait_for_log call must not pass the legacy
        # string.
        assert 'wait_for_log(LOG_FILE, "_busy reset to False"' not in source, (
            "runtime_test_runner.py still uses the legacy _busy reset to False "
            "marker as a wait_for_log argument"
        )

    def test_force_recover_still_checked(self):
        """FORCE RECOVER is still emitted by recording_controller.py:623,
        so the runner should still check for it as a fallback signal.
        """
        source = (SCRIPTS_DIR / "runtime_test_runner.py").read_text()
        assert "FORCE RECOVER" in source, (
            "runtime_test_runner.py should still check for FORCE RECOVER "
            "(still emitted by recording_controller.py:623)"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
