"""MIG-1.9 Phase 5 — macOS cutover validation (ADR-0020 Phase 5).

This test file is the **macOS cutover gate check** for MIG-1.9 Phase 5
of the ADR-0020 desktop-runtime migration (Electron → Tauri).
ADR-0020 §"Phase 5 — Validation & cutover" + docs/migration/cutover-playbook.md
mandate **per-platform cutover** (Windows first, macOS second, Linux
third). macOS cutover requires BOTH archs (aarch64-apple-darwin +
x86_64-apple-darwin) to have passed the Phase 0-M validation gate on a
real macOS host. macOS is the **2nd** platform in the cutover order —
it cannot cut over until Windows has been stable on Tauri for ≥ 1
release cycle, AND Phase 0-M passes on BOTH archs.

The Linux sandbox CANNOT run a real macOS build / codesign /
notarytool / stapler / spctl — those require a real macOS host + a
Developer ID Application certificate + an App Store Connect API key
(or Apple ID + app-specific password). These tests therefore validate
the **static configuration** of the macOS cutover plan:

  - ``docs/migration/cutover-playbook.md`` — the authoritative Phase 5
    cutover playbook that documents the macOS cutover steps + arch
    requirements + Phase 0-M gate + rollback procedure.
  - ``.github/workflows/tauri-macos-build.yml`` — the CI workflow that
    builds (aarch64 + x86_64 sidecars) → universal .app + .dmg via
    ``cargo tauri build --target universal-apple-darwin``, then signs
    + notarizes + staples the bundles.
  - ``voice_typer/client/electron-builder.yml`` — the Electron
    distribution config; the macOS ``mac:`` section with ``dmg`` target
    MUST stay present as the reversible fallback (ADR-0020 §"Reversibility"
    + cutover-playbook.md Step 2.2: "The Electron build PATH stays in
    the repo (reversible fallback) — only the active target is
    disabled.").

These tests check:
  1. The cutover playbook documents the macOS cutover (in the
     per-platform cutover order table) + lists BOTH archs (aarch64 +
     x86_64) + references the Phase 0-M gate.
  2. The CI workflow builds BOTH archs (aarch64-apple-darwin +
     x86_64-apple-darwin sidecars) + a universal .app/.dmg via
     ``cargo tauri build --target universal-apple-darwin``.
  3. The macOS Tauri CI workflow's universal-build job ``needs:``
     BOTH the aarch64 + x86_64 sidecar jobs (so the gate requires
     Phase 0-M to pass on BOTH archs — neither arch can be skipped).
  4. The macOS Tauri CI workflow is gated by ``if: false`` until
     Phase 0-M is manually validated (the per-platform Phase 5 gate).
  5. The Electron fallback is preserved: ``electron-builder.yml`` keeps
     the ``mac:`` section with ``dmg`` target + both ``x64`` + ``arm64``
     archs (the fallback that ships while macOS Tauri is in beta + the
     rollback path once macOS cuts over).
  6. The macOS cutover includes signing (codesign with
     ``MAC_SIGNING_IDENTITY``) + notarization (``xcrun notarytool
     submit --wait``) + stapling (``xcrun stapler staple``) +
     verification (``xcrun stapler validate``).
  7. The cutover is reversible: the playbook documents a rollback
     procedure (re-enable the electron-builder target + disable the
     Tauri workflow's ``if:`` guard) + states that the Electron build
     path stays in the repo + that no data/config/model loss occurs on
     rollback.

References:
  - ADR-0020 §"Phase 5 — Validation & cutover" + §"Reversibility" —
    the authoritative cutover spec.
  - docs/migration/cutover-playbook.md — the cutover playbook under test.
  - docs/migration/macos-validation-runbook.md — Phase 0-M runbook
    (the 9-point macOS host validation gate).
  - docs/migration/signing-guide.md — macOS Developer ID signing +
    notarization + stapling guide (ADR-0020 §13.2).
  - .github/workflows/tauri-macos-build.yml — the CI workflow under test.
  - voice_typer/client/electron-builder.yml — the Electron fallback
    config under test.

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1: the cutover playbook says macOS cutover produces "two DMGs"
    (aarch64 + x86_64 — see the macOS row in the "Per-platform cutover
    order" table), but the CI workflow produces a SINGLE universal DMG
    via ``cargo tauri build --target universal-apple-darwin``. Either
    the playbook or the workflow needs updating for consistency. The
    universal DMG is the better choice (smaller download, single
    artifact for users); the playbook should be updated to say
    "universal DMG" instead of "two DMGs". Report only — do NOT fix.
  - GAP-2: the CI workflow does NOT explicitly ``codesign --deep
    --entitlements src-tauri/entitlements.plist`` the .app bundle
    (it relies on cargo tauri build's internal signing via the
    ``MAC_SIGNING_IDENTITY`` env var). This is the same GAP-1 already
    documented in ``tests/tauri/mig18/test_macos_signing.py``. The .dmg
    IS explicitly signed with ``codesign --force --sign
    "$MAC_SIGNING_IDENTITY" "$DMG_PATH"`` (no --deep / --entitlements).
    Report only — do NOT fix.
  - GAP-3: the CI workflow's ``if: false`` guards are uniform across
    all 3 jobs (build-aarch64 / build-x86_64 / build-tauri-universal),
    so there is currently no way to enable ONLY aarch64 (e.g., if
    Phase 0-M passes on aarch64 but not yet on x86_64). Per the
    playbook, macOS cutover requires BOTH archs, so this is correct
    behavior — but it means a partial Phase 0-M pass cannot ship an
    aarch64-only Tauri beta. Report only — do NOT fix.

VALIDATE ON MACOS HOST (both archs):

  The macOS cutover is reversible ONLY when the 9-point Phase 0-M gate
  (see docs/migration/macos-validation-runbook.md) passes on BOTH archs.
  Sign-off (filed in the release notes per the playbook's "Evidence
  trail") must include name + date + target arch + OS version for EACH
  arch independently.

  ─── AARCH64 (Apple Silicon — macos-14 runner, native) ───────────────
  1. Trigger the workflow manually:
       GitHub Actions → "Tauri macOS Build (Phase 0-M)" → Run workflow
       → sign: true
     (Requires MAC_SIGNING_IDENTITY, APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD,
      APPLE_TEAM_ID secrets to be set in the repo.)
  2. After the build completes, download the "macos-universal-dmg"
     artifact.
  3. Mount the .dmg + drag "Voice Typer.app" to /Applications.
  4. Verify signing + notarization + stapling on aarch64:
       codesign -dv --verbose=4 "/Applications/Voice Typer.app"
         Expected: Authority=Developer ID Application: <name> (<TEAM_ID>)
       spctl --assess --verbose=4 "/Applications/Voice Typer.app"
         Expected: source=Notarized Developer ID
       xcrun stapler validate "/Applications/Voice Typer.app"
         Expected: "The validate action worked!"
  5. Launch the app + run the 9-point Phase 0-M gate (see
     docs/migration/macos-validation-runbook.md) on aarch64.
  6. Confirm the universal binary contains BOTH arch slices:
       lipo -archs "/Applications/Voice Typer.app/Contents/MacOS/Voice Typer"
         Expected: x86_64 arm64
  7. Confirm the embedded Nuitka sidecar is also universal (or both
     arch sidecars are present in the .app bundle):
       lipo -archs "/Applications/Voice Typer.app/Contents/Resources/python-sidecar"
         Expected: x86_64 arm64 (universal) OR verify both
         python-sidecar-aarch64-apple-darwin + python-sidecar-x86_64-apple-darwin
         are embedded.

  ─── X86_64 (Intel — macos-14 runner via Rosetta 2, OR macos-13 native) ──
  1. Same workflow as aarch64 — the CI workflow builds BOTH archs on a
     single macos-14 runner (aarch64 native + x86_64 via Rosetta 2).
     The universal .app + .dmg artifact contains both slices.
  2. On an Intel Mac, download + mount the same universal .dmg.
  3. Verify signing + notarization + stapling on x86_64 (same 3
     commands as aarch64 above — the bundle is universal, so the same
     signatures + notarization tickets cover both slices).
  4. Launch the app + run the 9-point Phase 0-M gate on x86_64.
  5. Confirm the x86_64 slice runs natively (not via Rosetta 2):
       file "/Applications/Voice Typer.app/Contents/MacOS/Voice Typer"
         Expected: Mach-O 64-bit executable x86_64
     Activity Monitor → Voice Typer → Kind: "Intel" (not "Apple" via
     Rosetta 2).

  ─── CUTOVER GATE (BOTH archs must pass) ─────────────────────────────
  The macOS cutover is reversible ONLY when the 9-point Phase 0-M gate
  passes on BOTH archs. Sign-off (filed in the release notes per the
  playbook's "Evidence trail") must include name + date + target arch +
  OS version for EACH arch independently. After BOTH archs pass + the
  evidence trail is filed, follow cutover-playbook.md Step 2 ("Flip the
  default (T-0 release)") for macOS:
    - .github/workflows/tauri-macos-build.yml: change `if: false` →
      `if: true` on all 3 jobs.
    - voice_typer/client/electron-builder.yml: comment out the `mac:`
      section's `target:` entries (the Electron build path stays in the
      repo as the reversible fallback).
    - Tag + push the release.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig19/test_macos_cutover.py.
# Path from file → root:
#   parents[0] = mig19/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CUTOVER_PLAYBOOK = PROJECT_ROOT / "docs" / "migration" / "cutover-playbook.md"
MACOS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-macos-build.yml"
ELECTRON_BUILDER = PROJECT_ROOT / "voice_typer" / "client" / "electron-builder.yml"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def playbook_text() -> str:
    """Read the cutover playbook once per module; fail fast if missing."""
    assert CUTOVER_PLAYBOOK.is_file(), (
        f"cutover-playbook.md not found at {CUTOVER_PLAYBOOK}. "
        "This is the authoritative Phase 5 cutover playbook (ADR-0020 "
        "§'Phase 5 — Validation & cutover')."
    )
    return CUTOVER_PLAYBOOK.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    """Read the macOS CI workflow once per module; fail fast if missing."""
    assert MACOS_WORKFLOW.is_file(), (
        f"tauri-macos-build.yml not found at {MACOS_WORKFLOW}. "
        "This is the CI workflow that builds + signs + notarizes + "
        "staples the macOS Tauri bundle (ADR-0020 Phase 0-M + §13.2)."
    )
    return MACOS_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def electron_builder_text() -> str:
    """Read electron-builder.yml once per module; fail fast if missing."""
    assert ELECTRON_BUILDER.is_file(), (
        f"electron-builder.yml not found at {ELECTRON_BUILDER}. "
        "The Electron fallback config must stay in the repo per "
        "ADR-0020 §'Reversibility' + cutover-playbook.md Step 2.2 "
        "(reversible fallback)."
    )
    return ELECTRON_BUILDER.read_text(encoding="utf-8")


# ─── 1. Playbook documents macOS cutover for BOTH archs ──────────────────────


def test_playbook_documents_macos_in_cutover_order(playbook_text: str):
    """Playbook must list macOS in the per-platform cutover order table.

    ADR-0020 §"Phase 5 — Validation & cutover" + cutover-playbook.md
    "Per-platform cutover order" table: Windows → macOS → Linux. macOS
    must be present (2nd in order, after Windows). Each platform is
    independent — Windows cutting over does NOT cut over macOS.
    """
    assert "Per-platform cutover order" in playbook_text, (
        "cutover-playbook.md is missing the 'Per-platform cutover order' "
        "section. ADR-0020 §'Phase 5' mandates the per-platform cutover "
        "order (Windows → macOS → Linux)."
    )
    # The macOS row must be present in the cutover order table.
    # Row format: "| 2nd | macOS | <reason> | <archs> | <gate> |"
    macos_row = re.search(
        r"^\|\s*\d+(?:st|nd|rd|th)?\s*\|\s*macOS\s*\|",
        playbook_text,
        re.MULTILINE | re.IGNORECASE,
    )
    assert macos_row is not None, (
        "cutover-playbook.md 'Per-platform cutover order' table does NOT "
        "include a macOS row. ADR-0020 §'Phase 5' mandates macOS as the "
        "2nd platform in the cutover order (after Windows)."
    )


def test_playbook_lists_both_archs_for_macos(playbook_text: str):
    """Playbook must list BOTH archs (aarch64 + x86_64) for macOS cutover.

    ADR-0020 §"Phase 5" + cutover-playbook.md macOS row: 'aarch64 +
    x86_64 (two DMGs)'. Both archs must be present because Apple Silicon
    + Intel Macs are both supported. macOS cutover requires Phase 0-M
    to pass on BOTH archs (a partial pass on one arch is NOT sufficient).
    """
    # Find the macOS row in the cutover order table.
    macos_row_match = re.search(
        r"^\|\s*\d+(?:st|nd|rd|th)?\s*\|\s*macOS\s*\|.*$",
        playbook_text,
        re.MULTILINE | re.IGNORECASE,
    )
    assert macos_row_match is not None, "Could not locate the macOS row in the 'Per-platform cutover order' table."
    macos_row = macos_row_match.group(0)
    # The row must mention both aarch64 (or arm64 / Apple Silicon) and
    # x86_64 (or x64 / Intel).
    has_aarch64 = "aarch64" in macos_row.lower() or "arm64" in macos_row.lower() or "Apple Silicon" in macos_row
    has_x86_64 = "x86_64" in macos_row.lower() or "x64" in macos_row.lower() or "Intel" in macos_row
    assert has_aarch64, (
        f"macOS row in cutover order table does NOT mention aarch64/arm64/"
        f"Apple Silicon. Row: {macos_row!r}. ADR-0020 §'Phase 5' mandates "
        f"both archs for macOS cutover (Apple Silicon + Intel)."
    )
    assert has_x86_64, (
        f"macOS row in cutover order table does NOT mention x86_64/x64/"
        f"Intel. Row: {macos_row!r}. ADR-0020 §'Phase 5' mandates both "
        f"archs for macOS cutover (Apple Silicon + Intel)."
    )


def test_playbook_references_phase_0_m_gate_for_macos(playbook_text: str):
    """Playbook must reference the Phase 0-M gate for macOS cutover.

    ADR-0020 §"Phase 5" + cutover-playbook.md: each platform's cutover
    requires the per-platform Phase 0 gate to pass. macOS's gate is
    'Phase 0-M' (9-point validation runbook in
    docs/migration/macos-validation-runbook.md).
    """
    # The macOS row's Phase 0 gate column must mention "Phase 0-M".
    macos_row_match = re.search(
        r"^\|\s*\d+(?:st|nd|rd|th)?\s*\|\s*macOS\s*\|.*$",
        playbook_text,
        re.MULTILINE | re.IGNORECASE,
    )
    assert macos_row_match is not None, "Could not locate the macOS row in the 'Per-platform cutover order' table."
    macos_row = macos_row_match.group(0)
    assert "Phase 0-M" in macos_row, (
        f"macOS row in cutover order table does NOT reference 'Phase 0-M' "
        f"as the gate. Row: {macos_row!r}. ADR-0020 §'Phase 5' mandates "
        f"that each platform's cutover requires its per-platform Phase 0 "
        f"gate to pass (macOS = Phase 0-M)."
    )


# ─── 2. CI workflow builds universal (or both arch) Tauri bundles ───────────


def test_workflow_builds_both_arch_sidecars(workflow_text: str):
    """CI workflow must build BOTH aarch64 + x86_64 sidecars.

    cutover-playbook.md macOS row: 'aarch64 + x86_64'. The CI workflow
    must build sidecars for both archs (even if the final .app/.dmg is
    universal — a universal binary requires both arch slices to be
    built first).
    """
    has_aarch64 = "aarch64-apple-darwin" in workflow_text
    has_x86_64 = "x86_64-apple-darwin" in workflow_text
    assert has_aarch64, (
        "tauri-macos-build.yml does NOT reference 'aarch64-apple-darwin'. "
        "The macOS cutover requires an aarch64 sidecar (Apple Silicon). "
        "Phase 0-M must pass on aarch64 before macOS can cut over."
    )
    assert has_x86_64, (
        "tauri-macos-build.yml does NOT reference 'x86_64-apple-darwin'. "
        "The macOS cutover requires an x86_64 sidecar (Intel). Phase 0-M "
        "must pass on x86_64 before macOS can cut over."
    )


def test_workflow_builds_universal_dmg(workflow_text: str):
    """CI workflow must build a universal .app + .dmg via cargo tauri build.

    ADR-0020 §"Phase 5" + cutover-playbook.md: macOS Tauri bundles ship
    as universal binaries (single .app + .dmg covering both archs). The
    CI workflow must invoke ``cargo tauri build --target
    universal-apple-darwin`` to produce the universal bundle.

    NOTE (GAP-1): the playbook says "two DMGs" but the workflow produces
    a SINGLE universal DMG. The universal DMG is the better choice
    (smaller download, single artifact); the playbook should be updated
    to say "universal DMG". See this file's module docstring GAP-1.
    """
    assert "universal-apple-darwin" in workflow_text, (
        "tauri-macos-build.yml does NOT reference 'universal-apple-darwin'. "
        "The macOS Tauri bundle must be built as a universal binary "
        "(single .app + .dmg covering both aarch64 + x86_64)."
    )
    assert "cargo tauri build" in workflow_text, (
        "tauri-macos-build.yml does NOT invoke 'cargo tauri build'. "
        "This is the command that produces the .app + .dmg bundle from "
        "the sidecar + prewarm + native-listener binaries."
    )


# ─── 3. macOS cutover gate requires Phase 0-M on BOTH archs ─────────────────


def test_workflow_universal_job_needs_both_arch_jobs(workflow_text: str):
    """The universal-build job must ``needs:`` BOTH the aarch64 + x86_64 jobs.

    ADR-0020 §"Phase 5" + cutover-playbook.md: macOS cutover requires
    Phase 0-M to pass on BOTH archs. The CI workflow enforces this by
    making the universal-build job depend on BOTH arch sidecar jobs
    (so neither arch can be skipped — if either fails, the universal
    build does not run).
    """
    # The build-tauri-universal job must have `needs: [build-aarch64, build-x86_64]`.
    needs_pattern = re.search(
        r"build-tauri-universal\s*:[^\n]*\n(?:[^\n]*\n)*?\s*needs:\s*\[([^\]]+)\]",
        workflow_text,
    )
    assert needs_pattern is not None, (
        "tauri-macos-build.yml does NOT have a 'build-tauri-universal' "
        "job with a 'needs:' dependency. ADR-0020 §'Phase 5' mandates "
        "that the universal build requires BOTH arch sidecar builds to "
        "succeed (Phase 0-M on BOTH archs)."
    )
    needs_value = needs_pattern.group(1)
    assert "build-aarch64" in needs_value, (
        f"build-tauri-universal job's 'needs:' does NOT include "
        f"'build-aarch64'. needs: [{needs_value}]. The universal build "
        f"must depend on the aarch64 sidecar build (Apple Silicon) — "
        f"Phase 0-M must pass on aarch64."
    )
    assert "build-x86_64" in needs_value, (
        f"build-tauri-universal job's 'needs:' does NOT include "
        f"'build-x86_64'. needs: [{needs_value}]. The universal build "
        f"must depend on the x86_64 sidecar build (Intel) — Phase 0-M "
        f"must pass on x86_64."
    )


def test_workflow_has_phase_0_m_gate(workflow_text: str):
    """The macOS Tauri workflow must be gated by ``if: false`` (Phase 0-M).

    ADR-0020 §"Phase 5" + cutover-playbook.md Step 2.1: a platform's
    Tauri workflow is gated (``if: false``) until its Phase 0 validation
    passes on a real host. The macOS workflow must have ``if: false``
    guards (the Phase 0-M gate) that are flipped to ``if: true`` ONLY
    when Phase 0-M passes on BOTH archs.

    Per cutover-playbook.md Step 2.1: "Enable the per-platform Tauri
    workflow's top-level ``if:`` guard. Change ``if: false`` → ``if: true``
    (or remove the guard)."
    """
    # The workflow must have at least one `if: false` guard on a job.
    if_false_count = workflow_text.count("if: false")
    assert if_false_count >= 1, (
        "tauri-macos-build.yml does NOT have any 'if: false' guards. "
        "ADR-0020 §'Phase 5' + cutover-playbook.md Step 2.1 mandate "
        "that the per-platform Tauri workflow is gated until its Phase 0 "
        "validation passes (macOS = Phase 0-M on BOTH archs)."
    )
    # The workflow must mention Phase 0-M (so the gate's purpose is
    # documented in the workflow itself, not just in the playbook).
    assert "Phase 0-M" in workflow_text, (
        "tauri-macos-build.yml does NOT reference 'Phase 0-M'. The "
        "workflow's `if: false` gate exists to block CI until Phase 0-M "
        "passes on a real macOS host — the workflow must document this "
        "so future maintainers know what the gate is for."
    )


# ─── 4. Electron fallback preserved (macOS dmg target) ──────────────────────


def test_electron_builder_preserves_macos_dmg_target(
    electron_builder_text: str,
):
    """electron-builder.yml must keep the macOS dmg target (reversible fallback).

    ADR-0020 §"Reversibility" + cutover-playbook.md Step 2.2: "The
    Electron build PATH stays in the repo (reversible fallback) — only
    the active target is disabled." The macOS ``mac:`` section with
    ``dmg`` target must stay present even after macOS cuts over to Tauri
    (so rollback is a one-line uncomment, not a git revert).
    """
    # The mac: section must exist (top-level key at start of line).
    assert re.search(r"^mac\s*:", electron_builder_text, re.MULTILINE), (
        "electron-builder.yml is missing the 'mac:' section. ADR-0020 "
        "§'Reversibility' + cutover-playbook.md Step 2.2 mandate that "
        "the Electron fallback stays in the repo (the mac: section is "
        "commented out on cutover, NOT deleted)."
    )
    # Isolate the mac: section (from `mac:` to the next top-level key).
    mac_section_match = re.search(
        r"^mac\s*:.*?(?=^[a-zA-Z_-]+\s*:|\Z)",
        electron_builder_text,
        re.MULTILINE | re.DOTALL,
    )
    assert mac_section_match is not None, "Could not isolate the 'mac:' section in electron-builder.yml."
    mac_section = mac_section_match.group(0)
    assert "dmg" in mac_section, (
        "electron-builder.yml 'mac:' section does NOT include 'dmg' as a "
        "target. ADR-0020 §'Reversibility' + cutover-playbook.md Step 2.2 "
        "mandate that the macOS dmg target stays present as the "
        "reversible fallback (commented out on cutover, NOT deleted)."
    )


def test_electron_builder_macos_lists_both_archs(
    electron_builder_text: str,
):
    """electron-builder.yml macOS dmg target must list BOTH archs (x64 + arm64).

    cutover-playbook.md macOS row: 'aarch64 + x86_64'. The Electron
    fallback must cover both archs (so rolling back from Tauri to
    Electron doesn't drop support for one arch). electron-builder uses
    'x64' (Intel) + 'arm64' (Apple Silicon) arch names.
    """
    mac_section_match = re.search(
        r"^mac\s*:.*?(?=^[a-zA-Z_-]+\s*:|\Z)",
        electron_builder_text,
        re.MULTILINE | re.DOTALL,
    )
    assert mac_section_match is not None, "Could not isolate the 'mac:' section in electron-builder.yml."
    mac_section = mac_section_match.group(0)
    assert "x64" in mac_section, (
        "electron-builder.yml 'mac:' section does NOT list 'x64' arch. "
        "The Electron fallback must cover Intel Macs (x64) so rollback "
        "from Tauri doesn't drop Intel support."
    )
    assert "arm64" in mac_section, (
        "electron-builder.yml 'mac:' section does NOT list 'arm64' arch. "
        "The Electron fallback must cover Apple Silicon Macs (arm64) so "
        "rollback from Tauri doesn't drop Apple Silicon support."
    )


# ─── 5. macOS cutover includes signing + notarization + stapling ────────────


def test_playbook_documents_macos_signing_notarization_stapling(
    playbook_text: str,
):
    """Playbook must document macOS signing + notarization + stapling.

    ADR-0020 §"Phase 5" + cutover-playbook.md 'Cutover criteria' hard
    criterion #8: "macOS: .app + .dmg Developer ID-signed, notarized,
    stapled; spctl --assess --verbose passes; xcrun stapler validate
    passes." The playbook must mention all 3 steps for macOS.
    """
    # Look for the macOS signing/notarization/stapling line in the
    # 'Signing + notarization' hard-criterion section (#8).
    macos_signing_line_match = re.search(
        r"macOS:[^\n]*\.app[^\n]*\.dmg[^\n]*",
        playbook_text,
        re.IGNORECASE,
    )
    assert macos_signing_line_match is not None, (
        "cutover-playbook.md does NOT have a macOS line in the 'Signing + "
        "notarization' hard-criterion section (#8). ADR-0020 §'Phase 5' "
        "mandates that macOS cutover requires signing + notarization + "
        "stapling of both the .app + .dmg."
    )
    macos_signing_line = macos_signing_line_match.group(0).lower()
    assert "sign" in macos_signing_line, (
        f"macOS signing line does NOT mention 'sign'. Line: "
        f"{macos_signing_line!r}. ADR-0020 §13.2 + §'Phase 5' mandate "
        f"Developer ID signing for the .app + .dmg."
    )
    assert "notariz" in macos_signing_line, (
        f"macOS signing line does NOT mention 'notariz' (notarization). "
        f"Line: {macos_signing_line!r}. ADR-0020 §13.2 + §'Phase 5' "
        f"mandate notarization via xcrun notarytool."
    )
    assert "stapl" in macos_signing_line, (
        f"macOS signing line does NOT mention 'stapl' (stapling). Line: "
        f"{macos_signing_line!r}. ADR-0020 §13.2 + §'Phase 5' mandate "
        f"stapling via xcrun stapler so Gatekeeper can verify offline."
    )


def test_workflow_runs_macos_signing_notarization_stapling(
    workflow_text: str,
):
    """CI workflow must run signing + notarization + stapling for macOS.

    ADR-0020 §"Phase 5" + §13.2 + cutover-playbook.md hard criterion #8:
    the macOS Tauri bundle must be (1) signed with Developer ID
    Application via ``codesign``, (2) notarized via
    ``xcrun notarytool submit --wait``, (3) stapled via
    ``xcrun stapler staple``, + (4) verified via
    ``xcrun stapler validate``.

    NOTE (GAP-2): the .app bundle is NOT explicitly signed with
    ``codesign --deep --entitlements`` — the workflow relies on cargo
    tauri build's internal signing via MAC_SIGNING_IDENTITY. The .dmg
    IS explicitly signed with ``codesign --force --sign``. See this
    file's module docstring GAP-2 + tests/tauri/mig18/test_macos_signing.py
    GAP-1.
    """
    # 1. Signing: codesign with --sign (the .dmg is explicitly signed;
    # the .app relies on cargo tauri build's internal signing — ).
    assert "codesign" in workflow_text, (
        "tauri-macos-build.yml does NOT invoke 'codesign'. ADR-0020 §13.2 "
        "+ §'Phase 5' mandate that the macOS bundle is signed with a "
        "Developer ID Application certificate."
    )
    assert "--sign" in workflow_text, (
        "tauri-macos-build.yml invokes 'codesign' but NOT with '--sign'. "
        "The signing identity must be passed via "
        "'--sign \"$MAC_SIGNING_IDENTITY\"'."
    )
    # 2. Notarization: xcrun notarytool submit --wait.
    assert "xcrun notarytool submit" in workflow_text, (
        "tauri-macos-build.yml does NOT run 'xcrun notarytool submit'. "
        "ADR-0020 §13.2 + §'Phase 5' mandate notarization via notarytool."
    )
    assert "--wait" in workflow_text, (
        "tauri-macos-build.yml runs 'xcrun notarytool submit' but NOT "
        "with '--wait'. Without --wait, stapling will fail because the "
        "notarization ticket isn't ready yet (Apple's service is async)."
    )
    # 3. Stapling: xcrun stapler staple.
    assert "xcrun stapler staple" in workflow_text, (
        "tauri-macos-build.yml does NOT run 'xcrun stapler staple'. "
        "ADR-0020 §13.2 + §'Phase 5' mandate stapling after notarization "
        "(so Gatekeeper can verify the bundle offline)."
    )
    # 4. Verification: xcrun stapler validate.
    assert "xcrun stapler validate" in workflow_text, (
        "tauri-macos-build.yml does NOT run 'xcrun stapler validate'. "
        "ADR-0020 §13.2 + §'Phase 5' mandate verification after stapling "
        "(to confirm the staple succeeded)."
    )


# ─── 6. Cutover is reversible ────────────────────────────────────────────────


def test_playbook_documents_rollback_procedure(playbook_text: str):
    """Playbook must document a per-platform rollback procedure.

    ADR-0020 §"Reversibility" + cutover-playbook.md 'Rollback procedure':
    cutover is reversible per-platform. The playbook must have a
    'Rollback procedure' section + describe the steps (re-enable the
    electron-builder target, disable the Tauri workflow's ``if:`` guard,
    tag a hotfix release).
    """
    assert "Rollback procedure" in playbook_text, (
        "cutover-playbook.md is missing the 'Rollback procedure' section. "
        "ADR-0020 §'Reversibility' mandates that the cutover is "
        "reversible per-platform — the rollback procedure must be "
        "documented."
    )
    # The rollback procedure must mention re-enabling the electron-builder
    # target + disabling the Tauri workflow's if: guard.
    playbook_lower = playbook_text.lower()
    assert "electron-builder" in playbook_lower, (
        "cutover-playbook.md 'Rollback procedure' does NOT mention "
        "'electron-builder'. The rollback procedure must describe "
        "re-enabling the electron-builder target for the rolled-back "
        "platform (per Step 2.2 + the rollback section)."
    )
    assert "if: false" in playbook_text or "if: true" in playbook_text, (
        "cutover-playbook.md 'Rollback procedure' does NOT mention the "
        "Tauri workflow's 'if: false' / 'if: true' guard. The rollback "
        "procedure must describe disabling the per-platform Tauri "
        "workflow's if: guard (change if: true → if: false)."
    )


def test_playbook_states_electron_fallback_preserved(playbook_text: str):
    """Playbook must state that the Electron build path stays in the repo.

    ADR-0020 §"Reversibility" + cutover-playbook.md Step 2.2: "The
    Electron build PATH stays in the repo (reversible fallback) — only
    the active target is disabled." This is the key reversibility
    guarantee: the Electron code is NOT deleted on cutover — only the
    active target is commented out, so rollback is a one-line uncomment.
    """
    assert "reversible fallback" in playbook_text.lower(), (
        "cutover-playbook.md does NOT mention 'reversible fallback'. "
        "ADR-0020 §'Reversibility' + cutover-playbook.md Step 2.2 mandate "
        "that the Electron build path stays in the repo as the reversible "
        "fallback (only the active target is disabled, the code is NOT "
        "deleted)."
    )


def test_playbook_states_no_data_loss_on_rollback(playbook_text: str):
    """Playbook must state that rollback causes no data/config/model loss.

    ADR-0020 §"Reversibility" + cutover-playbook.md 'What does NOT
    change on rollback': "No data, config, or model loss." This is the
    key user-facing reversibility guarantee: rollback doesn't wipe user
    history / vocabulary / templates / automation / models. The Tauri
    build writes to the same OS-specific data dir as the Electron build.
    """
    # Look for the "What does NOT change on rollback" section.
    assert "does NOT change on rollback" in playbook_text or "What does NOT change" in playbook_text, (
        "cutover-playbook.md is missing the 'What does NOT change on "
        "rollback' section. ADR-0020 §'Reversibility' mandates that "
        "rollback causes no data/config/model loss — this guarantee "
        "must be documented."
    )
    # Isolate the section + check it mentions data + config + model.
    no_change_section_match = re.search(
        r"What does NOT change on rollback.*?(?=^## |\Z)",
        playbook_text,
        re.DOTALL,
    )
    assert no_change_section_match is not None, "Could not isolate the 'What does NOT change on rollback' section."
    no_change_section = no_change_section_match.group(0).lower()
    assert "data" in no_change_section or "history" in no_change_section, (
        "cutover-playbook.md 'What does NOT change on rollback' section "
        "does NOT mention data/history. ADR-0020 §'Reversibility' "
        "mandates that rollback preserves user data."
    )
    assert "config" in no_change_section or "settings" in no_change_section, (
        "cutover-playbook.md 'What does NOT change on rollback' section "
        "does NOT mention config/settings. ADR-0020 §'Reversibility' "
        "mandates that rollback preserves user config."
    )
    assert "model" in no_change_section, (
        "cutover-playbook.md 'What does NOT change on rollback' section "
        "does NOT mention models. ADR-0020 §'Reversibility' mandates "
        "that rollback preserves user-downloaded models."
    )
