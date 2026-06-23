"""Regression tests for NEW-TS-004, NEW-TS-006, NEW-TS-012, NEW-TS-015.

These are TypeScript-side fixes verified via static source inspection
(the renderer doesn't have a JS test runner wired up for component
tests; we verify the source structure instead).

NEW-TS-004: Settings.tsx and Microphone.tsx re-implemented the snackbar
pattern inline instead of using the shared useSnackbar hook.

NEW-TS-006: Home.tsx registered two separate usePythonEvent listeners
for 'transcription_final' — consolidated into one.

NEW-TS-012: App.tsx had an ``as RecordingState`` cast that was never
removed despite a comment claiming it was.  Replaced with a runtime
validator.

NEW-TS-015: usePython().isReady was always true (the preload installs
window.python before React mounts), making every ``if (!isReady)
return`` guard dead code.  Removed the misleading flag.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RENDERER_SRC = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
)


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestNewTs004SnackbarConsolidation:
    """NEW-TS-004: pages must use the shared useSnackbar hook."""

    def test_settings_uses_shared_hook(self):
        """Settings.tsx must import and use useSnackbar, not inline state."""
        src = _read("pages/Settings.tsx")
        assert "import { useSnackbar }" in src, (
            "Settings.tsx must import useSnackbar from @/hooks/useSnackbar"
        )
        assert "const { showSnack, Snackbar } = useSnackbar()" in src, (
            "Settings.tsx must destructure showSnack + Snackbar from useSnackbar"
        )
        # The inline snackbar state must be gone.
        assert "useState<{ message: string; type: 'success'" not in src, (
            "Settings.tsx still has inline snackbar useState"
        )

    def test_microphone_uses_shared_snackbar_component(self):
        """Microphone.tsx must use <Snackbar />, not inline JSX."""
        src = _read("pages/Microphone.tsx")
        assert "const { showSnack, Snackbar } = useSnackbar()" in src, (
            "Microphone.tsx must destructure Snackbar from useSnackbar"
        )
        # The inline JSX snackbar must be gone.
        assert "{snackbar && (" not in src, (
            "Microphone.tsx still has inline snackbar JSX"
        )


class TestNewTs006SingleTranscriptionFinalListener:
    """NEW-TS-006: Home.tsx must register only ONE transcription_final listener."""

    def test_only_one_transcription_final_listener(self):
        """Count occurrences of usePythonEvent('transcription_final', ...).
        Must be exactly 1 (previously was 2).
        """
        src = _read("pages/Home.tsx")
        count = src.count("usePythonEvent('transcription_final'")
        assert count == 1, (
            f"Home.tsx has {count} usePythonEvent('transcription_final') "
            "calls; expected exactly 1 (NEW-TS-006 consolidated them)"
        )


class TestNewTs012NoAsRecordingStateCast:
    """NEW-TS-012: App.tsx must not cast to RecordingState without validation."""

    def test_no_unvalidated_as_recording_state_cast(self):
        """The ``as RecordingState`` cast must only appear inside a
        runtime validator (after ``RECORDING_STATES.has(value)`` has
        confirmed the value is valid).  An unvalidated cast on raw
        IPC input is what NEW-TS-012 forbids.
        """
        src = _read("App.tsx")
        # Strip comment lines.
        code_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("//"):
                continue
            if "//" in line:
                line = line.split("//", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        # The only allowed `as RecordingState` is inside the validator,
        # where it follows a .has() check.  We allow that one occurrence.
        # Find all occurrences.
        import re
        # Match `as RecordingState` not preceded by a .has() check on
        # the same logical line.
        # The validator pattern is:
        #   return RECORDING_STATES.has(value) ? (value as RecordingState) : null
        # We allow this specific pattern; any other `as RecordingState`
        # is a violation.
        validator_pattern = r"RECORDING_STATES\.has\(value\)\s*\?\s*\(value as RecordingState\)\s*:\s*null"
        validator_matches = re.findall(validator_pattern, code_only)
        # Count total `as RecordingState` occurrences.
        total_casts = code_only.count("as RecordingState")
        # The number of validator-pattern occurrences must equal the total.
        assert len(validator_matches) == total_casts, (
            f"Found {total_casts} `as RecordingState` casts but only "
            f"{len(validator_matches)} are inside the validated validator "
            "pattern.  Unvalidated casts are forbidden (NEW-TS-012)."
        )

    def test_runtime_validator_exists(self):
        """App.tsx must define a runtime validator for RecordingState."""
        src = _read("App.tsx")
        assert "asRecordingState" in src, (
            "App.tsx must define a runtime validator `asRecordingState` "
            "instead of using `as RecordingState` cast"
        )
        assert "RECORDING_STATES" in src, (
            "App.tsx must define the RECORDING_STATES set used by the validator"
        )


class TestNewTs015NoIsReadyField:
    """NEW-TS-015: usePython() must not return a misleading isReady flag."""

    def test_use_python_does_not_return_is_ready(self):
        """The hook must not return ``isReady`` (it was always true)."""
        src = _read("hooks/usePython.ts")
        # The hook must not return isReady.
        # We strip comments before checking.
        code_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("//"):
                continue
            if "//" in line:
                line = line.split("//", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        assert "isReady" not in code_only, (
            "usePython() still returns or references isReady — the flag "
            "was always true (the preload installs window.python before "
            "React mounts), making every `if (!isReady) return` guard "
            "dead code"
        )

    def test_app_does_not_use_is_ready(self):
        """App.tsx must not destructure isReady from usePython()."""
        src = _read("App.tsx")
        # Strip comment lines.
        code_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("//"):
                continue
            if "//" in line:
                line = line.split("//", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        assert "isReady" not in code_only, (
            "App.tsx still references isReady in code — should be removed"
        )


class TestNewIpc010RecordingStateEnumCleaned:
    """NEW-IPC-010: RecordingState enum must have only the 6 values
    that the Python backend actually emits."""

    def test_only_six_states(self):
        """The RecordingState union must have exactly 6 values:
        idle, recording, transcribing, loading, cancelling, error.
        """
        src = _read("types/ipc.ts")
        # Find the RecordingState union and extract just the union
        # block (between "export type RecordingState =" and the next
        # blank line / "export" statement).
        start = src.find("export type RecordingState")
        assert start != -1, "RecordingState type not found"
        # The union ends at the first blank line after the start.
        # Look for the pattern: a line with just whitespace followed
        # by a non-'|' line.
        end = start
        lines = src[start:].splitlines()
        union_lines = []
        for i, line in enumerate(lines[1:], 1):  # skip the first line (the export)
            stripped = line.strip()
            if stripped == "":
                # Blank line — end of union if the next non-blank line
                # doesn't start with '|'.
                break
            if stripped.startswith("|"):
                union_lines.append(stripped)
            else:
                # Non-pipe line — end of union.
                break
        union_text = "\n".join(union_lines)
        import re
        states = re.findall(r"'(\w+)'", union_text)
        assert set(states) == {
            "idle", "recording", "transcribing",
            "loading", "cancelling", "error",
        }, (
            f"RecordingState has unexpected values: {states}"
        )

    def test_dead_states_removed(self):
        """The 7 dead values must NOT be in the RecordingState union."""
        src = _read("types/ipc.ts")
        start = src.find("export type RecordingState")
        # Extract just the union block (same logic as above).
        lines = src[start:].splitlines()
        union_lines = []
        for line in lines[1:]:
            stripped = line.strip()
            if stripped == "":
                break
            if stripped.startswith("|"):
                union_lines.append(stripped)
            else:
                break
        union_text = "\n".join(union_lines)
        dead_values = [
            "listening", "processing", "warming_up",
            "downloading", "paused", "setup", "not_configured",
        ]
        for dead in dead_values:
            assert f"'{dead}'" not in union_text, (
                f"RecordingState still contains dead value '{dead}'"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
