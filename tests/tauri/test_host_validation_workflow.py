"""Structural pins for the Host Validation workflow (review.md §E close-out).

The §E Cannot-Verify items need real Windows / macOS / Linux desktop
runtimes. This workflow automates the headless-checkable subset on
GitHub-hosted runners and records the interactive remainder as warnings
instead of faking green. These pins keep it from silently rotting: the
three host jobs, the Node-24 action majors, the evidence uploads, and
the no-touch-fragile-pipelines invariant (C-CI-2).

Source-text assertions (headless; same style as test_arm_validation_workflow).
"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "host-validation.yml"


def _workflow_text() -> str:
    assert WORKFLOW.exists(), f"workflow file missing: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


class TestHostValidationWorkflowContract:
    def test_three_host_jobs_present(self) -> None:
        text = _workflow_text()
        assert "windows-host-validation" in text
        assert "macos-host-validation" in text
        assert "linux-host-validation" in text
        assert "runs-on: windows-2022" in text
        assert "runs-on: macos-14" in text
        assert "runs-on: ubuntu-22.04" in text

    def test_windows_job_covers_kill_path_contracts(self) -> None:
        text = _workflow_text()
        assert "VERSION" in text
        assert "PONG" in text
        assert "TerminateProcess" in text
        assert "CloseHandle" in text
        assert "finally:" in text
        assert "KILL_PATH_CONTRACTS=PASS" in text
        assert "win32_console_handler" in text

    def test_windows_job_covers_win32_taskkill_vt1(self) -> None:
        text = _workflow_text()
        assert "install_win32_console_handler" in text
        assert "taskkill" in text
        assert "WM-14" in text or "WM14" in text
        assert "_timeout_utils" in text

    def test_macos_job_covers_notarization_preflight(self) -> None:
        text = _workflow_text()
        assert "macos-key-listener.swift" in text
        assert "NSMicrophoneUsageDescription" in text
        assert "GP-7" in text or "GP7" in text

    def test_linux_job_covers_wmclass_without_autofix(self) -> None:
        text = _workflow_text()
        assert "S1-CR-146" in text
        assert "StartupWMClass" in text
        assert "xprop" in text

    def test_evidence_is_uploaded_per_host(self) -> None:
        text = _workflow_text()
        assert "host-validation-windows" in text
        assert "host-validation-macos" in text
        assert "host-validation-linux" in text
        assert "actions/upload-artifact@v6" in text

    def test_does_not_touch_fragile_pipelines(self) -> None:
        text = _workflow_text()
        assert "workflow_call:" not in text
        assert "uses: ./" not in text

    def test_no_node20_actions(self) -> None:
        text = _workflow_text()
        assert "actions/checkout@v5" in text
        assert "actions/setup-python@v7" in text
        assert "actions/upload-artifact@v6" in text
        assert "actions/download-artifact@v6" in text
        assert "upload-artifact@v5" not in text
        assert "download-artifact@v5" not in text
        assert "setup-python@v6" not in text
