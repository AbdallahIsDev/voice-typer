"""Windows Tauri workflow target-triple parameterization (matrix-based)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "tauri-windows-build.yml"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    """Read the workflow YAML once per module; fail fast if missing."""
    assert WORKFLOW_FILE.is_file(), (
        f"tauri-windows-build.yml not found at {WORKFLOW_FILE}. Did the project layout change?"
    )
    return WORKFLOW_FILE.read_text(encoding="utf-8")


def test_matrix_includes_x86_64_leg(workflow_text: str):
    """The active matrix must include the x86_64 leg."""
    # Locate the matrix.include block. The x86_64 leg has:
    assert re.search(
        r"- arch:\s*x86_64\s*\n"
        r"\s*target:\s*x86_64-pc-windows-msvc\s*\n"
        r"\s*runner:\s*windows-2022\s*\n"
        r"\s*tauri_config:\s*tauri\.windows-x86_64\.conf\.json",
        workflow_text,
        re.MULTILINE,
    ), "Matrix x86_64 leg (target: x86_64-pc-windows-msvc) not found, the primary Windows release path is missing."


def test_aarch64_leg_documented_as_commented_template(workflow_text: str):
    """The aarch64 leg must be preserved as a COMMENTED matrix template."""
    # The commented template preserves the leg's fields. NOTE: the
    assert re.search(
        r"#\s+- arch:\s*aarch64\s*\n"
        r"\s*#\s+target:\s*aarch64-pc-windows-msvc\s*\n"
        r"\s*#\s+runner:\s*windows-11-arm\s*\n"
        r"\s*#\s+tauri_config:\s*tauri\.windows-aarch64\.conf\.json",
        workflow_text,
        re.MULTILINE,
    ), (
        "The commented aarch64 matrix template (arch/target/runner/"
        "tauri_config) must be preserved in tauri-windows-build.yml so "
        "the Windows-on-ARM scaffold is a one-line uncomment away."
    )
    # And it must NOT be an active (uncommented) matrix entry, a live
    assert not re.search(
        r"^\s+- arch:\s*aarch64\s*\n"
        r"^\s+target:\s*aarch64-pc-windows-msvc\s*\n"
        r"^\s+runner:\s*windows-11-arm",
        workflow_text,
        re.MULTILINE,
    ), (
        "aarch64 must NOT be an active matrix entry, GitHub does not "
        "ship a windows-11-arm runner and there is no valid way to gate "
        "the leg off (matrix is unavailable in jobs.<id>.if)."
    )


def test_no_job_level_if_uses_matrix_context(workflow_text: str):
    """The job must NOT gate on ``matrix.enabled`` (or any matrix context)."""
    # No job-level `if:` may reference the matrix context.
    assert not re.search(r"^\s+if:.*matrix\.", workflow_text, re.MULTILINE), (
        "Job-level `if:` must NOT reference the matrix context, the "
        "`matrix` context is unavailable in jobs.<id>.if and GitHub "
        "rejects the workflow file at validation time (0s 'workflow file "
        "issue' on every push). This is a hard GitHub Actions validation "
        "rule, not a lint preference."
    )
    # The invalid construct must not appear as an active line either.
    assert "if: matrix.enabled" not in workflow_text, (
        "The literal `if: matrix.enabled` must not appear in the workflow, it is invalid in job-level if conditions."
    )


def test_env_rust_target_uses_matrix_target(workflow_text: str):
    """``RUST_TARGET`` env var must be sourced from ``matrix.target``."""
    m = re.search(r"^\s+RUST_TARGET:\s*\$\{\{\s*matrix\.target\s*\}\}", workflow_text, re.MULTILINE)
    assert m, (
        "RUST_TARGET env var must be sourced from ${{ matrix.target }} so "
        "the aarch64 leg (when enabled) actually builds for aarch64. "
        "A hardcoded x86_64 literal here would silently keep building "
        "x86_64 even when the aarch64 leg is dispatched."
    )


def test_env_pybs_triple_uses_matrix_target(workflow_text: str):
    """``PYBS_TRIPLE`` env var must be sourced from ``matrix.target``."""
    m = re.search(r"^\s+PYBS_TRIPLE:\s*\$\{\{\s*matrix\.target\s*\}\}", workflow_text, re.MULTILINE)
    assert m, (
        "PYBS_TRIPLE env var must be sourced from ${{ matrix.target }} so "
        "the aarch64 leg downloads the aarch64 python-build-standalone "
        "release (not the x86_64 one)."
    )


def test_cargo_tauri_build_uses_matrix_target(workflow_text: str):
    """The ``cargo tauri build --target`` flag must use ``matrix.target``."""
    step_start = workflow_text.find("name: Build the Tauri app")
    assert step_start != -1, "Build the Tauri app step not found"
    next_step = workflow_text.find("\n      - name:", step_start + 1)
    run_block_slice = workflow_text[step_start:next_step]
    assert "cargo tauri build" in run_block_slice, "cargo tauri build command not found in Build the Tauri app step"
    assert re.search(r"--target\s+\$\{\{\s*matrix\.target\s*\}\}", run_block_slice), (
        "cargo tauri build --target must use ${{ matrix.target }} (dynamic), "
        "not a hardcoded x86_64-pc-windows-msvc literal. Found run block:\n" + run_block_slice
    )
    # And the hardcoded literal must NOT appear on the cargo tauri build line.
    cargo_line = next(
        (ln for ln in run_block_slice.splitlines() if "cargo tauri build" in ln),
        "",
    )
    assert "x86_64-pc-windows-msvc" not in cargo_line, (
        f"cargo tauri build line still hardcodes x86_64-pc-windows-msvc: {cargo_line!r}"
    )


def test_cargo_tauri_build_uses_matrix_tauri_config(workflow_text: str):
    """The ``--config`` flag must use ``matrix.tauri_config``."""
    step_start = workflow_text.find("name: Build the Tauri app")
    assert step_start != -1, "Build the Tauri app step not found"
    next_step = workflow_text.find("\n      - name:", step_start + 1)
    run_block_slice = workflow_text[step_start:next_step]
    assert re.search(r"--config\s+\$\{\{\s*matrix\.tauri_config\s*\}\}", run_block_slice), (
        "cargo tauri build --config must use ${{ matrix.tauri_config }} so "
        "each leg picks its own arch-specific Tauri config. Run block:\n" + run_block_slice
    )


def test_nuitka_sidecar_output_uses_matrix_target(workflow_text: str):
    """The Nuitka sidecar build step must emit ``python-sidecar-${{ matrix.target }}.exe``."""
    assert "python-sidecar-${{ matrix.target }}.exe" in workflow_text, (
        "Nuitka sidecar output filename must use ${{ matrix.target }} so "
        "the aarch64 leg produces python-sidecar-aarch64-pc-windows-msvc.exe "
        "instead of overwriting the x86_64 binary."
    )


def test_workflow_has_no_prewarm_build_step(workflow_text: str):
    """The workflow must NOT build a prewarm binary."""
    assert "Build the prewarm binary" not in workflow_text, (
        "tauri-windows-build.yml must not contain a prewarm build step, "
        "the standalone prewarm binary was removed per "
        "plan-runtime-pack-split §6.2 P-1."
    )
    assert "prewarm-${{ matrix.target }}.exe" not in workflow_text, (
        "No Nuitka prewarm output filename may appear, the prewarm build "
        "step was removed per plan-runtime-pack-split §6.2 P-1."
    )


def test_workflow_does_not_reference_prewarm_filename(workflow_text: str):
    """The literal ``prewarm-x86_64-pc-windows-msvc.exe`` must be GONE."""
    assert "prewarm-x86_64-pc-windows-msvc.exe" not in workflow_text, (
        "The literal 'prewarm-x86_64-pc-windows-msvc.exe' must not appear "
        "in the workflow, the standalone prewarm binary was removed per "
        "plan-runtime-pack-split §6.2 P-1 (the mig18 signing test pins "
        "its ABSENCE)."
    )


def test_default_x86_64_literal_preserved(workflow_text: str):
    """The literal ``x86_64-pc-windows-msvc`` must still appear in the YAML."""
    assert "x86_64-pc-windows-msvc" in workflow_text, (
        "The literal 'x86_64-pc-windows-msvc' must still appear in the "
        "workflow (as the x86_64 leg's matrix.target) so existing cutover "
        "tests pass."
    )


def test_default_sidecar_filename_preserved(workflow_text: str):
    """The literal ``python-sidecar-x86_64-pc-windows-msvc.exe`` must appear."""
    assert "python-sidecar-x86_64-pc-windows-msvc.exe" in workflow_text, (
        "The literal 'python-sidecar-x86_64-pc-windows-msvc.exe' must still "
        "appear (in a documentation comment listing the default filenames) "
        "so tests/tauri/mig18/test_windows_signing.py keeps passing."
    )


# literal must NOT appear; see test_workflow_does_not_reference_prewarm_filename
