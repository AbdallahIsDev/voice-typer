"""Structural pins for the Windows-ARM emulation-validation workflow."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "tauri-windows-arm-validation.yml"


def _workflow_text() -> str:
    assert WORKFLOW.exists(), f"workflow file missing: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


class TestArmValidationWorkflowContract:
    def test_targets_the_hosted_arm_runner(self) -> None:
        """The job MUST run on windows-11-arm, the whole point is real"""
        assert "runs-on: windows-11-arm" in _workflow_text()

    def test_downloads_the_x64_installer_artifact(self) -> None:
        """The artifact contract must match the x64 build's literal"""
        text = _workflow_text()
        assert "name: tauri-windows-installer" in text
        assert "run-id:" in text, "cross-run download must pin the run id"

    def test_proves_the_runner_is_arm64_and_the_binary_is_x64(self) -> None:
        """Both halves of the emulation premise are asserted: the host is"""
        text = _workflow_text()
        assert "PROCESSOR_ARCHITECTURE" in text
        assert "0x8664" in text

    def test_sidecar_smoke_test_uses_the_cci14_process_pattern(self) -> None:
        """GUI-subsystem PEs must be launched via .NET Process + WaitForExit"""
        text = _workflow_text()
        assert "System.Diagnostics.ProcessStartInfo" in text
        assert "WaitForExit" in text
        assert "420000" in text, "emulated onefile extraction needs the 420 s ceiling"

    def test_installer_failure_and_sidecar_failure_are_hard_gates(self) -> None:
        """run: that is the XPLAT-12 acceptance signal (the x64 backend does"""
        text = _workflow_text()
        assert re.search(r"ExitCode -ne 0", text) or "ExitCode -ne 0" in text
        assert "does NOT run on Windows-ARM" in text

    def test_evidence_is_uploaded(self) -> None:
        """The run must upload its evidence artifact (arch, hashes, exit"""
        text = _workflow_text()
        assert "windows-arm-validation-evidence" in text
        assert "actions/upload-artifact@v6" in text

    def test_does_not_touch_the_fragile_x64_pipeline(self) -> None:
        """The validation workflow is self-contained by design (C-CI-2):"""
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
