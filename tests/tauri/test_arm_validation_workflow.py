"""Structural pins for the Windows-ARM emulation-validation workflow.

XPLAT-12 close-out: the app's Windows-on-ARM support model is "ship x64,
validate on the hosted windows-11-arm runner under Prism emulation" (a
NATIVE aarch64 sidecar build is infeasible — ctranslate2 publishes no
win-arm64 wheels, so faster-whisper cannot run natively; documented in
review.md + the workflow header).

These pins keep the validation workflow from silently rotting: the label,
the artifact contract, the C-CI-14 launch pattern, and the evidence
upload must all stay present, because a green run here is the recorded
ARM-host validation evidence.

Source-text assertions (headless; same style as tests/tauri/mig15-19).
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "tauri-windows-arm-validation.yml"


def _workflow_text() -> str:
    assert WORKFLOW.exists(), f"workflow file missing: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


class TestArmValidationWorkflowContract:
    def test_targets_the_hosted_arm_runner(self) -> None:
        """The job MUST run on windows-11-arm — the whole point is real
        ARM-hardware validation without owning ARM hardware."""
        assert "runs-on: windows-11-arm" in _workflow_text()

    def test_downloads_the_x64_installer_artifact(self) -> None:
        """The artifact contract must match the x64 build's literal
        artifact name (tauri-windows-installer — C-CI-13) so the
        validation always exercises the REAL shipped installer."""
        text = _workflow_text()
        assert "name: tauri-windows-installer" in text
        assert "run-id:" in text, "cross-run download must pin the run id"

    def test_proves_the_runner_is_arm64_and_the_binary_is_x64(self) -> None:
        """Both halves of the emulation premise are asserted: the host is
        ARM64 (else 'emulation' is vacuous) and the installer PE is x64
        (0x8664 — the emulation model)."""
        text = _workflow_text()
        assert "PROCESSOR_ARCHITECTURE" in text
        assert "0x8664" in text

    def test_sidecar_smoke_test_uses_the_cci14_process_pattern(self) -> None:
        """GUI-subsystem PEs must be launched via .NET Process + WaitForExit
        with redirected output (C-CI-14) — `& $exe` never waits and never
        sets $LASTEXITCODE. The ARM ceiling is 420 s (onefile extraction
        of the ~100 MB payload runs under Prism emulation)."""
        text = _workflow_text()
        assert "System.Diagnostics.ProcessStartInfo" in text
        assert "WaitForExit" in text
        assert "420000" in text, "emulated onefile extraction needs the 420 s ceiling"

    def test_installer_failure_and_sidecar_failure_are_hard_gates(self) -> None:
        """A failed silent install or a non-zero sidecar exit must fail the
        run — that is the XPLAT-12 acceptance signal (the x64 backend does
        not tolerate the ARM host)."""
        text = _workflow_text()
        # installer exit-code gate
        assert re.search(r"ExitCode -ne 0", text) or "ExitCode -ne 0" in text
        # sidecar exit-code gate + the explicit NOT-RUN-ON-ARM message
        assert "does NOT run on Windows-ARM" in text

    def test_evidence_is_uploaded(self) -> None:
        """The run must upload its evidence artifact (arch, hashes, exit
        codes) so the validation result is auditable after the fact."""
        text = _workflow_text()
        assert "windows-arm-validation-evidence" in text
        assert "actions/upload-artifact@v6" in text

    def test_does_not_touch_the_fragile_x64_pipeline(self) -> None:
        """The validation workflow is self-contained by design (C-CI-2):
        it must not COMPOSE the fragile x64 pipeline (no workflow_call,
        no uses of a local workflow, no shared job references) — it only
        consumes the x64 run's build ARTIFACTS."""
        text = _workflow_text()
        assert "workflow_call:" not in text, "must not be callable from the fragile x64 pipeline"
        assert "uses: ./" not in text, "must not compose local workflows (standalone validation only)"

    def test_no_node20_actions(self) -> None:
        """C-CI-5: every action must be on its Node-24 major."""
        text = _workflow_text()
        assert "actions/checkout@v5" in text
        assert "actions/download-artifact@v6" in text
        assert "actions/upload-artifact@v6" in text
        assert "upload-artifact@v5" not in text
        assert "download-artifact@v5" not in text
