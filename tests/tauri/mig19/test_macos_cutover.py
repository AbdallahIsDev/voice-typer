"""
macOS packaging contract (Tauri host only).
Gaps documented (report, do NOT fix, out of scope for this gate check):
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MACOS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-macos-build.yml"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    """Read the macOS CI workflow once per module; fail fast if missing."""
    assert MACOS_WORKFLOW.is_file(), (
        f"tauri-macos-build.yml not found at {MACOS_WORKFLOW}. "
        "This is the CI workflow that builds + signs + notarizes + "
        "staples the macOS Tauri bundle (ADR-0020 Phase 0-M + §13.2)."
    )
    return MACOS_WORKFLOW.read_text(encoding="utf-8")


def test_workflow_builds_both_arch_sidecars(workflow_text: str):
    """CI workflow must build BOTH aarch64 + x86_64 sidecars."""
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
    """CI workflow must build a universal .app + .dmg via cargo tauri build."""
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


def test_workflow_universal_job_needs_both_arch_jobs(workflow_text: str):
    """The universal-build job must ``needs:`` BOTH the aarch64 + x86_64 jobs."""
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
    """The macOS Tauri workflow must be ENABLED for Phase 0-M validation."""
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
    assert "Phase 0-M" in workflow_text, (
        "tauri-macos-build.yml does NOT reference 'Phase 0-M'. The "
        "workflow must document that Phase 0-M (macOS host validation "
        "runbook) must pass on BOTH archs before the release gate flips."
    )


def test_workflow_runs_macos_signing_notarization_stapling(
    workflow_text: str,
):
    """CI workflow must run signing + notarization + stapling for macOS."""
    # 1. Signing: codesign with --sign (the .dmg is explicitly signed;
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
