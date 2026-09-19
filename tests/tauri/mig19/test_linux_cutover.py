"""Linux packaging contract (Tauri host only)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
TAURI_LINUX_BUILD_YML = WORKFLOWS / "tauri-linux-build.yml"
TAURI_BUILD_YML = WORKFLOWS / "tauri-build.yml"
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"


@pytest.fixture(scope="module")
def linux_workflow_text() -> str:
    """Read ``tauri-linux-build.yml`` once per module."""
    assert TAURI_LINUX_BUILD_YML.is_file(), f"tauri-linux-build.yml missing: {TAURI_LINUX_BUILD_YML}"
    return TAURI_LINUX_BUILD_YML.read_text()


@pytest.fixture(scope="module")
def tauri_build_orchestrator_text() -> str:
    """Read ``tauri-build.yml`` (the cross-platform orchestrator) once per module."""
    assert TAURI_BUILD_YML.is_file(), f"tauri-build.yml missing: {TAURI_BUILD_YML}"
    return TAURI_BUILD_YML.read_text()


def _yaml_job_block(text: str, job: str) -> str | None:
    """Return the YAML lines of ``jobs.<job>`` up to the next top-level key."""
    lines = text.splitlines()
    header = f"  {job}:"
    for idx, line in enumerate(lines):
        if line.rstrip() == header:
            block: list[str] = []
            for sub in lines[idx + 1 :]:
                if len(sub) >= 3 and sub.startswith("  ") and not sub.startswith("   ") and sub[2].isalpha():
                    break
                block.append(sub)
            return "\n".join(block)
    return None


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load ``tauri.conf.json`` once per module."""
    assert TAURI_CONF.is_file(), f"tauri.conf.json missing: {TAURI_CONF}"
    return json.loads(TAURI_CONF.read_text())


def test_ci_workflow_runs_cargo_tauri_build(linux_workflow_text: str) -> None:
    """The Linux CI workflow must run ``cargo tauri build`` (produces all bundle formats)."""
    assert "cargo tauri build" in linux_workflow_text, (
        "tauri-linux-build.yml must run 'cargo tauri build' to produce bundle artifacts"
    )
    # The build must target the matrix arch's Rust triple.
    assert "--target" in linux_workflow_text, "tauri-linux-build.yml must pass --target <triple> to cargo tauri build"


def test_ci_workflow_uploads_deb_artifact(linux_workflow_text: str) -> None:
    """The Linux CI workflow must upload a .deb artifact."""
    assert "Upload .deb artifact" in linux_workflow_text, (
        "tauri-linux-build.yml must have an 'Upload .deb artifact' step"
    )
    assert "bundle/deb/*.deb" in linux_workflow_text, (
        "tauri-linux-build.yml .deb upload must reference bundle/deb/*.deb"
    )


def test_ci_workflow_uploads_appimage_artifact(linux_workflow_text: str) -> None:
    """The Linux CI workflow must upload a .AppImage artifact."""
    assert "Upload .AppImage artifact" in linux_workflow_text, (
        "tauri-linux-build.yml must have an 'Upload .AppImage artifact' step"
    )
    assert "bundle/appimage/*.AppImage" in linux_workflow_text, (
        "tauri-linux-build.yml AppImage upload must reference bundle/appimage/*.AppImage"
    )


def test_ci_workflow_builds_rpm_via_bundle_config(tauri_conf: dict) -> None:
    """The Linux CI workflow builds .rpm via ``tauri.conf.json`` ``bundle.linux.rpm``."""
    bundle = tauri_conf.get("bundle", {})
    assert "linux" in bundle, "tauri.conf.json missing 'bundle.linux'"
    assert "rpm" in bundle["linux"], (
        "tauri.conf.json bundle.linux.rpm must be configured so cargo tauri build "
        "produces .rpm (CI workflow relies on cargo tauri build to produce .rpm)"
    )
    rpm_cfg = bundle["linux"]["rpm"]
    assert isinstance(rpm_cfg, dict), "bundle.linux.rpm must be a dict"
    assert "postInstallScript" in rpm_cfg, (
        "bundle.linux.rpm.postInstallScript missing, Tauri v2 requires the 'postInstallScript' key"
    )
    assert "postInstall" not in rpm_cfg, (
        "stale short-form 'postInstall' key present on bundle.linux.rpm, should use Tauri v2 'postInstallScript'"
    )
    assert "preRemoveScript" in rpm_cfg, (
        "bundle.linux.rpm.preRemoveScript missing, Tauri v2 requires the 'preRemoveScript' key"
    )
    assert "preRemove" not in rpm_cfg, (
        "stale short-form 'preRemove' key present on bundle.linux.rpm, should use Tauri v2 'preRemoveScript'"
    )
    rpm_postinst = rpm_cfg["postInstallScript"]
    rpm_prerm = rpm_cfg["preRemoveScript"]
    assert rpm_postinst.endswith("postinst.rpm"), (
        f"bundle.linux.rpm.postInstallScript must reference postinst.rpm, got {rpm_postinst!r}"
    )
    assert rpm_prerm.endswith("prerm.rpm"), (
        f"bundle.linux.rpm.preRemoveScript must reference prerm.rpm, got {rpm_prerm!r}"
    )
    # `bundle.targets` must be "all" OR include "rpm".
    targets = bundle.get("targets")
    if isinstance(targets, str):
        assert targets == "all", f"bundle.targets must be 'all' (or a list including 'rpm'); got {targets!r}"
    elif isinstance(targets, list):
        assert "rpm" in targets or "all" in targets, f"bundle.targets list must include 'rpm' or 'all'; got {targets}"
    else:
        pytest.fail(f"bundle.targets must be a string or list; got {type(targets).__name__}")


def test_ci_workflow_documents_phase_0_l_gate(linux_workflow_text: str) -> None:
    """The Linux CI workflow must document the Phase 0-L gate + display-host requirement."""
    assert "Phase 0-L" in linux_workflow_text, (
        "tauri-linux-build.yml must reference Phase 0-L (the Linux Phase 0 spike gate)"
    )
    # The workflow header must mention that smoke tests need a real Linux
    assert "display" in linux_workflow_text.lower() or "X11" in linux_workflow_text, (
        "tauri-linux-build.yml must document that X11/Wayland display-host validation "
        "is required before enabling the workflow"
    )


def test_ci_workflow_documents_x11_and_wayland(linux_workflow_text: str) -> None:
    """The Linux CI workflow must document BOTH X11 AND Wayland (Phase 0-L gate)."""
    assert "X11" in linux_workflow_text, (
        "tauri-linux-build.yml must mention X11 (Phase 0-L gate requires X11 validation)"
    )
    assert "Wayland" in linux_workflow_text or "wayland" in linux_workflow_text, (
        "tauri-linux-build.yml must mention Wayland (Phase 0-L gate requires Wayland validation)"
    )


def test_ci_workflow_has_gate_status_block_with_9_runbook_checks(
    linux_workflow_text: str,
) -> None:
    """The Linux workflow header must carry a GATE STATUS block mirroring"""
    assert "GATE STATUS" in linux_workflow_text, (
        "tauri-linux-build.yml must carry a GATE STATUS header block mirroring tauri-macos-build.yml"
    )
    # The 10 markers = the "9-Point Validation Gate Summary" line + the 9
    assert linux_workflow_text.count("[ ]") >= 9, (
        "GATE STATUS block must list the 9 runbook checks each with a pass-state marker"
    )
    # Every one of the 9 runbook checks must appear by (case-insensitive)
    for keyword in (
        "externalBin",
        "handshake",
        "faster-whisper",
        "enigo",
        "libnotify",
        "shutdown",
        "prewarm",
        "linux-key-listener",
        "single-instance",
    ):
        assert keyword.lower() in linux_workflow_text.lower(), f"GATE STATUS block must track runbook check: {keyword}"


def test_ci_workflow_enabled_for_phase_0_l_validation(linux_workflow_text: str) -> None:
    """The Linux CI workflow's build job must be enabled for dispatch/orchestrator runs."""
    assert "if: false" not in linux_workflow_text, (
        "tauri-linux-build.yml still has an `if: false` job guard, the bundle "
        "build job is enabled for Phase 0-L validation (dispatch/orchestrator)."
    )
    assert (
        "if: github.event_name == 'workflow_dispatch' || github.event_name == 'workflow_call'" in linux_workflow_text
    ), (
        "tauri-linux-build.yml build job must be gated to "
        "`workflow_dispatch || workflow_call` (NOT plain `if: true`, push/PR "
        "triggers would fire the 30-60 min x2-arch bundle build on every push "
        "touching src-tauri/**)."
    )


def test_orchestrator_build_linux_triggers_linux_bundle_job(
    tauri_build_orchestrator_text: str, linux_workflow_text: str
) -> None:
    """Linux bundle job (workflow_call input wiring + gate compatibility)."""
    build_linux_block = _yaml_job_block(tauri_build_orchestrator_text, "build-linux")
    assert build_linux_block, "tauri-build.yml must define a build-linux job that calls the Linux workflow"
    block = build_linux_block
    assert "uses: ./.github/workflows/tauri-linux-build.yml" in block, (
        "build-linux must call tauri-linux-build.yml via workflow_call"
    )
    assert "target: ${{ github.event.inputs.target }}" in block, (
        "build-linux must pass the target input through to the Linux workflow"
    )
    assert "sign: ${{ fromJSON(github.event.inputs.sign) }}" in block, (
        "build-linux must pass the sign input through to the Linux workflow"
    )
    # Gate compatibility: the called workflow's bundle job fires on exactly
    assert (
        "if: github.event_name == 'workflow_dispatch' || github.event_name == 'workflow_call'" in linux_workflow_text
    ), (
        "Linux bundle job gate must accept the orchestrator's workflow_call "
        "event, otherwise build-linux dispatches silently produce nothing"
    )


def test_orchestrator_runs_pre_dispatch_drift_gate_before_fan_out(
    tauri_build_orchestrator_text: str,
) -> None:
    """pre-dispatch ``validate`` job before fanning out to all three platforms."""
    assert re.search(r"^  validate:", tauri_build_orchestrator_text, re.MULTILINE), (
        "tauri-build.yml must define a validate job (pre-dispatch drift gate)"
    )
    assert "Pre-dispatch config drift gate" in tauri_build_orchestrator_text, (
        "tauri-build.yml validate job must be the pre-dispatch drift gate"
    )
    assert "tests/tauri/test_bundle_identifier_parity.py" in tauri_build_orchestrator_text, (
        "validate job must run the identifier↔appId parity drift guard"
    )
    assert "tests/tauri/test_config_script_drift.py" in tauri_build_orchestrator_text, (
        "validate job must run the FULL config↔script drift file (all pair guards)"
    )
    assert "python3 scripts/gen_tauri_icons_stub.py --check-icons" in (tauri_build_orchestrator_text), (
        "validate job must run --check-icons on the committed bundle.icon set"
    )
    # Every platform call must depend on the gate (fan-out is blocked until
    for platform_job in ("build-windows", "build-macos", "build-linux"):
        platform_block = _yaml_job_block(tauri_build_orchestrator_text, platform_job)
        assert platform_block, f"tauri-build.yml must define a {platform_job} job"
        assert "needs: validate" in platform_block, (
            f"{platform_job} must needs: validate, the pre-dispatch drift gate must run once before the fan-out"
        )


def test_ci_workflow_matrix_includes_both_archs(linux_workflow_text: str) -> None:
    """The Linux CI workflow matrix must include BOTH x86_64 AND aarch64."""
    assert "x86_64" in linux_workflow_text, "tauri-linux-build.yml matrix must include x86_64"
    assert "aarch64" in linux_workflow_text, "tauri-linux-build.yml matrix must include aarch64"
    # The matrix.arch array must list both.
    matrix_match = re.search(
        r"matrix:\s*\n\s*arch:\s*\n((?:\s*-\s*\S+\s*\n?)+)",
        linux_workflow_text,
    )
    assert matrix_match, "could not extract matrix.arch block from tauri-linux-build.yml"
    arch_block = matrix_match.group(1)
    assert "x86_64" in arch_block, "matrix.arch must include x86_64"
    assert "aarch64" in arch_block, "matrix.arch must include aarch64"
    assert "fail-fast: false" in linux_workflow_text, (
        "tauri-linux-build.yml must set fail-fast: false so an aarch64 build failure "
        "doesn't hide an x86_64 success (per ADR-0020 §Reversibility)"
    )


def test_ci_workflow_documents_aarch64_cross_compile(linux_workflow_text: str) -> None:
    """The Linux CI workflow must document the aarch64 cross-compile path (qemu)."""
    assert "qemu" in linux_workflow_text.lower(), (
        "tauri-linux-build.yml must document the qemu-user-static aarch64 cross-compile path"
    )
    assert "binfmt" in linux_workflow_text.lower(), (
        "tauri-linux-build.yml must document the binfmt_misc registration for aarch64"
    )
