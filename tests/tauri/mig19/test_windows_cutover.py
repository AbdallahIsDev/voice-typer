"""MIG-1.9 Phase 5 Check — Windows cutover plan validation.

This test file is the **Windows cutover validation** for ADR-0020 Phase 5
(see ``docs/adr/0020-desktop-runtime-migration-analysis.md`` §"Phase 5 —
Validation & cutover (per platform)" + §"Reversibility"). It validates the
**structure** of the Windows cutover plan as documented in
``docs/migration/cutover-playbook.md`` and as wired in:

  - ``.github/workflows/tauri-windows-build.yml`` — the Tauri host build
    (Phase 0-W / Phase 1-W packaging; stubbed with ``if: false`` until the
    Phase 0-W host gate passes on a real Windows host).
  - ``.github/workflows/build.yml`` — the existing Electron build
    pipeline (the ``build-windows`` job runs
    ``npx electron-builder --win`` to produce the NSIS installer).
  - ``voice_typer/client/electron-builder.yml`` — the Electron fallback
    config; the ``win:`` section's ``target: [nsis]`` is the entry that
    gets commented out on Windows cutover (Step 2.2 of the playbook).

The Linux sandbox CANNOT run a real Windows host cutover (no
``cargo tauri build`` for Windows, no ``npx electron-builder --win``,
no NSIS installer signing, no real Phase 0-W host gate). These tests
therefore validate that the **plan + wiring** is in place:

  1. The cutover playbook documents the Windows cutover steps (per-OS
     flip procedure, comment-out electron-builder target, update release
     notes, etc.).
  2. The Electron fallback config (``electron-builder.yml``) exists and
     is valid YAML with a ``win:`` section (so the fallback remains
     buildable).
  3. The CI can build BOTH the Tauri host (``tauri-windows-build.yml``)
     AND the Electron fallback (``build.yml::build-windows``) — they
     coexist in the repo, neither deletes the other.
  4. The cutover is reversible — the Electron build path is NOT deleted,
     just deprioritized (commented out in electron-builder.yml on flip;
     re-enabled on rollback).
  5. The Windows cutover gate requires Phase 0-W to pass first — the
     ``tauri-windows-build.yml`` workflow is stubbed with ``if: false``
     until Phase 0-W host validation (see
     ``docs/migration/windows-validation-runbook.md``) passes on a real
     Windows host.
  6. The Windows cutover includes a rollback plan — the playbook has a
     "Rollback procedure" section that documents the reverse flip
     (re-enable electron-builder target, disable the Tauri workflow's
     ``if:`` guard, tag a hotfix release).

VALIDATE ON WINDOWS HOST:
    1. Confirm Phase 0-W gate passed (see MIG-1.5)
    2. Build the Tauri installer: cd src-tauri; cargo tauri build --target x86_64-pc-windows-msvc
    3. Build the Electron fallback: cd voice_typer/client; npm run dist:win
    4. Install the Tauri installer (NSIS) → verify Voice Typer launches
    5. Verify the sidecar runs (Task Manager → python-sidecar-*.exe)
    6. If Tauri fails: uninstall Tauri, install Electron fallback → verify it works
    Expected: Tauri is the default; Electron fallback is the rollback

References:
  - ADR-0020 §"Phase 5 — Validation & cutover (per platform)" +
    §"Reversibility" + §"Migration Plan" (Phase 0-W gate) — authoritative
    migration spec.
  - docs/migration/cutover-playbook.md — the per-platform cutover
    procedure (this test validates its Windows-specific content).
  - docs/migration/windows-validation-runbook.md — the Phase 0-W 9-point
    host gate (must pass on a real Windows 10 22H2 / Windows 11 host
    before the cutover gate flips).
  - .github/workflows/tauri-windows-build.yml — the Tauri host CI build
    (stubbed with ``if: false`` until Phase 0-W passes).
  - .github/workflows/build.yml::build-windows — the existing Electron
    fallback CI build (``npx electron-builder --win``).
  - voice_typer/client/electron-builder.yml — the Electron fallback
    config (``win:`` section with ``nsis`` target is the flip lever).

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1: The cutover playbook's Step 2.2 instructs the release engineer
    to "comment out the ``win:`` section's ``target: [nsis]``" in
    ``electron-builder.yml``. The current ``electron-builder.yml`` STILL
    has the ``win:`` target active (it has not been cut over yet — that
    is expected; Windows has not been cut over). This is the pre-cutover
    state, NOT a gap; flagging only to clarify the test asserts the
    *pre-cutover* state (active target) + that the playbook documents
    the comment-out step (the lever).
    See ``test_electron_fallback_win_target_is_currently_active_pre_cutover``
    + ``test_playbook_documents_electron_target_disable_step``.
  - GAP-2: ``tauri-windows-build.yml`` is stubbed with ``if: false`` —
    this is INTENTIONAL (Phase 0-W has not passed on a real Windows
    host). The cutover cannot proceed until (a) Phase 0-W passes on a
    real host AND (b) the ``if: false`` is flipped to ``if: true`` per
    playbook Step 2.1. This test asserts the pre-cutover state (stubbed
    guard present) so that flipping the guard is a deliberate, tested
    step.
    See ``test_tauri_windows_workflow_has_phase0w_if_false_guard``.
  - GAP-3: The cutover playbook's "Cutover log" section is empty — no
    platform has been cut over yet. This is the expected pre-cutover
    state. ``test_playbook_cutover_log_section_exists`` only asserts the
    section exists (so future cutovers are logged there).
"""

from __future__ import annotations

import re
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
CUTOVER_PLAYBOOK = PROJECT_ROOT / "docs" / "migration" / "cutover-playbook.md"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
TAURI_WINDOWS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-windows-build.yml"
TAURI_BUILD_ORCHESTRATOR = PROJECT_ROOT / ".github" / "workflows" / "tauri-build.yml"
BUILD_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "build.yml"
ELECTRON_BUILDER_YML = PROJECT_ROOT / "voice_typer" / "client" / "electron-builder.yml"
WINDOWS_VALIDATION_RUNBOOK = PROJECT_ROOT / "docs" / "migration" / "windows-validation-runbook.md"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def playbook_text() -> str:
    """Read the cutover playbook once per module; fail fast if missing."""
    assert CUTOVER_PLAYBOOK.is_file(), (
        f"cutover-playbook.md not found at {CUTOVER_PLAYBOOK}. Did the project layout change?"
    )
    return CUTOVER_PLAYBOOK.read_text(encoding="utf-8")


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


@pytest.fixture(scope="module")
def build_workflow_text() -> str:
    """Read the top-level CI build workflow once per module."""
    assert BUILD_WORKFLOW.is_file(), f"build.yml not found at {BUILD_WORKFLOW}."
    return BUILD_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def electron_builder_text() -> str:
    """Read electron-builder.yml once per module; fail fast if missing.

    This file is the Electron fallback config — its presence + validity
    is the foundation of the "reversible fallback" guarantee. The Windows
    cutover does NOT delete this file; it comments out the ``win:`` target.
    """
    assert ELECTRON_BUILDER_YML.is_file(), (
        f"electron-builder.yml not found at {ELECTRON_BUILDER_YML}. "
        "The Electron fallback must remain in the repo per ADR-0020 "
        "§'Reversibility' + the cutover playbook's §'Rollback procedure'."
    )
    return ELECTRON_BUILDER_YML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def adr_0020_text() -> str:
    """Read ADR-0020 once per module."""
    assert ADR_0020.is_file(), f"ADR-0020 not found at {ADR_0020}."
    return ADR_0020.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def windows_runbook_text() -> str:
    """Read the Windows validation runbook once per module."""
    assert WINDOWS_VALIDATION_RUNBOOK.is_file(), (
        f"windows-validation-runbook.md not found at {WINDOWS_VALIDATION_RUNBOOK}."
    )
    return WINDOWS_VALIDATION_RUNBOOK.read_text(encoding="utf-8")


# ─── 1. Cutover playbook documents the Windows cutover steps ─────────────────


def test_playbook_has_windows_cutover_section(playbook_text: str):
    """The cutover playbook must document the Windows cutover as the
    first platform to flip.

    ADR-0020 §"Per-platform cutover order" mandates Windows first
    (largest user base, smallest Tauri unknowns — WebView2 = Chromium,
    no notarization, no Wayland). The playbook's
    §"Per-platform cutover order" table must list Windows as 1st.
    """
    assert "Windows" in playbook_text, "cutover-playbook.md must mention Windows (the 1st cutover platform)."
    # The per-platform cutover order table lists Windows as 1st.
    # Anchor on the actual `## Per-platform cutover order` header (the
    # scope-of-document bullet at the top of the file ALSO says "Per-
    # platform cutover order", so we anchor on `^## ` to skip it).
    order_section_match = re.search(
        r"^## Per-platform cutover order.*?(?=^## |\Z)",
        playbook_text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert order_section_match is not None, (
        "cutover-playbook.md must have a '## Per-platform cutover order' section header."
    )
    order_section = order_section_match.group(0)
    assert "1st" in order_section, "cutover-playbook.md per-platform order table must use '1st' label."
    # Windows is listed as 1st.
    line = next(
        (ln for ln in order_section.splitlines() if "1st" in ln),
        "",
    )
    assert "Windows" in line, f"cutover-playbook.md must list Windows as the 1st cutover platform (line: {line!r})."


def test_playbook_documents_electron_target_disable_step(playbook_text: str):
    """The playbook must document Step 2.2 — disable the electron-builder
    target for the platform being cut over.

    Per the playbook §"Step 2 — Flip the default (T-0 release)" item 2,
    the release engineer comments out the platform's ``target:`` entries
    in ``electron-builder.yml`` (on Windows, the ``win:`` section's
    ``target: [nsis]``). This is the cutover lever — it must be
    documented.
    """
    assert "electron-builder.yml" in playbook_text, (
        "cutover-playbook.md must reference electron-builder.yml (the file "
        "whose win: target is commented out on cutover)."
    )
    # The playbook instructs commenting out the win: target.
    assert "comment out" in playbook_text.lower() or "disable" in playbook_text.lower(), (
        "cutover-playbook.md must instruct the release engineer to "
        "comment out / disable the electron-builder target on cutover."
    )
    # Windows-specific: the playbook must mention the `win:` section.
    assert "win:" in playbook_text or "win " in playbook_text.lower(), (
        "cutover-playbook.md must reference the win: section of electron-builder.yml for the Windows cutover."
    )


def test_playbook_documents_tauri_workflow_if_guard_flip(playbook_text: str):
    """The playbook must document Step 2.1 — enable the per-platform
    Tauri workflow's ``if:`` guard (``if: false`` → ``if: true``).

    Per the playbook §"Step 2 — Flip the default" item 1, the release
    engineer flips ``if: false`` → ``if: true`` (or removes the guard)
    on ``.github/workflows/tauri-<platform>-build.yml``. On Windows,
    this is ``tauri-windows-build.yml``.
    """
    assert "tauri-" in playbook_text, (
        "cutover-playbook.md must reference the per-platform Tauri "
        "workflow file (.github/workflows/tauri-<platform>-build.yml)."
    )
    assert "if: false" in playbook_text, (
        "cutover-playbook.md must mention the `if: false` guard that "
        "gates the per-platform Tauri workflow until the cutover."
    )
    assert "if: true" in playbook_text, "cutover-playbook.md must mention flipping the guard to `if: true` on cutover."


def test_playbook_documents_release_notes_update_step(playbook_text: str):
    """The playbook must document Step 2.3 — update the release notes
    to announce the per-platform cutover + link the rollback path.
    """
    assert "release notes" in playbook_text.lower(), "cutover-playbook.md must document the release notes update step."
    # The release notes must mention the rollback path (the prior
    # Electron installer is still downloadable from the same release).
    assert "rollback" in playbook_text.lower(), (
        "cutover-playbook.md must reference the rollback path in the release notes update step."
    )


def test_playbook_documents_evidence_trail(playbook_text: str):
    """The playbook must document the evidence trail that must be filed
    before the flip (runbook checklist, FT-1 log, side-by-side smoke
    test, bundle size + startup latency, signing verification, user
    acceptance sign-off, rollback plan confirmed).
    """
    assert "Evidence trail" in playbook_text, (
        "cutover-playbook.md must have an 'Evidence trail' section listing what must be filed before the cutover flip."
    )
    # Required evidence items.
    for required in (
        "user acceptance",
        "signing",
        "FT-1",
        "smoke",
        "rollback",
    ):
        assert required.lower() in playbook_text.lower(), (
            f"cutover-playbook.md evidence trail must mention '{required}'."
        )


# ─── 2. Electron fallback is preserved (electron-builder.yml exists + valid) ─


def test_electron_builder_yml_exists(electron_builder_text: str):
    """electron-builder.yml must exist (Electron fallback preserved).

    ADR-0020 §"Reversibility" + the cutover playbook's §"Rollback
    procedure" both depend on this file remaining in the repo. Deleting
    it would break the reversibility guarantee.
    """
    # The fixture already asserts the file exists; this test makes the
    # intent explicit in the test report.
    assert electron_builder_text, "electron-builder.yml must be non-empty (Electron fallback config)."


def test_electron_builder_yml_has_win_section(electron_builder_text: str):
    """electron-builder.yml must have a ``win:`` section.

    The ``win:`` section is the Electron fallback config for Windows.
    The Windows cutover (Step 2.2 of the playbook) comments out the
    ``target:`` entries inside this section — it does NOT delete the
    section. The section must exist (pre-cutover state) so the cutover
    lever is reachable.
    """
    # The `win:` section must appear as a top-level key (start of line).
    assert re.search(r"^win:", electron_builder_text, flags=re.MULTILINE), (
        "electron-builder.yml must have a top-level `win:` section "
        "(the Electron fallback config for Windows — the cutover lever)."
    )


def test_electron_builder_yml_win_target_is_nsis(electron_builder_text: str):
    """electron-builder.yml ``win:`` section must target ``nsis``.

    The Windows Electron installer is NSIS (matching
    ``.github/workflows/build.yml::build-windows`` which runs
    ``npx electron-builder --win``). The cutover comments out this
    target (Step 2.2 of the playbook).
    """
    # Find the `win:` section and confirm it has `target: nsis` or
    # `target:\n    - nsis`.
    win_section_match = re.search(
        r"^win:\s*\n((?:[ \t]+.*\n)+)",
        electron_builder_text,
        flags=re.MULTILINE,
    )
    assert win_section_match is not None, "electron-builder.yml `win:` section must have indented content."
    win_section = win_section_match.group(1)
    assert "nsis" in win_section, (
        "electron-builder.yml `win:` section must target `nsis` (the Electron fallback installer format on Windows)."
    )


def test_electron_builder_yml_win_target_is_currently_active_pre_cutover(
    electron_builder_text: str,
):
    """Pre-cutover state: the ``win:`` target is NOT commented out.

    This is GAP-1 in the module docstring. Windows has not been cut over
    yet (Phase 0-W has not passed on a real Windows host), so the
    ``win:`` target in electron-builder.yml must still be active
    (NOT prefixed with ``#``).

    When the cutover happens, the release engineer will comment out the
    ``target:`` entries per Step 2.2 of the playbook — at that point
    this test should be updated to assert the commented-out state.
    """
    # Find the `win:` section.
    win_section_match = re.search(
        r"^win:\s*\n((?:[ \t]+.*\n)+)",
        electron_builder_text,
        flags=re.MULTILINE,
    )
    assert win_section_match is not None, "electron-builder.yml `win:` section must exist."
    win_section = win_section_match.group(1)
    # The `target:` line inside the `win:` section must NOT be a comment.
    target_lines = [ln for ln in win_section.splitlines() if "target" in ln.lower()]
    assert target_lines, "electron-builder.yml `win:` section must have a `target:` entry."
    for ln in target_lines:
        # A commented-out line starts with `#` (after optional whitespace).
        stripped = ln.lstrip()
        assert not stripped.startswith("#"), (
            "electron-builder.yml `win:` target must NOT be commented out "
            "in the pre-cutover state. (If you are running this test AFTER "
            "the Windows cutover, update this test to assert the "
            "commented-out state per cutover-playbook.md Step 2.2.)"
        )


# ─── 3. CI workflow can build BOTH the Tauri host AND the Electron fallback ──


def test_tauri_windows_workflow_exists(tauri_windows_workflow_text: str):
    """The Tauri Windows build workflow must exist (Tauri host build).

    ``.github/workflows/tauri-windows-build.yml`` is the CI workflow
    that builds the Tauri host (NSIS + MSI installers) for Windows.
    Without it, the Tauri host cannot be built in CI for the cutover.
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

    Per ADR-0020 §"Phase 0-W (Windows)" + the cutover playbook's
    per-platform table, the Windows cutover is x86_64 first (aarch64
    follows). The Tauri build must produce an installer for that
    target triple.
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
    call ``tauri-windows-build.yml`` so that flipping the Windows
    workflow's ``if:`` guard actually causes the Windows installer to
    be built on tag dispatch.
    """
    assert "tauri-windows-build.yml" in tauri_build_orchestrator_text, (
        "tauri-build.yml orchestrator must call tauri-windows-build.yml (the per-platform Windows workflow)."
    )
    assert "build-windows" in tauri_build_orchestrator_text, (
        "tauri-build.yml orchestrator must have a `build-windows` job "
        "that fans out to the per-platform Windows workflow."
    )


def test_electron_windows_build_job_exists_in_ci(build_workflow_text: str):
    """The top-level CI workflow must still build the Electron Windows
    installer (the fallback).

    ``.github/workflows/build.yml::build-windows`` runs
    ``npx electron-builder --win`` to produce the Electron NSIS
    installer. This job MUST remain in the repo (it is the reversible
    fallback) — the cutover playbook does NOT instruct deleting it; it
    instructs commenting out the ``win:`` target in
    ``electron-builder.yml``.
    """
    assert "build-windows" in build_workflow_text, (
        "build.yml must still have a `build-windows` job (the Electron "
        "fallback build path — preserved per ADR-0020 §'Reversibility')."
    )
    assert "electron-builder --win" in build_workflow_text, (
        "build.yml::build-windows must invoke "
        "`npx electron-builder --win` to produce the Electron NSIS "
        "installer (the fallback)."
    )


def test_ci_can_build_both_tauri_and_electron_for_windows(
    tauri_windows_workflow_text: str,
    build_workflow_text: str,
):
    """The repo's CI MUST be able to build BOTH the Tauri host AND the
    Electron fallback for Windows (they coexist).

    ADR-0020 §"Reversibility" + the cutover playbook's
    §"Mixed-mode period" require that both builds can ship in the same
    release (Tauri as default + Electron as the alternative/legacy
    fallback). This means BOTH workflows must exist in ``.github/workflows/``
    — neither deletes the other.
    """
    # Tauri host build path exists.
    assert "cargo tauri build" in tauri_windows_workflow_text, (
        "Tauri host build path (tauri-windows-build.yml) must invoke `cargo tauri build`."
    )
    # Electron fallback build path exists.
    assert "electron-builder --win" in build_workflow_text, (
        "Electron fallback build path (build.yml::build-windows) must invoke `npx electron-builder --win`."
    )


# ─── 4. Cutover is reversible (Electron build is NOT deleted, just deprioritized) ──


def test_playbook_states_electron_path_stays_in_repo(playbook_text: str):
    """The playbook must explicitly state that the Electron build PATH
    stays in the repo (reversible fallback).

    Per the playbook §"Step 2 — Flip the default" item 2: "The Electron
    build PATH stays in the repo (reversible fallback) — only the active
    target is disabled." This is the reversibility guarantee.
    """
    assert "reversible fallback" in playbook_text.lower(), (
        "cutover-playbook.md must explicitly state that the Electron "
        "build path stays in the repo as a 'reversible fallback'."
    )


def test_playbook_rollback_does_not_delete_electron(playbook_text: str):
    """The rollback procedure must NOT delete Electron code — it
    re-enables the electron-builder target + disables the Tauri
    workflow's ``if:`` guard.

    Per the playbook §"What does NOT change on rollback": no data,
    config, or model loss; the Tauri build writes to the same data dir;
    the Python sidecar is the same binary in both paths.
    """
    # Find the "What does NOT change on rollback" subsection.
    no_change_match = re.search(
        r"What does NOT change on rollback.*?(?=^### |^## |\Z)",
        playbook_text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert no_change_match is not None, "cutover-playbook.md must have a 'What does NOT change on rollback' subsection."
    no_change = no_change_match.group(0)
    assert "data" in no_change.lower() or "config" in no_change.lower(), (
        "cutover-playbook.md rollback section must mention that no data or config is lost on rollback."
    )


def test_electron_builder_yml_not_scheduled_for_deletion(
    electron_builder_text: str,
    playbook_text: str,
):
    """The Electron fallback config (electron-builder.yml) must NOT be
    scheduled for deletion — neither the playbook nor the config file
    itself indicates removal.

    ADR-0020 §"Reversibility" mandates the Electron code path stays
    intact on every platform throughout the migration. The playbook's
    §"Step 3 — Post-flip monitoring" says "the Electron fallback can
    be marked 'legacy' in the release notes (but NOT deleted from the
    repo)".
    """
    # The playbook must say Electron is NOT deleted.
    assert "NOT deleted" in playbook_text or "not deleted" in playbook_text.lower(), (
        "cutover-playbook.md must explicitly state the Electron fallback "
        "is NOT deleted from the repo (marked 'legacy' at most)."
    )
    # electron-builder.yml is non-empty (the fixture asserted existence;
    # this is the deprioritized-not-deleted guarantee).
    assert electron_builder_text.strip(), (
        "electron-builder.yml must be non-empty (Electron fallback config preserved in the repo, not deleted)."
    )


# ─── 5. Windows cutover gate requires Phase 0-W to pass first ────────────────


def test_tauri_windows_workflow_has_phase0w_if_false_guard(
    tauri_windows_workflow_text: str,
):
    """The Tauri Windows workflow MUST be stubbed with ``if: false``
    until Phase 0-W host validation passes.

    GAP-2 in the module docstring. ``tauri-windows-build.yml`` is
    intentionally disabled (``if: false`` on the job) so it does NOT
    run on push or PR until the Phase 0-W host validation gate (see
    ``docs/migration/windows-validation-runbook.md``) has passed on a
    real Windows 10 22H2 / Windows 11 host. Flipping ``if: false`` →
    ``if: true`` is the cutover lever (playbook Step 2.1) — it must be
    a deliberate, tested step.

    This test asserts the PRE-cutover state. After the Windows cutover,
    this test should be updated to assert ``if: true``.
    """
    # The workflow file's header comment explains the stub.
    assert "if: false" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must have `if: false` on the job "
        "(Phase 0-W host gate has not passed yet — this is the "
        "pre-cutover state)."
    )
    # The header comment must reference Phase 0-W.
    assert "Phase 0-W" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must reference Phase 0-W in its header "
        "comment (the gate that must pass before flipping `if: false`)."
    )


def test_tauri_windows_workflow_references_validation_runbook(
    tauri_windows_workflow_text: str,
):
    """The Tauri Windows workflow must reference the Phase 0-W host
    validation runbook.

    The workflow header comment must point to
    ``docs/migration/windows-validation-runbook.md`` so the release
    engineer knows what host validation to run BEFORE flipping the
    ``if: false`` guard.
    """
    assert "windows-validation-runbook" in tauri_windows_workflow_text, (
        "tauri-windows-build.yml must reference "
        "docs/migration/windows-validation-runbook.md (the Phase 0-W "
        "host validation gate procedure)."
    )


def test_playbook_requires_phase_0_w_for_windows_cutover(playbook_text: str):
    """The cutover playbook's hard criteria must require Phase 0-W
    to pass on a real Windows host before the Windows cutover.

    Per the playbook §"Cutover criteria per platform" item 1: "Phase 0
    spike passes on a real host for that platform (see the per-platform
    runbook for the 9-point gate)." The Phase 0-W gate (9 points) is
    documented in ``windows-validation-runbook.md``.
    """
    assert "Phase 0" in playbook_text, "cutover-playbook.md must reference Phase 0 (the per-platform spike gate)."
    # The hard criteria section must mention "real host".
    assert "real host" in playbook_text.lower(), (
        "cutover-playbook.md cutover criteria must require Phase 0 to pass on a 'real host' (not just CI)."
    )
    # The per-platform order table must list Phase 0-W as the Windows gate.
    assert "Phase 0-W" in playbook_text, (
        "cutover-playbook.md must reference Phase 0-W (the Windows-specific "
        "spike gate) in the per-platform cutover order table."
    )


def test_playbook_phase_0_w_runbook_referenced(
    playbook_text: str,
    windows_runbook_text: str,
):
    """The cutover playbook must reference the Phase 0-W per-platform
    runbook, and that runbook must exist + document the 9-point gate.
    """
    # Playbook mentions per-platform runbooks.
    assert "runbook" in playbook_text.lower(), (
        "cutover-playbook.md must reference the per-platform runbooks (windows-validation-runbook.md, etc.)."
    )
    # The Windows runbook must document the 9-point gate.
    assert "9-point" in windows_runbook_text or "9 point" in windows_runbook_text, (
        "windows-validation-runbook.md must document the 9-point Phase 0-W host validation gate."
    )


# ─── 6. Windows cutover includes a rollback plan ─────────────────────────────


def test_playbook_has_rollback_procedure_section(playbook_text: str):
    """The cutover playbook must have a 'Rollback procedure' section.

    Per ADR-0020 §"Reversibility" + the playbook's own scope
    statement, the rollback procedure is part of the cutover plan.
    Without it, a failed cutover cannot be reverted.
    """
    assert "Rollback procedure" in playbook_text, "cutover-playbook.md must have a 'Rollback procedure' section."


def test_playbook_rollback_is_per_platform(playbook_text: str):
    """The rollback procedure must be per-platform (rolling back Windows
    does NOT roll back macOS or Linux).

    Per the playbook §"Rollback procedure": "Rolling back Windows does
    NOT roll back macOS or Linux." This is the per-platform
    reversibility guarantee from ADR-0020 §"Reversibility".
    """
    # Anchor on the actual `## Rollback procedure` header (the
    # scope-of-document bullet at the top of the file ALSO says
    # "Rollback procedure (how to revert per-platform)", so we anchor
    # on `^## ` to skip it).
    rollback_match = re.search(
        r"^## Rollback procedure.*?(?=^## |\Z)",
        playbook_text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert rollback_match is not None, "cutover-playbook.md must have a '## Rollback procedure' section header."
    rollback_section = rollback_match.group(0)
    assert "per-platform" in rollback_section.lower(), (
        "cutover-playbook.md rollback procedure must be explicitly per-platform."
    )
    assert "Windows" in rollback_section, (
        "cutover-playbook.md rollback procedure must mention Windows (a per-platform rollback example)."
    )


def test_playbook_rollback_steps_documented(playbook_text: str):
    """The rollback procedure must document the reverse flip steps:
    (1) re-enable the electron-builder target, (2) disable the Tauri
    workflow's ``if:`` guard, (3) tag a hotfix release.
    """
    rollback_match = re.search(
        r"^## Rollback procedure.*?(?=^## |\Z)",
        playbook_text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert rollback_match is not None, "cutover-playbook.md must have a '## Rollback procedure' section header."
    rollback_section = rollback_match.group(0)
    # Step 1: re-enable electron-builder target.
    assert "electron-builder" in rollback_section, (
        "cutover-playbook.md rollback must reference electron-builder.yml (re-enable the platform's target)."
    )
    # Step 2: disable the Tauri workflow's `if:` guard.
    assert "if: false" in rollback_section, (
        "cutover-playbook.md rollback must restore the `if: false` guard on the per-platform Tauri workflow."
    )
    # Step 3: tag a hotfix release.
    assert "hotfix" in rollback_section.lower(), "cutover-playbook.md rollback must instruct tagging a hotfix release."


def test_playbook_cutover_log_section_exists(playbook_text: str):
    """The cutover playbook must have a 'Cutover log' section so future
    cutovers (and rollbacks) are tracked.

    GAP-3 in the module docstring. The section is currently empty (no
    platform has been cut over yet) — that is the expected pre-cutover
    state. This test only asserts the section EXISTS so future cutovers
    have a place to be logged.
    """
    assert "Cutover log" in playbook_text, (
        "cutover-playbook.md must have a 'Cutover log' section where "
        "each cutover + rollback is recorded (currently empty — "
        "expected pre-cutover state)."
    )


# ─── 7. ADR-0020 Phase 5 + Reversibility cross-check ─────────────────────────


def test_adr_0020_phase_5_documents_per_platform_cutover(
    adr_0020_text: str,
):
    """ADR-0020 §"Phase 5 — Validation & cutover (per platform)" must
    document the per-platform cutover + cutover order (Windows first).
    """
    assert "Phase 5" in adr_0020_text, "ADR-0020 must have a Phase 5 section."
    assert "cutover" in adr_0020_text.lower(), "ADR-0020 Phase 5 must discuss the cutover."
    assert "Windows first" in adr_0020_text, "ADR-0020 must state 'Windows first' as the cutover order."


def test_adr_0020_reversibility_section_exists(adr_0020_text: str):
    """ADR-0020 must have a 'Reversibility' section that mandates the
    Electron code path stays intact (per-platform).
    """
    assert "Reversibility" in adr_0020_text, "ADR-0020 must have a 'Reversibility' section."
    reversibility_match = re.search(
        r"### Reversibility.*?(?=^### |^## |\Z)",
        adr_0020_text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert reversibility_match is not None, "ADR-0020 must have a '### Reversibility' subsection."
    reversibility = reversibility_match.group(0)
    # Electron code must stay intact (not removed).
    assert "Electron code is untouched" in reversibility, (
        "ADR-0020 Reversibility must state 'Electron code is untouched' "
        "(the per-platform reversible fallback guarantee)."
    )
    # Reverting one platform must NOT revert the others.
    assert "does not revert the others" in reversibility.lower(), (
        "ADR-0020 Reversibility must state that reverting one platform "
        "does NOT revert the others (per-platform independence)."
    )
