"""Consolidated diagnostics CLI script tests.

Split from the former catch-all test module (
2026-08-25). Verifies ``scripts/diagnostics.py`` — the consolidated
developer-facing diagnostic CLI — exists and keeps its subcommands.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestConsolidatedDiagnostics:
    """Verify consolidated diagnostics script."""

    def test_diagnostics_script_exists(self):
        """scripts/diagnostics.py should exist."""
        script = REPO_ROOT / "scripts" / "diagnostics.py"
        assert script.exists(), "Consolidated diagnostics script should exist"

    def test_diagnostics_has_subcommands(self):
        """Diagnostics script should have f2, cublas, runtime, test-runner subcommands."""
        script = REPO_ROOT / "scripts" / "diagnostics.py"
        content = script.read_text()
        for cmd in ["f2", "cublas", "runtime", "test-runner"]:
            assert cmd in content, f"Diagnostics script should have '{cmd}' subcommand"
