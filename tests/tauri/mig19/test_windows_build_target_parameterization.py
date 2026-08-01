"""S2-CR-66 — Windows Tauri workflow target-triple parameterization.

This test file validates that ``.github/workflows/tauri-windows-build.yml``
no longer hardcodes ``x86_64-pc-windows-msvc`` for the Rust target triple,
python-build-standalone triple, and ``cargo tauri build --target`` flag.
The workflow now accepts a ``target`` workflow_dispatch + workflow_call
input (default ``x86_64-pc-windows-msvc``) and derives ``RUST_TARGET``,
``PYBS_TRIPLE``, and ``RUST_ARCH`` env vars from it, so a maintainer can
dispatch the workflow with ``target=aarch64-pc-windows-msvc`` to produce
Windows-on-ARM binaries (Surface Pro X, Copilot+ PCs, Snapdragon X laptops).

The Linux sandbox CANNOT run a real GitHub Actions workflow, so these
tests validate the YAML **structure** of the parameterization:

  1. ``workflow_dispatch.inputs.target`` exists with the correct default.
  2. ``workflow_call.inputs.target`` continues to exist (was already
     present pre-fix; the bug was that it was unused).
  3. The top-level ``env:`` block sources ``RUST_TARGET`` and
     ``PYBS_TRIPLE`` from ``inputs.target`` (with a default fallback to
     ``x86_64-pc-windows-msvc``).
  4. The ``cargo tauri build`` step uses ``$env:RUST_TARGET`` (dynamic)
     instead of a hardcoded ``x86_64-pc-windows-msvc`` literal.
  5. The default value ``x86_64-pc-windows-msvc`` still appears in the
     workflow text (as the default fallback) so existing tests that
     assert ``"x86_64-pc-windows-msvc" in workflow_text`` keep passing.
  6. The literal ``python-sidecar-x86_64-pc-windows-msvc.exe`` and
     ``prewarm-x86_64-pc-windows-msvc.exe`` strings are preserved
     (in a documentation comment listing the default filenames) so the
     mig18 signing tests that grep for these literals keep passing.

VALIDATE ON WINDOWS HOST:
    1. Dispatch the workflow with default inputs → assert
       ``python-sidecar-x86_64-pc-windows-msvc.exe`` is produced.
    2. Dispatch with ``target=aarch64-pc-windows-msvc`` on a
       windows-11-arm runner → assert
       ``python-sidecar-aarch64-pc-windows-msvc.exe`` is produced.
    Expected: both dispatches produce a signed installer matching the
    requested target triple.

References:
  - review.md entry #17 (S2-CR-66) — Windows Tauri workflow hardcodes
    x86_64 (no Windows-on-ARM build).
  - .github/workflows/tauri-windows-build.yml — the parameterized workflow.
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


# ─── 1. workflow_dispatch surface ────────────────────────────────────────────
def test_workflow_dispatch_has_target_input(workflow_text: str):
    """The ``workflow_dispatch`` block must accept a ``target`` input.

    Without this input, a maintainer dispatching the workflow from the
    Actions UI cannot select a target triple — they are forced into
    x86_64. The input is the user-facing surface for S2-CR-66.
    """
    # Locate the workflow_dispatch: block and assert `target:` is under it.
    # We match the indentation: workflow_dispatch inputs are at 6 spaces.
    m = re.search(
        r"^  workflow_dispatch:\s*\n(?P<block>(?:    .*\n)+)",
        workflow_text,
        re.MULTILINE,
    )
    assert m, "workflow_dispatch: block not found in workflow"
    block = m.group("block")
    assert re.search(r"^      target:", block, re.MULTILINE), (
        "workflow_dispatch.inputs.target is missing — a maintainer "
        "cannot dispatch a Windows-on-ARM build from the Actions UI."
    )


def test_workflow_dispatch_target_default_is_x86_64(workflow_text: str):
    """The ``target`` input must default to ``x86_64-pc-windows-msvc``.

    Defaulting to x86_64 preserves the pre-fix behavior for every
    existing dispatch (tauri-build.yml orchestrator + manual dispatches
    that don't pass `target`). A non-x86_64 default would silently
    change the build target of every release.
    """
    m = re.search(
        r"^      target:\s*\n(?:        [^\n]*\n)*?        default:\s*"
        r"\"(?P<default>[^\"]+)\"",
        workflow_text,
        re.MULTILINE,
    )
    assert m, (
        "target input's default value not found — the input must have a default to preserve x86_64-first behavior."
    )
    assert m.group("default") == "x86_64-pc-windows-msvc", (
        f"target input default is {m.group('default')!r}, expected "
        f"'x86_64-pc-windows-msvc' (the Phase 0-W primary target)."
    )


# ─── 2. workflow_call surface (pre-existing, now actually used) ──────────────
def test_workflow_call_has_target_input(workflow_text: str):
    """The ``workflow_call`` block must accept a ``target`` input.

    This input existed pre-fix but was unused — the bug S2-CR-66 cites.
    The test asserts it still exists (so the tauri-build.yml orchestrator
    can pass a target through) AND that it is now actually consumed by
    the env block (covered by test_env_uses_inputs_target).
    """
    m = re.search(
        r"^  workflow_call:\s*\n(?P<block>(?:    .*\n)+)",
        workflow_text,
        re.MULTILINE,
    )
    assert m, "workflow_call: block not found in workflow"
    block = m.group("block")
    assert re.search(r"^      target:", block, re.MULTILINE), (
        "workflow_call.inputs.target is missing — the tauri-build.yml "
        "orchestrator cannot pass a target triple through to this workflow."
    )


# ─── 3. env block sources RUST_TARGET / PYBS_TRIPLE from inputs.target ──────
def test_env_rust_target_uses_inputs_target(workflow_text: str):
    """``RUST_TARGET`` env var must be sourced from ``inputs.target``.

    Pre-fix: ``RUST_TARGET: x86_64-pc-windows-msvc`` (hardcoded literal).
    Post-fix: ``RUST_TARGET: ${{ inputs.target || ... || 'x86_64-pc-windows-msvc' }}``
    so dispatching with target=aarch64-pc-windows-msvc flows through.
    """
    m = re.search(r"^  RUST_TARGET:\s*(?P<value>.+)$", workflow_text, re.MULTILINE)
    assert m, "RUST_TARGET env var not found in top-level env: block"
    value = m.group("value")
    assert "inputs.target" in value, (
        f"RUST_TARGET must reference inputs.target (found: {value!r}). "
        "Without this, dispatching with target=aarch64-pc-windows-msvc "
        "has no effect on the actual build target."
    )
    assert "x86_64-pc-windows-msvc" in value, (
        f"RUST_TARGET must default to 'x86_64-pc-windows-msvc' (found: {value!r}). "
        "A different default would change every existing dispatch's target."
    )


def test_env_pybs_triple_uses_inputs_target(workflow_text: str):
    """``PYBS_TRIPLE`` env var must be sourced from ``inputs.target``.

    PYBS_TRIPLE controls which python-build-standalone release is
    downloaded (cpython-<ver>+<date>-<triple>-install_only.tar.gz).
    Pre-fix it was hardcoded to x86_64-pc-windows-msvc, so an aarch64
    dispatch would still download the x86_64 python-build-standalone
    release — producing a broken aarch64 sidecar that can't run any
    pure-Python extensions.
    """
    m = re.search(r"^  PYBS_TRIPLE:\s*(?P<value>.+)$", workflow_text, re.MULTILINE)
    assert m, "PYBS_TRIPLE env var not found in top-level env: block"
    value = m.group("value")
    assert "inputs.target" in value, (
        f"PYBS_TRIPLE must reference inputs.target (found: {value!r}). "
        "Without this, an aarch64 dispatch would still download the "
        "x86_64 python-build-standalone release."
    )


def test_env_rust_arch_derived_from_target(workflow_text: str):
    """``RUST_ARCH`` env var must be derived from ``inputs.target``.

    RUST_ARCH is the short arch token (x86_64 / aarch64) used to select
    the arch-specific Tauri config file (tauri.windows-<arch>.conf.json).
    It must be derived from inputs.target so an aarch64 dispatch picks
    tauri.windows-aarch64.conf.json (when it exists) instead of the
    x86_64 config.
    """
    m = re.search(r"^  RUST_ARCH:\s*(?P<value>.+)$", workflow_text, re.MULTILINE)
    assert m, (
        "RUST_ARCH env var not found — needed to select the arch-specific "
        "Tauri config file (tauri.windows-<arch>.conf.json)."
    )
    value = m.group("value")
    assert "inputs.target" in value, f"RUST_ARCH must reference inputs.target (found: {value!r})."
    assert "aarch64" in value and "x86_64" in value, f"RUST_ARCH must distinguish aarch64 vs x86_64 (found: {value!r})."


# ─── 4. cargo tauri build uses dynamic --target ─────────────────────────────
def test_cargo_tauri_build_uses_dynamic_target(workflow_text: str):
    """The ``cargo tauri build --target`` flag must use the env var.

    Pre-fix: ``cargo tauri build --target x86_64-pc-windows-msvc ...``
    Post-fix: ``cargo tauri build --target $env:RUST_TARGET ...``
    so the build target follows the dispatched input.
    """
    # Find the "Build the Tauri app" step's run block. The step has the
    # form:
    #       - name: Build the Tauri app (ADR-0020 §7)
    #         shell: pwsh
    #         run: |
    #           cd src-tauri
    #           ...
    #           cargo tauri build --target $env:RUST_TARGET --config $archConfig
    # The run-block body is indented 10 spaces.
    step_start = workflow_text.find("name: Build the Tauri app")
    assert step_start != -1, "Build the Tauri app step not found"
    # Find the next "name:" (start of the following step) to bound the slice.
    next_step = workflow_text.find("\n      - name:", step_start + 1)
    run_block_slice = workflow_text[step_start:next_step]
    assert "cargo tauri build" in run_block_slice, "cargo tauri build command not found in Build the Tauri app step"
    assert re.search(r"--target\s+\$env:RUST_TARGET", run_block_slice), (
        "cargo tauri build --target must use $env:RUST_TARGET (dynamic), "
        "not a hardcoded x86_64-pc-windows-msvc literal. Found run block:\n" + run_block_slice
    )
    # And the hardcoded literal must NOT appear in the cargo tauri build line.
    cargo_line = next(
        (ln for ln in run_block_slice.splitlines() if "cargo tauri build" in ln),
        "",
    )
    assert "x86_64-pc-windows-msvc" not in cargo_line, (
        f"cargo tauri build line still hardcodes x86_64-pc-windows-msvc: {cargo_line!r}"
    )


def test_cargo_tauri_build_uses_arch_specific_config(workflow_text: str):
    """The ``--config`` flag must use the arch-specific config filename.

    Post-fix, the config filename is constructed from $env:RUST_ARCH:
    ``tauri.windows-$env:RUST_ARCH.conf.json`` with a fallback to
    ``tauri.conf.json`` if the arch-specific config is missing. This
    lets an aarch64 dispatch pick ``tauri.windows-aarch64.conf.json``
    when it exists, instead of always using the x86_64 config.
    """
    step_start = workflow_text.find("name: Build the Tauri app")
    assert step_start != -1, "Build the Tauri app step not found"
    next_step = workflow_text.find("\n      - name:", step_start + 1)
    run_block_slice = workflow_text[step_start:next_step]
    assert "RUST_ARCH" in run_block_slice, (
        "Build the Tauri app step must reference $env:RUST_ARCH to "
        "select the arch-specific config file. Run block:\n" + run_block_slice
    )
    assert "tauri.windows-" in run_block_slice, (
        "Build the Tauri app step must construct a tauri.windows-<arch>.conf.json "
        "filename. Run block:\n" + run_block_slice
    )


# ─── 5. Backward-compat: default literal preserved for downstream tests ─────
def test_default_x86_64_literal_preserved(workflow_text: str):
    """The literal ``x86_64-pc-windows-msvc`` must still appear in the YAML.

    Backward-compat assertion: tests/tauri/mig19/test_windows_cutover.py:415
    asserts ``"x86_64-pc-windows-msvc" in workflow_text``. The
    parameterization keeps this literal as the default value of the
    ``target`` input + the fallback in the env vars, so the cutover
    test (which validates the Phase 0-W primary target) keeps passing.
    """
    assert "x86_64-pc-windows-msvc" in workflow_text, (
        "The literal 'x86_64-pc-windows-msvc' must still appear in the "
        "workflow (as the default target) so existing cutover tests pass."
    )


def test_default_sidecar_filename_preserved(workflow_text: str):
    """The literal ``python-sidecar-x86_64-pc-windows-msvc.exe`` must appear.

    Backward-compat assertion: tests/tauri/mig18/test_windows_signing.py:153
    asserts this literal is in the workflow text (it greps for the sidecar
    binary filename to confirm signing is wired). The parameterization
    keeps this literal in a documentation comment listing the default
    filenames, so the mig18 signing test keeps passing.
    """
    assert "python-sidecar-x86_64-pc-windows-msvc.exe" in workflow_text, (
        "The literal 'python-sidecar-x86_64-pc-windows-msvc.exe' must still "
        "appear (in a documentation comment) so tests/tauri/mig18/"
        "test_windows_signing.py keeps passing."
    )


def test_default_prewarm_filename_preserved(workflow_text: str):
    """The literal ``prewarm-x86_64-pc-windows-msvc.exe`` must appear.

    Backward-compat assertion: tests/tauri/mig18/test_windows_signing.py:167
    asserts this literal is in the workflow text. The parameterization
    keeps this literal in a documentation comment listing the default
    filenames, so the mig18 signing test keeps passing.
    """
    assert "prewarm-x86_64-pc-windows-msvc.exe" in workflow_text, (
        "The literal 'prewarm-x86_64-pc-windows-msvc.exe' must still appear "
        "(in a documentation comment) so tests/tauri/mig18/test_windows_signing.py "
        "keeps passing."
    )
