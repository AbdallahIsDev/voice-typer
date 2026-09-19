"""
Phase 1 Check 5: Windows Authenticode signing validation.
Gaps documented (report, do NOT fix, out of scope for this gate check):
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "tauri-windows-build.yml"
SIDECAR_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_windows.sh"
PREWARM_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_prewarm_windows.sh"
TAURI_CONF_FILE = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
SIGNING_GUIDE = PROJECT_ROOT / "docs" / "migration" / "signing-guide.md"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"

# Canonical timestamp server (RFC-3161) per signing-guide.md.
EXPECTED_TIMESTAMP_SERVER = "http://timestamp.digicert.com"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    """Read the CI workflow once per module; fail fast if missing."""
    assert WORKFLOW_FILE.is_file(), (
        f"tauri-windows-build.yml not found at {WORKFLOW_FILE}. Did the project layout change?"
    )
    return WORKFLOW_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sidecar_script_text() -> str:
    """Read the sidecar build script once per module."""
    assert SIDECAR_BUILD_SCRIPT.is_file(), f"build_sidecar_windows.sh not found at {SIDECAR_BUILD_SCRIPT}."
    return SIDECAR_BUILD_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prewarm_script_text() -> str:
    """Read the prewarm build script once per module."""
    assert PREWARM_BUILD_SCRIPT.is_file(), f"build_prewarm_windows.sh not found at {PREWARM_BUILD_SCRIPT}."
    return PREWARM_BUILD_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tauri_conf_json() -> dict:
    """Parse tauri.conf.json once per module."""
    assert TAURI_CONF_FILE.is_file(), f"tauri.conf.json not found at {TAURI_CONF_FILE}."
    return json.loads(TAURI_CONF_FILE.read_text(encoding="utf-8"))


def test_workflow_runs_signtool_sign_on_sidecar(workflow_text: str):
    """The CI workflow must run ``signtool sign`` on the sidecar exe."""
    assert "signtool sign" in workflow_text, (
        "tauri-windows-build.yml must invoke `signtool sign` to "
        "Authenticode-sign the sidecar + prewarm + NSIS (ADR-0020 §13.1)."
    )
    assert "python-sidecar-x86_64-pc-windows-msvc.exe" in workflow_text, (
        "tauri-windows-build.yml must sign python-sidecar-x86_64-pc-windows-msvc.exe "
        "(the Nuitka-produced sidecar binary)."
    )


def test_workflow_does_not_sign_prewarm(workflow_text: str):
    """The CI workflow must NOT reference the prewarm exe."""
    assert "prewarm-x86_64-pc-windows-msvc.exe" not in workflow_text, (
        "tauri-windows-build.yml must NOT reference prewarm-*.exe, the "
        "standalone prewarm binary was removed per plan-runtime-pack-split "
        "§6.2 P-1."
    )


def test_workflow_runs_signtool_sign_on_nsis(workflow_text: str):
    """The CI workflow must run ``signtool sign`` on the NSIS installer."""
    # The NSIS signing step in the workflow signs ``${{ steps.artifacts.outputs.nsis_path }}``.
    assert "nsis_path" in workflow_text, (
        "tauri-windows-build.yml must produce an `nsis_path` artifact output "
        "(the NSIS installer path) and sign it with signtool."
    )
    # The actual signing step has the signtool sign command followed by
    assert "signtool sign" in workflow_text
    # The signing step's `if:` guard references the NSIS path interpolation
    nsis_signing_marker = "steps.artifacts.outputs.nsis_path"
    assert nsis_signing_marker in workflow_text, (
        "tauri-windows-build.yml must reference steps.artifacts.outputs.nsis_path "
        "in the signtool sign command (NSIS installer signing)."
    )


def test_workflow_runs_signtool_verify(workflow_text: str):
    """The CI workflow must run ``signtool verify`` after signing."""
    assert "signtool verify" in workflow_text, (
        "tauri-windows-build.yml must run `signtool verify /pa /v` after "
        "signing to confirm the signature is valid (signing-guide.md §Verify)."
    )
    # /pa = use Default Authentication Verification Policy;
    assert "/pa" in workflow_text, (
        "tauri-windows-build.yml must use `signtool verify /pa /v` (/pa = Default Authentication Verification Policy)."
    )


def test_workflow_uses_win_csc_link_secret(workflow_text: str):
    """The workflow must consume the ``WIN_CSC_LINK`` secret."""
    assert "WIN_CSC_LINK" in workflow_text, (
        "tauri-windows-build.yml must consume the WIN_CSC_LINK secret "
        "(Authenticode PFX cert, reused from the predecessor build per "
        "signing-guide.md §'Reused signing identities')."
    )
    assert "secrets.WIN_CSC_LINK" in workflow_text, (
        "tauri-windows-build.yml must reference secrets.WIN_CSC_LINK "
        "(the GitHub Actions secret holding the base64-encoded PFX)."
    )


def test_workflow_uses_win_csc_key_password_secret(workflow_text: str):
    """The workflow must consume the ``WIN_CSC_KEY_PASSWORD`` secret."""
    assert "WIN_CSC_KEY_PASSWORD" in workflow_text, (
        "tauri-windows-build.yml must consume the WIN_CSC_KEY_PASSWORD "
        "secret (the PFX password, paired with WIN_CSC_LINK)."
    )
    assert "secrets.WIN_CSC_KEY_PASSWORD" in workflow_text


def test_workflow_signing_step_is_gated_on_csc_env_vars(workflow_text: str):
    """The signing step must be conditional on the CSC env vars being set."""
    # The signing step's if-condition references WIN_CSC_LINK + WIN_CSC_KEY_PASSWORD.
    assert "env.WIN_CSC_LINK" in workflow_text, (
        "tauri-windows-build.yml signing step must be gated on "
        "`env.WIN_CSC_LINK != ''` (skip signing on PR builds without secrets)."
    )
    assert "env.WIN_CSC_KEY_PASSWORD" in workflow_text


def test_workflow_uses_rfc3161_timestamp_server(workflow_text: str):
    """The workflow must use an RFC-3161 timestamp server."""
    assert EXPECTED_TIMESTAMP_SERVER in workflow_text, (
        f"tauri-windows-build.yml must use the RFC-3161 timestamp server "
        f"{EXPECTED_TIMESTAMP_SERVER} (signing-guide.md §'Timestamp server')."
    )
    # The /tr flag (RFC-3161 timestamp URL). NOT the legacy /t flag.
    assert "/tr " in workflow_text or "/tr\t" in workflow_text or "/tr`" in workflow_text, (
        "tauri-windows-build.yml must use the `/tr` flag (RFC-3161 timestamp) "
        "instead of the legacy `/t` flag (signing-guide.md §'Timestamp server')."
    )


def test_workflow_does_not_use_legacy_t_flag(workflow_text: str):
    """The workflow must NOT use the legacy ``/t`` timestamp flag."""
    # Look for `/t ` (with trailing space), this would indicate the legacy
    legacy_pattern = " /t http"
    assert legacy_pattern not in workflow_text, (
        "tauri-windows-build.yml must NOT use the legacy `/t http://...` "
        "timestamp flag, use `/tr` (RFC-3161) instead (signing-guide.md)."
    )


def test_workflow_uses_sha256_digest_algorithm(workflow_text: str):
    """The workflow must use SHA-256 as the signature digest algorithm."""
    assert "/fd SHA256" in workflow_text, (
        "tauri-windows-build.yml must use `/fd SHA256` for the file digest "
        "algorithm (SHA-1 is deprecated; signing-guide.md mandates SHA-256)."
    )


def test_workflow_uses_sha256_timestamp_digest(workflow_text: str):
    """The workflow must use SHA-256 as the timestamp digest algorithm."""
    assert "/td SHA256" in workflow_text, (
        "tauri-windows-build.yml must use `/td SHA256` for the timestamp "
        "digest algorithm (paired with `/tr` for RFC-3161 timestamps)."
    )


def test_sidecar_build_script_documents_signing_next_step(
    sidecar_script_text: str,
):
    """``build_sidecar_windows.sh`` must document the signtool next-step."""
    assert "signtool" in sidecar_script_text.lower() or ("signing-guide.md" in sidecar_script_text), (
        "build_sidecar_windows.sh must document the signing next-step "
        "(reference signtool and/or signing-guide.md §13.1) at the end "
        "of the script so a developer running it locally knows to sign."
    )
    # The script's final echo line should reference signing-guide.md.
    assert "signing-guide.md" in sidecar_script_text, (
        "build_sidecar_windows.sh must reference signing-guide.md in its "
        "NEXT-step echo (the authoritative signing reference)."
    )


def test_prewarm_build_script_documents_signing_next_step(
    prewarm_script_text: str,
):
    """``build_prewarm_windows.sh`` must document the signtool next-step."""
    assert "signtool" in prewarm_script_text.lower() or ("signing-guide.md" in prewarm_script_text), (
        "build_prewarm_windows.sh must document the signing next-step "
        "(reference signtool and/or signing-guide.md §13.1) at the end "
        "of the script so a developer running it locally knows to sign."
    )
    assert "signing-guide.md" in prewarm_script_text, (
        "build_prewarm_windows.sh must reference signing-guide.md in its "
        "NEXT-step echo (the authoritative signing reference)."
    )


def test_sidecar_build_script_signing_is_ci_only(
    sidecar_script_text: str,
):
    """GAP-3 (documented): the sidecar build script does NOT invoke signtool."""
    # The script's only signtool reference is in the final echo line:
    assert "signtool sign" not in sidecar_script_text, (
        "build_sidecar_windows.sh now invokes `signtool sign` directly, "
        "update this test to assert the script DOES sign (and remove "
        "GAP-3 from the module docstring)."
    )


def test_prewarm_build_script_signing_is_ci_only(
    prewarm_script_text: str,
):
    """GAP-3 (documented): the prewarm build script does NOT invoke signtool."""
    assert "signtool sign" not in prewarm_script_text, (
        "build_prewarm_windows.sh now invokes `signtool sign` directly, "
        "update this test to assert the script DOES sign (and remove "
        "GAP-3 from the module docstring)."
    )


def test_tauri_conf_has_bundle_block(tauri_conf_json: dict):
    """``tauri.conf.json`` must have a top-level ``bundle`` block."""
    assert "bundle" in tauri_conf_json, (
        "tauri.conf.json must have a top-level `bundle` block (the root of per-platform bundle config)."
    )
    assert isinstance(tauri_conf_json["bundle"], dict)


def test_tauri_conf_has_external_bin_for_sidecar(tauri_conf_json: dict):
    """The bundle must declare the sidecar as an ``externalBin``."""
    bundle = tauri_conf_json.get("bundle", {})
    external_bin = bundle.get("externalBin", [])
    assert "bin/python-sidecar" in external_bin, (
        "tauri.conf.json bundle.externalBin must include 'bin/python-sidecar' "
        "(the Tauri externalBin base name for the Nuitka sidecar)."
    )


def test_known_gap_tauri_conf_missing_bundle_windows(tauri_conf_json: dict):
    """XPLAT-4 FIXED: ``tauri.conf.json`` now HAS a ``bundle.windows`` block."""
    bundle = tauri_conf_json.get("bundle", {})
    # The linux block must exist (sanity check that the config is loaded).
    assert "linux" in bundle, (
        "Reference pattern broken: tauri.conf.json bundle should have a "
        "`linux` block (deb + rpm postInstallScript entries)."
    )
    # FIXED: the windows block is now present.
    assert "windows" in bundle, "tauri.conf.json should have a `bundle.windows` block (XPLAT-4 fix)."
    windows = bundle["windows"]
    assert "signCommand" in windows, (
        "bundle.windows.signCommand must be set (XPLAT-4 fix, env-var ref for CI/production signing)."
    )


# 6. : MSI is NOT signed by the workflow ─────────────────────────────
def test_known_gap_msi_not_signed_in_workflow(workflow_text: str):
    """GAP-1 (documented): the workflow does NOT sign the MSI installer."""
    # The workflow produces an msi_path artifact output (used for upload)
    assert "msi_path" in workflow_text, (
        "Reference pattern broken: tauri-windows-build.yml should produce "
        "an `msi_path` artifact output (used for upload)."
    )
    # Look for any line that contains BOTH "signtool sign" AND "msi_path".
    msi_signed = False
    for line in workflow_text.splitlines():
        if "signtool sign" in line and "msi_path" in line:
            msi_signed = True
            break
    lines = workflow_text.splitlines()
    for i, line in enumerate(lines):
        if "signtool sign" in line:
            window = "\n".join(lines[i : i + 6])
            if "msi_path" in window:
                msi_signed = True
                break
    assert not msi_signed, (
        "tauri-windows-build.yml now signs the MSI installer, update "
        "this test to assert the MSI IS signed, and remove GAP-1 from "
        "the module docstring."
    )


def test_signing_guide_exists_and_documents_windows_authenticode():
    """``docs/migration/signing-guide.md`` must exist + document Windows."""
    assert SIGNING_GUIDE.is_file(), f"signing-guide.md not found at {SIGNING_GUIDE}."
    text = SIGNING_GUIDE.read_text(encoding="utf-8")
    # Section header for Windows Authenticode.
    assert "Windows: Authenticode" in text, (
        "signing-guide.md must have a 'Windows: Authenticode' section (per ADR-0020 §13.1 cross-reference)."
    )
    # The signtool command must be documented.
    assert "signtool sign" in text, "signing-guide.md must document the `signtool sign` command."
    # The timestamp server must be documented.
    assert EXPECTED_TIMESTAMP_SERVER in text, (
        f"signing-guide.md must document the {EXPECTED_TIMESTAMP_SERVER} timestamp server."
    )
    # The WIN_CSC_LINK env var must be documented.
    assert "WIN_CSC_LINK" in text, (
        "signing-guide.md must document the WIN_CSC_LINK env var "
        "(Authenticode PFX cert path, reused from the predecessor build)."
    )


def test_adr_0020_section_13_1_documents_windows_signing():
    """ADR-0020 §13.1 must document the Windows Authenticode signing spec."""
    assert ADR_0020.is_file(), f"ADR-0020 not found at {ADR_0020}."
    text = ADR_0020.read_text(encoding="utf-8")
    # Section header for §13.1 Windows.
    assert "13.1 Windows (Authenticode)" in text, "ADR-0020 must have a §13.1 'Windows (Authenticode)' section."
    # The signtool command + timestamp server must be specified.
    assert "signtool sign" in text, "ADR-0020 §13.1 must specify the `signtool sign` command."
    assert EXPECTED_TIMESTAMP_SERVER in text, (
        f"ADR-0020 §13.1 must specify the {EXPECTED_TIMESTAMP_SERVER} timestamp server."
    )
    # The cert env var reuse must be documented.
    assert "WIN_CSC_LINK" in text, "ADR-0020 §13.1 must document the WIN_CSC_LINK env var reuse."


def test_workflow_has_signing_step_for_sidecar(workflow_text: str):
    """The workflow must have a dedicated signing step for the sidecar."""
    # The step name appears as `name: Sign sidecar + native listener (...)`.
    assert "Sign sidecar + native listener" in workflow_text, (
        "tauri-windows-build.yml must have a step named 'Sign sidecar + "
        "native listener' (runs signtool on the sidecar + native listener "
        "BEFORE the Tauri build, per ADR-0020 §13.1 signing order)."
    )


def test_workflow_has_signing_step_for_nsis(workflow_text: str):
    """The workflow must have a dedicated signing step for the NSIS installer."""
    assert "Sign the final NSIS installer" in workflow_text, (
        "tauri-windows-build.yml must have a step named 'Sign the final "
        "NSIS installer' (runs signtool on the NSIS installer AFTER the "
        "Tauri build, per ADR-0020 §13.1 signing order)."
    )


def test_workflow_signing_step_runs_before_tauri_build(workflow_text: str):
    """The sidecar signing step must run BEFORE the Tauri build step."""
    sign_sidecar_pos = workflow_text.find("Sign sidecar + native listener")
    build_tauri_pos = workflow_text.find("Build the Tauri app")
    assert sign_sidecar_pos != -1, "tauri-windows-build.yml must have a 'Sign sidecar + native listener' step."
    assert build_tauri_pos != -1, "tauri-windows-build.yml must have a 'Build the Tauri app' step."
    assert sign_sidecar_pos < build_tauri_pos, (
        "tauri-windows-build.yml: the 'Sign sidecar + native listener' step must "
        "appear BEFORE the 'Build the Tauri app' step (ADR-0020 §13.1 "
        "signing order, sign the sidecar before it enters the bundle)."
    )


def test_workflow_nsis_signing_step_runs_after_tauri_build(workflow_text: str):
    """The NSIS signing step must run AFTER the Tauri build step."""
    build_tauri_pos = workflow_text.find("Build the Tauri app")
    sign_nsis_pos = workflow_text.find("Sign the final NSIS installer")
    assert build_tauri_pos != -1
    assert sign_nsis_pos != -1
    assert build_tauri_pos < sign_nsis_pos, (
        "tauri-windows-build.yml: the 'Build the Tauri app' step must "
        "appear BEFORE the 'Sign the final NSIS installer' step "
        "(ADR-0020 §13.1 signing order, sign the installer after it's built)."
    )
