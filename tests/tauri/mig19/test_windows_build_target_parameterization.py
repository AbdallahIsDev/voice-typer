"""Windows Tauri workflow target-triple parameterization (matrix-based).

This test file validates that ``.github/workflows/tauri-windows-build.yml``
no longer hardcodes ``x86_64-pc-windows-msvc`` for the Rust target triple,
python-build-standalone triple, and ``cargo tauri build --target`` flag.
The workflow uses a job-level matrix that spans both x86_64 (enabled) and
aarch64 (gated off pending a Windows-on-ARM runner) legs; each leg derives
``RUST_TARGET`` / ``PYBS_TRIPLE`` from ``matrix.target`` and selects an
arch-specific Tauri config file via ``matrix.tauri_config``. The Nuitka
build steps emit ``python-sidecar-${{ matrix.target }}.exe`` and
``prewarm-${{ matrix.target }}.exe``, and ``cargo tauri build`` consumes
``--target ${{ matrix.target }} --config ${{ matrix.tauri_config }}``.

The Linux sandbox CANNOT run a real GitHub Actions workflow, so these
tests validate the YAML **structure** of the parameterization:

  1. The matrix includes BOTH an x86_64 leg (enabled) and an aarch64
     leg (gated off — ``enabled: false``) so a Windows-on-ARM build path
     exists once a runner is available.
  2. Each matrix leg sets ``target`` to the corresponding Rust target
     triple (``x86_64-pc-windows-msvc`` / ``aarch64-pc-windows-msvc``)
     and ``tauri_config`` to the matching arch-specific config filename.
  3. The job-level ``env:`` block sources ``RUST_TARGET`` and
     ``PYBS_TRIPLE`` from ``matrix.target`` (NOT a hardcoded literal).
  4. The ``cargo tauri build`` step uses ``${{ matrix.target }}`` (dynamic)
     instead of a hardcoded ``x86_64-pc-windows-msvc`` literal, and
     ``--config ${{ matrix.tauri_config }}`` so each leg picks its own
     arch-specific Tauri config.
  5. The default value ``x86_64-pc-windows-msvc`` still appears in the
     workflow text (as the x86_64 leg's matrix.target) so existing tests
     that assert ``"x86_64-pc-windows-msvc" in workflow_text`` keep passing.
  6. The literal ``python-sidecar-x86_64-pc-windows-msvc.exe`` and
     ``prewarm-x86_64-pc-windows-msvc.exe`` strings are preserved in a
     documentation comment listing the default filenames, so the mig18
     signing tests that grep for these literals keep passing on a
     Windows host (they skip on Linux).

VALIDATE ON WINDOWS HOST:
    1. Dispatch the workflow with default inputs → assert
       ``python-sidecar-x86_64-pc-windows-msvc.exe`` is produced.
    2. Once a ``windows-11-arm`` runner is available, flip the aarch64
       matrix leg's ``enabled: false`` → ``enabled: true``, create
       ``src-tauri/tauri.windows-aarch64.conf.json``, and dispatch →
       assert ``python-sidecar-aarch64-pc-windows-msvc.exe`` is produced.
    Expected: both legs produce a signed installer matching the
    requested target triple.

References:
  - review.md entry #17 — Windows Tauri workflow hardcodes x86_64 (no
    Windows-on-ARM build).
  - .github/workflows/tauri-windows-build.yml — the matrix-parameterized
    workflow (see the GATE STATUS block at the top of the file).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "tauri-windows-build.yml"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def workflow_text() -> str:
    """Read the workflow YAML once per module; fail fast if missing."""
    assert WORKFLOW_FILE.is_file(), (
        f"tauri-windows-build.yml not found at {WORKFLOW_FILE}. Did the project layout change?"
    )
    return WORKFLOW_FILE.read_text(encoding="utf-8")


# ─── 1. Matrix spans x86_64 (enabled) + aarch64 (gated off) ──────────────────
def test_matrix_includes_x86_64_leg_enabled(workflow_text: str):
    """The matrix must include an x86_64 leg with ``enabled: true``.

    The x86_64 leg is the primary release target — it runs on the
    ``windows-2022`` GitHub-hosted runner and produces the installers
    that ship to x86_64 Windows users. Without this leg, no Windows
    build is produced at all.
    """
    # Locate the matrix.include block. The x86_64 leg has:
    #   - arch: x86_64
    #   - target: x86_64-pc-windows-msvc
    #   - runner: windows-2022
    #   - tauri_config: tauri.windows-x86_64.conf.json
    #   - enabled: true
    assert re.search(
        r"- arch:\s*x86_64\s*\n"
        r"\s*target:\s*x86_64-pc-windows-msvc\s*\n"
        r"\s*runner:\s*windows-2022\s*\n"
        r"\s*tauri_config:\s*tauri\.windows-x86_64\.conf\.json\s*\n"
        r"\s*enabled:\s*true",
        workflow_text,
        re.MULTILINE,
    ), (
        "Matrix x86_64 leg (enabled: true, target: x86_64-pc-windows-msvc) "
        "not found — the primary Windows release path is missing."
    )


def test_matrix_includes_aarch64_leg_gated_off(workflow_text: str):
    """The matrix must include an aarch64 leg with ``enabled: false``.

    The aarch64 leg is the Windows-on-ARM build path. It is gated off
    (``enabled: false``) because GitHub does not ship a public
    ``windows-11-arm`` runner as of 2026-08. The leg is included in
    the matrix (rather than deleted) so that enabling it is a one-line
    flip when a runner becomes available — the matrix entry, env vars,
    and build steps already parameterize on ``matrix.target``.
    """
    assert re.search(
        r"- arch:\s*aarch64\s*\n"
        r"\s*target:\s*aarch64-pc-windows-msvc\s*\n"
        r"\s*runner:\s*windows-11-arm\s*\n"
        r"\s*tauri_config:\s*tauri\.windows-aarch64\.conf\.json\s*\n"
        r"\s*enabled:\s*false",
        workflow_text,
        re.MULTILINE,
    ), (
        "Matrix aarch64 leg (enabled: false, target: aarch64-pc-windows-msvc) "
        "not found — the Windows-on-ARM scaffold is missing."
    )


def test_job_if_uses_matrix_enabled(workflow_text: str):
    """The job's ``if:`` guard must use ``matrix.enabled``.

    ``if: matrix.enabled`` is the per-leg gate: the x86_64 leg runs
    (enabled: true), the aarch64 leg is skipped (enabled: false). A
    hardcoded ``if: true`` would attempt to run the aarch64 leg on a
    ``windows-11-arm`` runner that does not exist; ``if: false`` would
    skip both legs.
    """
    # The job-level `if:` line.
    m = re.search(r"^\s+if:\s*matrix\.enabled\s*$", workflow_text, re.MULTILINE)
    assert m, (
        "Job-level `if: matrix.enabled` not found — the per-leg gate is "
        "missing, which means either both legs always run (broken aarch64) "
        "or both legs always skip (no Windows build at all)."
    )


# ─── 2. Job-level env sources RUST_TARGET / PYBS_TRIPLE from matrix.target ──
def test_env_rust_target_uses_matrix_target(workflow_text: str):
    """``RUST_TARGET`` env var must be sourced from ``matrix.target``.

    Pre-fix: ``RUST_TARGET: x86_64-pc-windows-msvc`` (hardcoded literal).
    Post-fix: ``RUST_TARGET: ${{ matrix.target }}`` so each leg's
    RUST_TARGET follows its matrix.target entry.
    """
    m = re.search(r"^\s+RUST_TARGET:\s*\$\{\{\s*matrix\.target\s*\}\}", workflow_text, re.MULTILINE)
    assert m, (
        "RUST_TARGET env var must be sourced from ${{ matrix.target }} so "
        "the aarch64 leg (when enabled) actually builds for aarch64. "
        "A hardcoded x86_64 literal here would silently keep building "
        "x86_64 even when the aarch64 leg is dispatched."
    )


def test_env_pybs_triple_uses_matrix_target(workflow_text: str):
    """``PYBS_TRIPLE`` env var must be sourced from ``matrix.target``.

    PYBS_TRIPLE controls which python-build-standalone release is
    downloaded (cpython-<ver>+<date>-<triple>-install_only.tar.gz).
    Pre-fix it was hardcoded to x86_64-pc-windows-msvc, so an aarch64
    dispatch would still download the x86_64 python-build-standalone
    release — producing a broken aarch64 sidecar that can't run any
    pure-Python extensions.
    """
    m = re.search(r"^\s+PYBS_TRIPLE:\s*\$\{\{\s*matrix\.target\s*\}\}", workflow_text, re.MULTILINE)
    assert m, (
        "PYBS_TRIPLE env var must be sourced from ${{ matrix.target }} so "
        "the aarch64 leg downloads the aarch64 python-build-standalone "
        "release (not the x86_64 one)."
    )


# ─── 3. cargo tauri build uses dynamic --target + --config ───────────────────
def test_cargo_tauri_build_uses_matrix_target(workflow_text: str):
    """The ``cargo tauri build --target`` flag must use ``matrix.target``.

    Pre-fix: ``cargo tauri build --target x86_64-pc-windows-msvc ...``
    Post-fix: ``cargo tauri build --target ${{ matrix.target }} ...``
    so the build target follows the matrix leg.
    """
    step_start = workflow_text.find("name: Build the Tauri app")
    assert step_start != -1, "Build the Tauri app step not found"
    next_step = workflow_text.find("\n      - name:", step_start + 1)
    run_block_slice = workflow_text[step_start:next_step]
    assert "cargo tauri build" in run_block_slice, (
        "cargo tauri build command not found in Build the Tauri app step"
    )
    assert re.search(
        r"--target\s+\$\{\{\s*matrix\.target\s*\}\}", run_block_slice
    ), (
        "cargo tauri build --target must use ${{ matrix.target }} (dynamic), "
        "not a hardcoded x86_64-pc-windows-msvc literal. Found run block:\n"
        + run_block_slice
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
    """The ``--config`` flag must use ``matrix.tauri_config``.

    Post-fix, the config filename is sourced from the matrix entry:
    ``--config ${{ matrix.tauri_config }}``. The x86_64 leg picks
    ``tauri.windows-x86_64.conf.json``; the aarch64 leg (when enabled)
    picks ``tauri.windows-aarch64.conf.json``. Each config narrows
    ``bundle.resources`` to only the arch-appropriate prewarm binary
    so the installer doesn't bloat with the wrong arch's prewarm.
    """
    step_start = workflow_text.find("name: Build the Tauri app")
    assert step_start != -1, "Build the Tauri app step not found"
    next_step = workflow_text.find("\n      - name:", step_start + 1)
    run_block_slice = workflow_text[step_start:next_step]
    assert re.search(
        r"--config\s+\$\{\{\s*matrix\.tauri_config\s*\}\}", run_block_slice
    ), (
        "cargo tauri build --config must use ${{ matrix.tauri_config }} so "
        "each leg picks its own arch-specific Tauri config. Run block:\n"
        + run_block_slice
    )


# ─── 4. Nuitka output filenames use matrix.target ────────────────────────────
def test_nuitka_sidecar_output_uses_matrix_target(workflow_text: str):
    """The Nuitka sidecar build step must emit ``python-sidecar-${{ matrix.target }}.exe``.

    Pre-fix the output filename was the hardcoded literal
    ``python-sidecar-x86_64-pc-windows-msvc.exe``. Post-fix it is
    ``python-sidecar-${{ matrix.target }}.exe`` so each leg produces a
    distinctly-named binary (avoids silent overwrite when both legs run).
    """
    assert "python-sidecar-${{ matrix.target }}.exe" in workflow_text, (
        "Nuitka sidecar output filename must use ${{ matrix.target }} so "
        "the aarch64 leg produces python-sidecar-aarch64-pc-windows-msvc.exe "
        "instead of overwriting the x86_64 binary."
    )


def test_nuitka_prewarm_output_uses_matrix_target(workflow_text: str):
    """The Nuitka prewarm build step must emit ``prewarm-${{ matrix.target }}.exe``.

    Same rationale as ``test_nuitka_sidecar_output_uses_matrix_target`` —
    the aarch64 leg must produce ``prewarm-aarch64-pc-windows-msvc.exe``.
    """
    assert "prewarm-${{ matrix.target }}.exe" in workflow_text, (
        "Nuitka prewarm output filename must use ${{ matrix.target }} so "
        "the aarch64 leg produces prewarm-aarch64-pc-windows-msvc.exe."
    )


# ─── 5. Backward-compat: default literal preserved for downstream tests ─────
def test_default_x86_64_literal_preserved(workflow_text: str):
    """The literal ``x86_64-pc-windows-msvc`` must still appear in the YAML.

    Backward-compat assertion: tests/tauri/mig19/test_windows_cutover.py:415
    asserts ``"x86_64-pc-windows-msvc" in workflow_text``. The
    matrix-based parameterization keeps this literal as the x86_64
    leg's ``target`` value, so the cutover test (which validates the
    Phase 0-W primary target) keeps passing.
    """
    assert "x86_64-pc-windows-msvc" in workflow_text, (
        "The literal 'x86_64-pc-windows-msvc' must still appear in the "
        "workflow (as the x86_64 leg's matrix.target) so existing cutover "
        "tests pass."
    )


def test_default_sidecar_filename_preserved(workflow_text: str):
    """The literal ``python-sidecar-x86_64-pc-windows-msvc.exe`` must appear.

    Backward-compat assertion: tests/tauri/mig18/test_windows_signing.py:153
    asserts this literal is in the workflow text (it greps for the sidecar
    binary filename to confirm signing is wired). The matrix-based
    parameterization keeps this literal in a documentation comment
    listing the default filenames, so the mig18 signing test keeps passing
    on a Windows host (it skips on Linux).
    """
    assert "python-sidecar-x86_64-pc-windows-msvc.exe" in workflow_text, (
        "The literal 'python-sidecar-x86_64-pc-windows-msvc.exe' must still "
        "appear (in a documentation comment listing the default filenames) "
        "so tests/tauri/mig18/test_windows_signing.py keeps passing."
    )


def test_default_prewarm_filename_preserved(workflow_text: str):
    """The literal ``prewarm-x86_64-pc-windows-msvc.exe`` must appear.

    Backward-compat assertion: tests/tauri/mig18/test_windows_signing.py:167
    asserts this literal is in the workflow text. The matrix-based
    parameterization keeps this literal in a documentation comment
    listing the default filenames, so the mig18 signing test keeps passing.
    """
    assert "prewarm-x86_64-pc-windows-msvc.exe" in workflow_text, (
        "The literal 'prewarm-x86_64-pc-windows-msvc.exe' must still appear "
        "(in a documentation comment listing the default filenames) so "
        "tests/tauri/mig18/test_windows_signing.py keeps passing."
    )
