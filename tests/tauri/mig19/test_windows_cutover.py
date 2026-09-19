"""Phase 5 Check: Windows packaging contract (Tauri host only)."""

from __future__ import annotations

from pathlib import Path

import pytest

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TAURI_WINDOWS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-windows-build.yml"
TAURI_BUILD_ORCHESTRATOR = PROJECT_ROOT / ".github" / "workflows" / "tauri-build.yml"


@pytest.fixture(scope="module")
def tauri_windows_workflow_text() -> str:
    """Read the Tauri Windows CI workflow once per module."""
    assert TAURI_WINDOWS_WORKFLOW.is_file(), f"tauri-windows-build.yml not found at {TAURI_WINDOWS_WORKFLOW}."
    return TAURI_WINDOWS_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tauri_build_orchestrator_text() -> str:
    """Read the Tauri orchestrator workflow once per module."""
    assert TAURI_BUILD_ORCHESTRATOR.is_file(), f"tauri-build.yml not found at {TAURI_BUILD_ORCHESTRATOR}."
    return TAURI_BUILD_ORCHESTRATOR.read_text(encoding="utf-8")


def test_tauri_windows_workflow_exists(tauri_windows_workflow_text: str):
    """The Tauri Windows build workflow must exist (Tauri host build)."""
    assert "tauri-windows-build" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must reference its own workflow name."
    )
    assert "cargo tauri build" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must invoke `cargo tauri build` to produce the Tauri host installer."
    )


def test_tauri_windows_workflow_targets_msvc(
    tauri_windows_workflow_text: str,
):
    """The Tauri Windows build workflow must target"""
    assert "x86_64-pc-windows-msvc" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must target x86_64-pc-windows-msvc (the Phase 0-W target triple)."
    )


def test_tauri_build_orchestrator_calls_windows_workflow(
    tauri_build_orchestrator_text: str,
):
    """The top-level Tauri orchestrator workflow must call the Windows"""
    assert "tauri-windows-build.yml" in tauri_build_orchestrator_text, (
        "tauri-build.yml orchestrator must call tauri-windows-build.yml (the per-platform Windows workflow)."
    )
    assert "build-windows" in tauri_build_orchestrator_text, (
        "tauri-build.yml orchestrator must have a `build-windows` job "
        "that fans out to the per-platform Windows workflow."
    )


def test_ci_builds_tauri_host_for_windows(tauri_windows_workflow_text: str):
    """The repo's CI builds the Tauri host."""
    assert "cargo tauri build" in tauri_windows_workflow_text, (
        "Tauri host build path (tauri-windows-build.yml) must invoke `cargo tauri build`."
    )


def test_tauri_windows_workflow_has_active_job_after_cutover(
    tauri_windows_workflow_text: str,
):
    """The Tauri Windows workflow's job must be active (runnable)."""
    assert "if: matrix.enabled" not in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must NOT contain `if: matrix.enabled`, "
        "the `matrix` context is unavailable in jobs.<id>.if and GitHub "
        "rejects the workflow file at validation time (0s 'workflow file "
        "issue' on every push)."
    )
    # The active x86_64 matrix leg must exist (the build path runs).
    assert "arch: x86_64" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must have an active x86_64 matrix leg."
    )
    assert "windows-2022" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must run the x86_64 leg on windows-2022."
    )
    # The header comment must reference Phase 0-W.
    assert "Phase 0-W" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must reference Phase 0-W in its header "
        "comment (the host gate documented in "
        "docs/migration/windows-validation-runbook.md)."
    )


def test_tauri_windows_workflow_references_validation_runbook(
    tauri_windows_workflow_text: str,
):
    """The Tauri Windows workflow must reference the Phase 0-W host"""
    assert "windows-validation-runbook" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must reference "
        "docs/migration/windows-validation-runbook.md (the Phase 0-W "
        "host validation gate procedure)."
    )
