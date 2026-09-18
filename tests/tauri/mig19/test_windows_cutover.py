"""MIG-1.9 Phase 5 Check: Windows packaging contract (Tauri host only).

The project ships exactly ONE desktop shell: the Tauri Rust host. This
module pins the live Windows packaging contract the release pipeline
depends on:

  - ``.github/workflows/tauri-windows-build.yml`` builds the Windows
    host with ``cargo tauri build`` for ``x86_64-pc-windows-msvc`` on
    ``windows-2022``, with the x86_64 matrix leg active and no
    ``matrix.*`` reference in a job-level ``if:`` (the ``matrix``
    context is unavailable there, GitHub rejects the file at
    validation time).
  - ``.github/workflows/tauri-build.yml`` is the cross-platform
    orchestrator; its ``build-windows`` job fans out to the
    per-platform Windows workflow.
  - ``tauri-windows-build.yml`` documents the Phase 0-W host
    validation gate (``docs/migration/windows-validation-runbook.md``)
    so the release engineer knows what must pass before the push/PR
    triggers are uncommented.

VALIDATE ON WINDOWS HOST:
    1. Build the installer: cd src-tauri; cargo tauri build --target x86_64-pc-windows-msvc
    2. Install the NSIS installer → verify the app launches
    3. Verify the sidecar runs (Task Manager → python-sidecar-*.exe)

References:
  - ADR-0020, the desktop-runtime migration record (Windows is the
    first platform in the per-platform order).
  - docs/migration/windows-validation-runbook.md, the Phase 0-W 9-point
    host gate.
  - .github/workflows/tauri-windows-build.yml, the Windows host CI build.
  - .github/workflows/tauri-build.yml, the cross-platform orchestrator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig19/test_windows_cutover.py.
# Path from file → root:
#   parents[0] = mig19/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TAURI_WINDOWS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-windows-build.yml"
TAURI_BUILD_ORCHESTRATOR = PROJECT_ROOT / ".github" / "workflows" / "tauri-build.yml"


# ─── Fixtures ────────────────────────────────────────────────────────────────
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


# ─── Windows host build ─────────────────────────────────────────────────────


def test_tauri_windows_workflow_exists(tauri_windows_workflow_text: str):
    """The Tauri Windows build workflow must exist (Tauri host build).

    ``.github/workflows/tauri-windows-build.yml`` is the CI workflow
    that builds the Tauri host (NSIS + MSI installers) for Windows.
    Without it, the Tauri host cannot be built in CI.
    """
    assert "tauri-windows-build" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must reference its own workflow name."
    )
    assert "cargo tauri build" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must invoke `cargo tauri build` to produce the Tauri host installer."
    )


def test_tauri_windows_workflow_targets_msvc(
    tauri_windows_workflow_text: str,
):
    """The Tauri Windows build workflow must target
    ``x86_64-pc-windows-msvc``.

    Per ADR-0020 §"Phase 0-W (Windows)" + the per-platform table, the
    Windows release is x86_64 first (aarch64 follows once GitHub ships
    a ``windows-11-arm`` runner). The Tauri build must produce an
    installer for that target triple.
    """
    assert "x86_64-pc-windows-msvc" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must target x86_64-pc-windows-msvc (the Phase 0-W target triple)."
    )


def test_tauri_build_orchestrator_calls_windows_workflow(
    tauri_build_orchestrator_text: str,
):
    """The top-level Tauri orchestrator workflow must call the Windows
    per-platform workflow.

    ``.github/workflows/tauri-build.yml`` is the cross-cutting
    orchestrator that fans out to the per-platform workflows. It must
    call ``tauri-windows-build.yml`` so a dispatch actually produces the
    Windows installer.
    """
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
    """The Tauri Windows workflow's job must be active (runnable).

    ``tauri-windows-build.yml`` was originally disabled (``if: false``
    on the job) so it did NOT run on push or PR until the Phase 0-W host
    validation gate (see ``docs/migration/windows-validation-runbook.md``)
    had passed on a real Windows 10 22H2 / Windows 11 host. The workflow
    is now enabled and runs via ``workflow_dispatch`` /
    ``workflow_call``.

    The matrix refactor parameterized the job over ``matrix.target``
    (x86_64 active + aarch64 scaffold). An early revision gated the
    per-leg build with a job-level ``if: matrix.enabled``, but the
    ``matrix`` context is NOT available in ``jobs.<id>.if``, GitHub
    Actions rejects the workflow file at validation time (a 0s
    "workflow file issue" run on every push, with no jobs). The invalid
    gate was therefore removed entirely: the active matrix contains
    exactly one leg (x86_64), so every matrix job should run. The
    aarch64 leg is preserved as a commented matrix template for when
    GitHub ships a ``windows-11-arm`` runner.
    """
    # The invalid per-leg gate must be gone (matrix is unavailable in
    # job-level `if:`, this is a hard GitHub Actions validation rule).
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
    """The Tauri Windows workflow must reference the Phase 0-W host
    validation runbook.

    The workflow header comment must point to
    ``docs/migration/windows-validation-runbook.md`` so the release
    engineer knows what host validation to run BEFORE un-commenting the
    push/PR triggers.
    """
    assert "windows-validation-runbook" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must reference "
        "docs/migration/windows-validation-runbook.md (the Phase 0-W "
        "host validation gate procedure)."
    )
