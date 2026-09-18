"""MIG-1.9 Phase 5: macOS packaging contract (Tauri host only).

The project ships exactly ONE desktop shell: the Tauri Rust host. This
module pins the live macOS packaging contract:

  - ``.github/workflows/tauri-macos-build.yml`` builds the aarch64 +
    x86_64 sidecars, assembles a universal ``.app`` + ``.dmg`` via
    ``cargo tauri build --target universal-apple-darwin``, then signs +
    notarizes + staples the bundles.
  - The universal-build job ``needs:`` BOTH the aarch64 + x86_64
    sidecar jobs, so neither arch can be skipped.
  - Every job is ENABLED (no ``if: false`` guards left); the workflow
    runs via ``workflow_dispatch`` / the tauri-build.yml orchestrator's
    ``workflow_call``, with push/PR triggers staying commented out until
    the Phase 0-M host validation passes (ADR-0020 §15).

The Linux sandbox CANNOT run a real macOS build / codesign / notarytool
/ stapler / spctl, those require a real macOS host + a Developer ID
Application certificate + an App Store Connect API key (or Apple ID +
app-specific password). These tests therefore validate the **static
configuration** of the macOS packaging path.

Gaps documented (report, do NOT fix, out of scope for this gate check):
  - GAP-1: the CI workflow produces a SINGLE universal DMG via
    ``cargo tauri build --target universal-apple-darwin``.
  - GAP-2: the CI workflow does NOT explicitly ``codesign --deep
    --entitlements src-tauri/entitlements.plist`` the .app bundle
    (it relies on cargo tauri build's internal signing via the
    ``MAC_SIGNING_IDENTITY`` env var). This is the same GAP-1 already
    documented in ``tests/tauri/mig18/test_macos_signing.py``. The .dmg
    IS explicitly signed with ``codesign --force --sign
    "$MAC_SIGNING_IDENTITY" "$DMG_PATH"`` (no --deep / --entitlements).
    Report only, do NOT fix.
  - GAP-3 (CLOSED, workflow enabled): the CI workflow's 3 jobs
    (build-aarch64 / build-x86_64 / build-tauri-universal) are uniform:
    all ENABLED (``if: true``) for Phase 0-M validation, so there is
    currently no way to enable ONLY aarch64 (e.g., if Phase 0-M passes on
    aarch64 but not yet on x86_64). Report only, do NOT fix.

VALIDATE ON MACOS HOST (both archs):

  ─── AARCH64 (Apple Silicon, macos-14 runner, native) ───────────────
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

  ─── X86_64 (Intel, macos-14 runner via Rosetta 2, OR macos-13 native) ──
  1. Same workflow as aarch64, the CI workflow builds BOTH archs on a
     single macos-14 runner (aarch64 native + x86_64 via Rosetta 2).
     The universal .app + .dmg artifact contains both slices.
  2. On an Intel Mac, download + mount the same universal .dmg.
  3. Verify signing + notarization + stapling on x86_64 (same 3
     commands as aarch64 above, the bundle is universal, so the same
     signatures + notarization tickets cover both slices).
  4. Launch the app + run the 9-point Phase 0-M gate on x86_64.
  5. Confirm the x86_64 slice runs natively (not via Rosetta 2):
       file "/Applications/Voice Typer.app/Contents/MacOS/Voice Typer"
         Expected: Mach-O 64-bit executable x86_64
     Activity Monitor → Voice Typer → Kind: "Intel" (not "Apple" via
     Rosetta 2).

  ─── RELEASE GATE (BOTH archs must pass) ─────────────────────────────
  The macOS release gate requires the 9-point Phase 0-M gate to pass on
  BOTH archs. Sign-off (filed in the release notes) must include name +
  date + target arch + OS version for EACH arch independently. After
  BOTH archs pass, uncomment the push/PR triggers (ADR-0020 §15) so
  CI-driven runs confirm the runbook pass on real runners.

References:
  - ADR-0020 §"Phase 5, Validation & cutover" + §"Reversibility" —
    the authoritative cutover spec.
  - docs/migration/macos-validation-runbook.md, Phase 0-M runbook
    (the 9-point macOS host validation gate).
  - docs/migration/signing-guide.md, macOS Developer ID signing +
    notarization + stapling guide (ADR-0020 §13.2).
  - .github/workflows/tauri-macos-build.yml, the CI workflow under test.
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
MACOS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-macos-build.yml"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def workflow_text() -> str:
    """Read the macOS CI workflow once per module; fail fast if missing."""
    assert MACOS_WORKFLOW.is_file(), (
        f"tauri-macos-build.yml not found at {MACOS_WORKFLOW}. "
        "This is the CI workflow that builds + signs + notarizes + "
        "staples the macOS Tauri bundle (ADR-0020 Phase 0-M + §13.2)."
    )
    return MACOS_WORKFLOW.read_text(encoding="utf-8")


# ─── 1. CI workflow builds universal (or both arch) Tauri bundles ───────────


def test_workflow_builds_both_arch_sidecars(workflow_text: str):
    """CI workflow must build BOTH aarch64 + x86_64 sidecars.

    The macOS release covers Apple Silicon + Intel, and the CI workflow
    must build sidecars for both archs (even though the final .app/.dmg
    is universal, a universal binary requires both arch slices to be
    built first).
    """
    has_aarch64 = "aarch64-apple-darwin" in workflow_text
    has_x86_64 = "x86_64-apple-darwin" in workflow_text
    assert has_aarch64, (
        "tauri-macos-build.yml does NOT reference 'aarch64-apple-darwin'. "
        "The macOS release requires an aarch64 sidecar (Apple Silicon). "
        "Phase 0-M must pass on aarch64 before the release gate flips."
    )
    assert has_x86_64, (
        "tauri-macos-build.yml does NOT reference 'x86_64-apple-darwin'. "
        "The macOS release requires an x86_64 sidecar (Intel). Phase 0-M "
        "must pass on x86_64 before the release gate flips."
    )


def test_workflow_builds_universal_dmg(workflow_text: str):
    """CI workflow must build a universal .app + .dmg via cargo tauri build.

    ADR-0020 §"Phase 5": macOS Tauri bundles ship as universal binaries
    (single .app + .dmg covering both archs). The CI workflow must
    invoke ``cargo tauri build --target universal-apple-darwin`` to
    produce the universal bundle.
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


# ─── 2. macOS release gate requires Phase 0-M on BOTH archs ─────────────────


def test_workflow_universal_job_needs_both_arch_jobs(workflow_text: str):
    """The universal-build job must ``needs:`` BOTH the aarch64 + x86_64 jobs.

    ADR-0020 §"Phase 5": the macOS release gate requires Phase 0-M to
    pass on BOTH archs. The CI workflow enforces this by making the
    universal-build job depend on BOTH arch sidecar jobs (so neither
    arch can be skipped, if either fails, the universal build does not
    run).
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
        f"must depend on the aarch64 sidecar build (Apple Silicon), "
        f"Phase 0-M must pass on aarch64."
    )
    assert "build-x86_64" in needs_value, (
        f"build-tauri-universal job's 'needs:' does NOT include "
        f"'build-x86_64'. needs: [{needs_value}]. The universal build "
        f"must depend on the x86_64 sidecar build (Intel), Phase 0-M "
        f"must pass on x86_64."
    )


def test_workflow_is_enabled_for_phase_0_m_validation(workflow_text: str):
    """The macOS Tauri workflow must be ENABLED for Phase 0-M validation.

    A platform's Tauri workflow is gated until its Phase 0 validation is
    ready to run on a real host, then enabled so the validation run can
    execute via ``workflow_dispatch``. The macOS workflow must have NO
    ``if: false`` job guards left, and must still document the Phase 0-M
    validation requirement so future maintainers know what must pass
    before the release gate flips.
    """
    # No job may remain disabled.
    assert "if: false" not in workflow_text, (
        "tauri-macos-build.yml still has `if: false` job guards, the "
        "macOS workflow must be enabled for Phase 0-M validation."
    )
    # All three jobs must be present and explicitly enabled.
    for job in ("build-aarch64:", "build-x86_64:", "build-tauri-universal:"):
        assert job in workflow_text, f"tauri-macos-build.yml missing job {job!r}"
    assert "if: true" in workflow_text, (
        "tauri-macos-build.yml jobs must be enabled with `if: true` so the Phase 0-M validation run can execute."
    )
    # The Phase 0-M validation requirement must still be documented in the
    # workflow itself (not just in the runbook), so the release gate stays
    # discoverable.
    assert "Phase 0-M" in workflow_text, (
        "tauri-macos-build.yml does NOT reference 'Phase 0-M'. The "
        "workflow must document that Phase 0-M (macOS host validation "
        "runbook) must pass on BOTH archs before the release gate flips."
    )


# ─── 3. macOS signing + notarization + stapling ─────────────────────────────


def test_workflow_runs_macos_signing_notarization_stapling(
    workflow_text: str,
):
    """CI workflow must run signing + notarization + stapling for macOS.

    ADR-0020 §"Phase 5" + §13.2: the macOS Tauri bundle must be
    (1) signed with Developer ID Application via ``codesign``,
    (2) notarized via ``xcrun notarytool submit --wait``,
    (3) stapled via ``xcrun stapler staple``, + (4) verified via
    ``xcrun stapler validate``.

    NOTE (GAP-2): the .app bundle is NOT explicitly signed with
    ``codesign --deep --entitlements``, the workflow relies on cargo
    tauri build's internal signing via MAC_SIGNING_IDENTITY. The .dmg
    IS explicitly signed with ``codesign --force --sign``. See this
    file's module docstring GAP-2 + tests/tauri/mig18/test_macos_signing.py
    GAP-1.
    """
    # 1. Signing: codesign with --sign (the .dmg is explicitly signed;
    # the .app relies on cargo tauri build's internal signing).
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
