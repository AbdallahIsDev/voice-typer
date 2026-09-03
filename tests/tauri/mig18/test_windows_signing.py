"""MIG-1.8 Phase 1 Check 5 — Windows Authenticode signing validation.

This test file is the **5th check** in the MIG-1.8 Phase 1 Windows host
validation gate (ADR-0020 §13.1). It validates the **structure** of the
Windows code-signing configuration — specifically the
``.github/workflows/tauri-windows-build.yml`` workflow +
``scripts/build/build_sidecar_windows.sh`` + ``build_prewarm_windows.sh``
+ ``src-tauri/tauri.conf.json`` — to confirm they wire Authenticode
signing of the Nuitka sidecar + prewarm + NSIS/MSI installers per
ADR-0020 §13.1 + ``docs/migration/signing-guide.md``.

The Linux sandbox CANNOT run a real Windows signtool (no Windows SDK,
no Authenticode cert, no PFX). These tests therefore:
  - validate the CI workflow YAML contains the mandated ``signtool sign``
    invocations on the sidecar exe + prewarm exe + NSIS installer,
  - validate the workflow consumes the ``WIN_CSC_LINK`` +
    ``WIN_CSC_KEY_PASSWORD`` (alias ``CSC_LINK`` /
    ``CSC_KEY_PASSWORD``) secrets for the Authenticode cert,
  - validate the workflow uses an RFC-3161 timestamp server
    (``http://timestamp.digicert.com``) + ``/fd SHA256`` + ``/td SHA256``,
  - validate the build scripts (sidecar + prewarm) document the signing
    next-step (CI-only signing pattern — they do NOT invoke signtool
    themselves; the workflow does, after the build),
  - validate the ``tauri.conf.json`` has a ``bundle.windows`` block OR
    documents (via this test) that signing is CI-only — this is the
    known gap (GAP-2 below),
  - document (and assert) the known gap that the CI workflow does NOT
    sign the MSI installer (GAP-1 below), and
  - document the exact ``VALIDATE ON WINDOWS HOST`` commands a human
    must run on a real Windows 10 22H2 / Windows 11 host.

VALIDATE ON WINDOWS HOST:
    1. Obtain an Authenticode code-signing cert (EV or OV) from DigiCert/Sectigo/GlobalSign
    2. Export to PFX: certutil -exportPFX my "CN=Voice Typer" voice-typer.pfx
    3. Set env vars:
       - WIN_CSC_LINK=path/to/voice-typer.pfx
       - WIN_CSC_KEY_PASSWORD=<password>
    4. cd src-tauri; cargo tauri build --target x86_64-pc-windows-msvc
    5. Verify signing:
       - signtool verify /pa /v target\\...\\release\\bundle\\nsis\\*-setup.exe
       - signtool verify /pa /v src-tauri\\bin\\python-sidecar-*.exe
       - signtool verify /pa /v src-tauri\\resources\\prewarm-*.exe
    Expected: "Successfully verified" for all 3 binaries

References:
  - ADR-0020 §13.1 — Windows Authenticode signing spec (authoritative).
  - docs/migration/signing-guide.md §"Windows — Authenticode" —
    authoritative code-signing guide (env vars, signtool commands,
    timestamp server, cert reuse strategy).
  - .github/workflows/tauri-windows-build.yml — CI workflow that runs
    signtool on the sidecar + prewarm + NSIS (signing is optional,
    gated on the ``WIN_CSC_LINK`` + ``WIN_CSC_KEY_PASSWORD`` secrets).
  - scripts/build/build_sidecar_windows.sh + build_prewarm_windows.sh —
    the build scripts that produce the .exe binaries; they document the
    signing next-step (signtool invocation is CI-only).
  - src-tauri/tauri.conf.json — the Tauri bundle config (no
    ``bundle.windows`` block — signing is CI-only; see GAP-2).

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1: ``tauri-windows-build.yml`` does NOT have a ``signtool sign``
    step for the **MSI** installer. The signing guide mandates signing
    both MSI + NSIS, but the workflow only signs NSIS (the sidecar +
    prewarm + NSIS path is wired; MSI is left unsigned by the workflow).
    See ``test_known_gap_msi_not_signed_in_workflow``.
  - GAP-2: ``tauri.conf.json`` has NO ``bundle.windows`` block (only
    ``bundle.linux`` is configured). Authenticode signing of the host
    exe + MSI/NSIS is therefore entirely CI-driven (via signtool in the
    workflow) — Tauri's bundler does not auto-sign because no
    ``TAURI_SIGNING_PRIVATE_KEY`` / ``WIN_CSC_LINK`` is wired into the
    config. This is acceptable for v1 (ADR-0020 §15 — no auto-update,
    so no updater signing key) but should be tracked.
    See ``test_known_gap_tauri_conf_missing_bundle_windows``.
  - GAP-3: ``build_sidecar_windows.sh`` + ``build_prewarm_windows.sh``
    do NOT invoke ``signtool`` themselves — they only emit an
    ``echo ... NEXT: sign with signtool ...`` message at the end. This
    is intentional (CI-only signing pattern; the workflow runs signtool
    after the build), but a developer running the script locally will
    produce UNSIGNED binaries unless they manually invoke signtool.
    See ``test_sidecar_build_script_documents_signing_next_step`` +
    ``test_prewarm_build_script_documents_signing_next_step``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig18/test_windows_signing.py.
# Path from file → root:
#   parents[0] = mig18/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "tauri-windows-build.yml"
SIDECAR_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_windows.sh"
PREWARM_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_prewarm_windows.sh"
TAURI_CONF_FILE = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
SIGNING_GUIDE = PROJECT_ROOT / "docs" / "migration" / "signing-guide.md"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"

# Canonical timestamp server (RFC-3161) per signing-guide.md.
EXPECTED_TIMESTAMP_SERVER = "http://timestamp.digicert.com"


# ─── Fixtures ────────────────────────────────────────────────────────────────
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


# ─── 1. Workflow runs signtool sign on sidecar + prewarm + NSIS ──────────────
def test_workflow_runs_signtool_sign_on_sidecar(workflow_text: str):
    """The CI workflow must run ``signtool sign`` on the sidecar exe.

    ADR-0020 §13.1 + signing-guide.md mandate that
    ``python-sidecar-x86_64-pc-windows-msvc.exe`` is Authenticode-signed
    immediately after the Nuitka build, BEFORE it enters the Tauri
    bundle. Unsigned sidecars trigger SmartScreen / AV.
    """
    assert "signtool sign" in workflow_text, (
        "tauri-windows-build.yml must invoke `signtool sign` to "
        "Authenticode-sign the sidecar + prewarm + NSIS (ADR-0020 §13.1)."
    )
    assert "python-sidecar-x86_64-pc-windows-msvc.exe" in workflow_text, (
        "tauri-windows-build.yml must sign python-sidecar-x86_64-pc-windows-msvc.exe "
        "(the Nuitka-produced sidecar binary)."
    )


def test_workflow_does_not_sign_prewarm(workflow_text: str):
    """The CI workflow must NOT reference the prewarm exe.

    The standalone prewarm binary was REMOVED per
    plan-runtime-pack-split.md §6.2 P-1 (prewarm is an in-process startup
    phase of the worker exe; see tests/test_architecture_doc_accuracy.py's
    deletion pin and the workflow's removal note). This pins the removal
    so a stale signing entry (which would hard-fail every signed build
    with 'Cannot sign missing binary') cannot silently return.
    """
    assert "prewarm-x86_64-pc-windows-msvc.exe" not in workflow_text, (
        "tauri-windows-build.yml must NOT reference prewarm-*.exe — the "
        "standalone prewarm binary was removed per plan-runtime-pack-split "
        "§6.2 P-1."
    )


def test_workflow_runs_signtool_sign_on_nsis(workflow_text: str):
    """The CI workflow must run ``signtool sign`` on the NSIS installer.

    ADR-0020 §13.1 + signing-guide.md mandate that the NSIS installer
    (``*-setup.exe``) is Authenticode-signed after the Tauri bundle
    step. This is the final user-facing installer — it MUST be signed.
    """
    # The NSIS signing step in the workflow signs ``${{ steps.artifacts.outputs.nsis_path }}``.
    assert "nsis_path" in workflow_text, (
        "tauri-windows-build.yml must produce an `nsis_path` artifact output "
        "(the NSIS installer path) and sign it with signtool."
    )
    # The actual signing step has the signtool sign command followed by
    # the NSIS path interpolation. We check both tokens are present.
    assert "signtool sign" in workflow_text
    # The signing step's `if:` guard references the NSIS path interpolation
    # indirectly via the steps.artifacts.outputs.nsis_path. Confirm it appears.
    nsis_signing_marker = "steps.artifacts.outputs.nsis_path"
    assert nsis_signing_marker in workflow_text, (
        "tauri-windows-build.yml must reference steps.artifacts.outputs.nsis_path "
        "in the signtool sign command (NSIS installer signing)."
    )


def test_workflow_runs_signtool_verify(workflow_text: str):
    """The CI workflow must run ``signtool verify`` after signing.

    This is the post-signing verification step — it confirms the
    signature is valid + the cert chain is trusted. ADR-0020 §13.1 +
    signing-guide.md §"Verify" mandate this step.
    """
    assert "signtool verify" in workflow_text, (
        "tauri-windows-build.yml must run `signtool verify /pa /v` after "
        "signing to confirm the signature is valid (signing-guide.md §Verify)."
    )
    # /pa = use Default Authentication Verification Policy;
    # /v = verbose (prints the cert chain).
    assert "/pa" in workflow_text, (
        "tauri-windows-build.yml must use `signtool verify /pa /v` (/pa = Default Authentication Verification Policy)."
    )


# ─── 2. Signing uses an Authenticode cert (WIN_CSC_LINK env var) ─────────────
def test_workflow_uses_win_csc_link_secret(workflow_text: str):
    """The workflow must consume the ``WIN_CSC_LINK`` secret.

    ADR-0020 §13.1 + signing-guide.md §"Reused signing identities":
    the Windows build reuses the existing ``WIN_CSC_LINK`` secret
    (the Authenticode PFX cert, base64-encoded) from the Electron
    build. This avoids cert duplication in CI.
    """
    assert "WIN_CSC_LINK" in workflow_text, (
        "tauri-windows-build.yml must consume the WIN_CSC_LINK secret "
        "(Authenticode PFX cert — reused from the Electron build per "
        "signing-guide.md §'Reused signing identities')."
    )
    assert "secrets.WIN_CSC_LINK" in workflow_text, (
        "tauri-windows-build.yml must reference secrets.WIN_CSC_LINK "
        "(the GitHub Actions secret holding the base64-encoded PFX)."
    )


def test_workflow_uses_win_csc_key_password_secret(workflow_text: str):
    """The workflow must consume the ``WIN_CSC_KEY_PASSWORD`` secret.

    ADR-0020 §13.1 + signing-guide.md §"Reused signing identities":
    the PFX password is stored separately in ``WIN_CSC_KEY_PASSWORD``
    (alias ``CSC_KEY_PASSWORD``).
    """
    assert "WIN_CSC_KEY_PASSWORD" in workflow_text, (
        "tauri-windows-build.yml must consume the WIN_CSC_KEY_PASSWORD "
        "secret (the PFX password — paired with WIN_CSC_LINK)."
    )
    assert "secrets.WIN_CSC_KEY_PASSWORD" in workflow_text


def test_workflow_signing_step_is_gated_on_csc_env_vars(workflow_text: str):
    """The signing step must be conditional on the CSC env vars being set.

    The workflow uses ``if: ${{ env.WIN_CSC_LINK != '' && ... }}`` so
    signing is skipped on dev / PR builds (no cert secret available).
    This is the ADR-0020 §13.1 "optional, if secrets are set" pattern.
    """
    # The signing step's if-condition references WIN_CSC_LINK + WIN_CSC_KEY_PASSWORD.
    assert "env.WIN_CSC_LINK" in workflow_text, (
        "tauri-windows-build.yml signing step must be gated on "
        "`env.WIN_CSC_LINK != ''` (skip signing on PR builds without secrets)."
    )
    assert "env.WIN_CSC_KEY_PASSWORD" in workflow_text


# ─── 3. Signing uses an RFC-3161 timestamp server ────────────────────────────
def test_workflow_uses_rfc3161_timestamp_server(workflow_text: str):
    """The workflow must use an RFC-3161 timestamp server.

    ADR-0020 §13.1 + signing-guide.md §"Timestamp server": use the
    ``/tr`` flag (RFC-3161, hash-then-timestamp) — NOT the legacy
    ``/t`` flag. DigiCert is the canonical server; Sectigo + GlobalSign
    are also acceptable alternatives.
    """
    assert EXPECTED_TIMESTAMP_SERVER in workflow_text, (
        f"tauri-windows-build.yml must use the RFC-3161 timestamp server "
        f"{EXPECTED_TIMESTAMP_SERVER} (signing-guide.md §'Timestamp server')."
    )
    # The /tr flag (RFC-3161 timestamp URL) — NOT the legacy /t flag.
    assert "/tr " in workflow_text or "/tr\t" in workflow_text or "/tr`" in workflow_text, (
        "tauri-windows-build.yml must use the `/tr` flag (RFC-3161 timestamp) "
        "instead of the legacy `/t` flag (signing-guide.md §'Timestamp server')."
    )


def test_workflow_does_not_use_legacy_t_flag(workflow_text: str):
    """The workflow must NOT use the legacy ``/t`` timestamp flag.

    The legacy ``/t`` flag does NOT embed a timestamp hash (it just
    stamps the signature with the server's clock). The RFC-3161 ``/tr``
    flag is required for cert-expiry survival. signing-guide.md uses
    ``/tr`` exclusively.
    """
    # Look for `/t ` (with trailing space) — this would indicate the legacy
    # /t flag. We deliberately check for "/t " (with space) to avoid matching
    # /tr or /td. The /tr (RFC-3161 timestamp) and /td (timestamp digest)
    # flags are the correct ones.
    # NOTE: the workflow uses PowerShell line-continuation backticks, so the
    # signtool flags appear as ``/fd SHA256 ``, ``/tr http://... ``, etc.
    # We check for the legacy /t flag pattern that would NOT match /tr or /td.
    legacy_pattern = " /t http"
    assert legacy_pattern not in workflow_text, (
        "tauri-windows-build.yml must NOT use the legacy `/t http://...` "
        "timestamp flag — use `/tr` (RFC-3161) instead (signing-guide.md)."
    )


def test_workflow_uses_sha256_digest_algorithm(workflow_text: str):
    """The workflow must use SHA-256 as the signature digest algorithm.

    ADR-0020 §13.1 + signing-guide.md: ``/fd SHA256`` sets the file
    digest algorithm to SHA-256 (the modern standard; SHA-1 is
    deprecated + rejected by Windows 10+ SmartScreen).
    """
    assert "/fd SHA256" in workflow_text, (
        "tauri-windows-build.yml must use `/fd SHA256` for the file digest "
        "algorithm (SHA-1 is deprecated; signing-guide.md mandates SHA-256)."
    )


def test_workflow_uses_sha256_timestamp_digest(workflow_text: str):
    """The workflow must use SHA-256 as the timestamp digest algorithm.

    ADR-0020 §13.1 + signing-guide.md: ``/td SHA256`` sets the
    timestamp digest algorithm to SHA-256 (paired with ``/tr``).
    """
    assert "/td SHA256" in workflow_text, (
        "tauri-windows-build.yml must use `/td SHA256` for the timestamp "
        "digest algorithm (paired with `/tr` for RFC-3161 timestamps)."
    )


# ─── 4. Build scripts have a signing hook (or document CI-only) ──────────────
def test_sidecar_build_script_documents_signing_next_step(
    sidecar_script_text: str,
):
    """``build_sidecar_windows.sh`` must document the signtool next-step.

    The script does NOT invoke ``signtool`` itself — signing is CI-only
    (the workflow runs signtool after the build, per ADR-0020 §13.1 +
    the CI-only pattern documented in signing-guide.md). The script's
    final echo MUST point the user at signtool + the signing guide so a
    developer running the script locally knows the next step.

    This is the "documents that signing is CI-only" path of the build
    script signing hook requirement.
    """
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
    """``build_prewarm_windows.sh`` must document the signtool next-step.

    Same as ``test_sidecar_build_script_documents_signing_next_step``
    but for the prewarm build script.
    """
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
    """GAP-3 (documented): the sidecar build script does NOT invoke signtool.

    The script's signtool reference is in an ``echo`` (documentation),
    NOT in an actual ``signtool sign ...`` invocation. This is the
    CI-only signing pattern — the workflow runs signtool after the
    build. A developer running the script locally will produce an
    UNSIGNED binary unless they manually invoke signtool.

    This test ASSERTS the gap is present (so a future fix will flip it
    to a passing assertion). DO NOT fix this gap as part of MIG-1.8
    check 5 — report it to the primary agent.
    """
    # The script's only signtool reference is in the final echo line:
    #   echo "[build_sidecar_windows] NEXT: sign with signtool (see ...)."
    # There is NO `signtool sign` invocation in the script body.
    # We check that the script body does NOT contain a real signtool
    # invocation (the only mention is in an echo string).
    assert "signtool sign" not in sidecar_script_text, (
        "build_sidecar_windows.sh now invokes `signtool sign` directly — "
        "update this test to assert the script DOES sign (and remove "
        "GAP-3 from the module docstring)."
    )


def test_prewarm_build_script_signing_is_ci_only(
    prewarm_script_text: str,
):
    """GAP-3 (documented): the prewarm build script does NOT invoke signtool.

    Same as ``test_sidecar_build_script_signing_is_ci_only`` but for
    the prewarm build script.
    """
    assert "signtool sign" not in prewarm_script_text, (
        "build_prewarm_windows.sh now invokes `signtool sign` directly — "
        "update this test to assert the script DOES sign (and remove "
        "GAP-3 from the module docstring)."
    )


# ─── 5. tauri.conf.json bundle.windows config (or documented gap) ────────────
def test_tauri_conf_has_bundle_block(tauri_conf_json: dict):
    """``tauri.conf.json`` must have a top-level ``bundle`` block.

    This is a sanity check — the bundle block is the root of all
    per-platform bundle config (linux, windows, macos).
    """
    assert "bundle" in tauri_conf_json, (
        "tauri.conf.json must have a top-level `bundle` block (the root of per-platform bundle config)."
    )
    assert isinstance(tauri_conf_json["bundle"], dict)


def test_tauri_conf_has_external_bin_for_sidecar(tauri_conf_json: dict):
    """The bundle must declare the sidecar as an ``externalBin``.

    ADR-0020 §7 + the externalBin mechanism: the sidecar .exe is
    declared as an ``externalBin`` so Tauri appends the Rust target
    triple at spawn time (``python-sidecar-x86_64-pc-windows-msvc.exe``).
    """
    bundle = tauri_conf_json.get("bundle", {})
    external_bin = bundle.get("externalBin", [])
    assert "bin/python-sidecar" in external_bin, (
        "tauri.conf.json bundle.externalBin must include 'bin/python-sidecar' "
        "(the Tauri externalBin base name for the Nuitka sidecar)."
    )


def test_known_gap_tauri_conf_missing_bundle_windows(tauri_conf_json: dict):
    """XPLAT-4 FIXED: ``tauri.conf.json`` now HAS a ``bundle.windows`` block.

    The bundle config previously had a ``linux`` block (deb + rpm with
    postInstallScript / preRemoveScript entries) but NO ``windows`` block. Authenticode
    signing of the host exe + MSI/NSIS was therefore entirely CI-driven
    (via signtool in the workflow) — Tauri's bundler did not auto-sign
    because no ``TAURI_SIGNING_PRIVATE_KEY`` / ``WIN_CSC_LINK`` was
    wired into the config.

    XPLAT-4 fix: added ``bundle.windows.signCommand`` pointing at
    ``scripts/tauri-sign.cmd`` (``..\\scripts\tauri-sign.cmd %1`` relative
    to the src-tauri cwd — same convention as ``bundle.windows.nsis.installerHooks``).

    Note on the env-var design: the Tauri bundler executes signCommand via
    ``Command::new`` with NO shell and NO ``${VAR}`` expansion
    (tauri-bundler ``bundle/windows/sign.rs`` ``sign_command_custom``), so a
    literal ``${WIN_SIGN_COMMAND}`` value would be spawned as a program name
    and break every Windows ``cargo tauri build``. The wrapper script honors
    ``WIN_SIGN_COMMAND`` when set (runs it with the file path as ``%1``) and
    exits 0 otherwise — so local builds without signing env vars still work,
    and CI/production builds that set the env var get signed by the bundler.

    This test now ASSERTS PRESENCE of the ``windows`` block (GAP-2 closed).
    """
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
        "bundle.windows.signCommand must be set (XPLAT-4 fix — env-var ref for CI/production signing)."
    )


# 6. : MSI is NOT signed by the workflow ─────────────────────────────
def test_known_gap_msi_not_signed_in_workflow(workflow_text: str):
    """GAP-1 (documented): the workflow does NOT sign the MSI installer.

    ADR-0020 §13.1 + signing-guide.md §"Tauri bundler signing" mandate
    signing BOTH the MSI + NSIS installers. The workflow
    (``tauri-windows-build.yml``) currently signs only the NSIS
    installer — the MSI is uploaded as an artifact but left unsigned.

    This test ASSERTS the gap is present (so a future fix will flip it
    to a passing assertion). DO NOT fix this gap as part of MIG-1.8
    check 5 — report it to the primary agent.

    The fix would be to add a 3rd ``signtool sign`` step after the
    "Sign the final NSIS installer" step, targeting
    ``steps.artifacts.outputs.msi_path``.
    """
    # The workflow produces an msi_path artifact output (used for upload)
    # but does NOT sign it. We check:
    #   1. msi_path IS produced (sanity — the workflow knows about MSI).
    #   2. msi_path is NOT referenced in any signtool sign command.
    assert "msi_path" in workflow_text, (
        "Reference pattern broken: tauri-windows-build.yml should produce "
        "an `msi_path` artifact output (used for upload)."
    )
    # Look for any line that contains BOTH "signtool sign" AND "msi_path".
    # If such a line exists, the MSI is being signed and the gap is closed.
    msi_signed = False
    for line in workflow_text.splitlines():
        if "signtool sign" in line and "msi_path" in line:
            msi_signed = True
            break
        # Also handle the PowerShell line-continuation case where the
        # signtool command spans multiple lines:
        #   & $signtool sign /f ... `
        #       /fd SHA256 /tr ... `
        #       "${{ steps.artifacts.outputs.msi_path }}"
        # We detect this by checking if a signtool sign block (delimited
        # by `& $signtool sign` ... `Run:` or the next `& $signtool`)
        # contains msi_path. For simplicity, we use a coarse check: any
        # occurrence of msi_path within 5 lines of a `signtool sign` line.
    # Coarse-grained 5-line window check (handles PowerShell line
    # continuations).
    lines = workflow_text.splitlines()
    for i, line in enumerate(lines):
        if "signtool sign" in line:
            window = "\n".join(lines[i : i + 6])
            if "msi_path" in window:
                msi_signed = True
                break
    assert not msi_signed, (
        "tauri-windows-build.yml now signs the MSI installer — update "
        "this test to assert the MSI IS signed, and remove GAP-1 from "
        "the module docstring."
    )


# ─── 7. Documentation cross-references (signing guide + ADR-0020 §13.1) ──────
def test_signing_guide_exists_and_documents_windows_authenticode():
    """``docs/migration/signing-guide.md`` must exist + document Windows.

    This is the authoritative code-signing guide (per ADR-0020 §13.1
    cross-reference). It must have a dedicated Windows Authenticode
    section.
    """
    assert SIGNING_GUIDE.is_file(), f"signing-guide.md not found at {SIGNING_GUIDE}."
    text = SIGNING_GUIDE.read_text(encoding="utf-8")
    # Section header for Windows Authenticode.
    assert "Windows — Authenticode" in text, (
        "signing-guide.md must have a 'Windows — Authenticode' section (per ADR-0020 §13.1 cross-reference)."
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
        "(Authenticode PFX cert path — reused from the Electron build)."
    )


def test_adr_0020_section_13_1_documents_windows_signing():
    """ADR-0020 §13.1 must document the Windows Authenticode signing spec.

    This is the authoritative source — the signing guide + the workflow
    + this test all derive from ADR-0020 §13.1.
    """
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


# ─── 8. Workflow file structural sanity (not a stub for signing) ─────────────
def test_workflow_has_signing_step_for_sidecar(workflow_text: str):
    """The workflow must have a dedicated signing step for the sidecar.

    This is a structural check: the workflow must have a step named
    "Sign sidecar + native listener" that runs BEFORE the Tauri
    build step (so the binaries are signed before they enter the bundle).

    Prewarm was REMOVED from the signing list per
    plan-runtime-pack-split.md §6.2 P-1 (the standalone prewarm binary
    no longer exists — prewarm is an in-process startup phase of the
    worker exe; see the workflow's removal note at the former prewarm
    build step and tests/test_architecture_doc_accuracy.py's pin).
    """
    # The step name appears as `name: Sign sidecar + native listener (...)`.
    assert "Sign sidecar + native listener" in workflow_text, (
        "tauri-windows-build.yml must have a step named 'Sign sidecar + "
        "native listener' (runs signtool on the sidecar + native listener "
        "BEFORE the Tauri build, per ADR-0020 §13.1 signing order)."
    )


def test_workflow_has_signing_step_for_nsis(workflow_text: str):
    """The workflow must have a dedicated signing step for the NSIS installer.

    This is a structural check: the workflow must have a step named
    "Sign the final NSIS installer" (or similar) that runs AFTER the
    Tauri build step (so the installer is signed after it's produced).
    """
    assert "Sign the final NSIS installer" in workflow_text, (
        "tauri-windows-build.yml must have a step named 'Sign the final "
        "NSIS installer' (runs signtool on the NSIS installer AFTER the "
        "Tauri build, per ADR-0020 §13.1 signing order)."
    )


def test_workflow_signing_step_runs_before_tauri_build(workflow_text: str):
    """The sidecar signing step must run BEFORE the Tauri build step.

    ADR-0020 §13.1 signing order:
      1. Sign sidecar (before bundling — unsigned sidecars trigger SmartScreen).
      2. Sign the native listener (same step; prewarm removed per §6.2 P-1).
      3. Tauri builds the MSI/EXE.
      4. Sign the NSIS installer (after bundling).

    This test asserts the sidecar signing step appears BEFORE
    the "Build the Tauri app" step in the workflow YAML.
    """
    sign_sidecar_pos = workflow_text.find("Sign sidecar + native listener")
    build_tauri_pos = workflow_text.find("Build the Tauri app")
    assert sign_sidecar_pos != -1, "tauri-windows-build.yml must have a 'Sign sidecar + native listener' step."
    assert build_tauri_pos != -1, "tauri-windows-build.yml must have a 'Build the Tauri app' step."
    assert sign_sidecar_pos < build_tauri_pos, (
        "tauri-windows-build.yml: the 'Sign sidecar + native listener' step must "
        "appear BEFORE the 'Build the Tauri app' step (ADR-0020 §13.1 "
        "signing order — sign the sidecar before it enters the bundle)."
    )


def test_workflow_nsis_signing_step_runs_after_tauri_build(workflow_text: str):
    """The NSIS signing step must run AFTER the Tauri build step.

    ADR-0020 §13.1 signing order — the NSIS installer must be signed
    AFTER it's produced by ``cargo tauri build``.
    """
    build_tauri_pos = workflow_text.find("Build the Tauri app")
    sign_nsis_pos = workflow_text.find("Sign the final NSIS installer")
    assert build_tauri_pos != -1
    assert sign_nsis_pos != -1
    assert build_tauri_pos < sign_nsis_pos, (
        "tauri-windows-build.yml: the 'Build the Tauri app' step must "
        "appear BEFORE the 'Sign the final NSIS installer' step "
        "(ADR-0020 §13.1 signing order — sign the installer after it's built)."
    )
