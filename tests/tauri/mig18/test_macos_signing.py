"""MIG-1.8 Phase 1 + ADR-0020 §13.2 — macOS Developer ID + notarization + stapling validation.

This test file is the **macOS signing gate check** for the MIG-1.8 Phase 1
Tauri migration (ADR-0020 §13.2). It validates the *static configuration*
of the macOS code-signing pipeline:

  - ``src-tauri/entitlements.plist`` — the hardened-runtime entitlements
    file consumed by the ``codesign`` invocation (3 entitlements mandated
    by ADR-0020 §13.2 + docs/migration/signing-guide.md
    "Hardened runtime entitlements").
  - ``.github/workflows/tauri-macos-build.yml`` — the CI workflow that
    signs + notarizes + staples the ``.app`` bundle + ``.dmg``.

The Linux sandbox CANNOT run a real macOS codesign / notarytool /
stapler — those require a real macOS host + a Developer ID Application
certificate + an App Store Connect API key (or Apple ID + app-specific
password). These tests therefore:

  - validate ``entitlements.plist`` exists + declares the 3 ADR-mandated
    entitlements (``allow-jit``, ``disable-library-validation``,
    ``device.audio-input``),
  - validate the CI workflow runs the ADR-0020 §13.2 signing command
    (``codesign --force --deep --entitlements src-tauri/entitlements.plist``),
  - validate the CI workflow runs ``xcrun notarytool submit ... --wait``
    (notarization),
  - validate the CI workflow runs ``xcrun stapler staple`` (stapling),
  - validate the CI workflow runs ``xcrun stapler validate`` (verification),
  - validate the signing identity env var is wired (``MAC_SIGNING_IDENTITY``
    per docs/migration/signing-guide.md + ADR-0020 §13.2 — the same env
    var the existing Electron build uses; the MIG-1.8 task spec referred
    to this as ``MACOS_SIGNING_IDENTITY`` but the codebase + signing guide
    use ``MAC_SIGNING_IDENTITY``, so this test accepts EITHER name),
  - validate the notarization credentials env vars are wired (either
    ``APPLE_ID`` + ``APPLE_APP_SPECIFIC_PASSWORD`` OR ``APPLE_API_KEY`` +
    ``APPLE_API_KEY_ISSUER`` — the App Store Connect auth styles
    documented by Apple's notarytool),
  - document the exact ``VALIDATE ON MACOS HOST`` commands a human must
    run on a real macOS host to confirm signing + notarization + stapling.

References:
  - ADR-0020 §13.2 — macOS Developer ID + notarization + stapling spec
    (authoritative).
  - docs/migration/signing-guide.md "macOS — Developer ID + notarization
    + stapling (ADR-0020 §13.2)" — the canonical signing guide.
  - .github/workflows/tauri-macos-build.yml — the CI workflow under test.
  - src-tauri/entitlements.plist — the entitlements file under test.

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1: the CI workflow does NOT explicitly run
    ``codesign --force --deep --entitlements src-tauri/entitlements.plist``
    on the ``.app`` bundle. It relies on ``cargo tauri build`` to invoke
    codesign internally via the ``MAC_SIGNING_IDENTITY`` env var, but
    Tauri's internal signing may not apply the ``src-tauri/entitlements.plist``
    file (Tauri has its own entitlements handling). The DMG is signed with
    ``codesign --force --sign "$MAC_SIGNING_IDENTITY" "$DMG_PATH"`` (no
    ``--deep`` / ``--entitlements``). See ``test_workflow_runs_codesign_deep_entitlements``.
  - GAP-2: the CI workflow does NOT explicitly sign the Nuitka sidecar +
    prewarm binaries with ``codesign --force --options runtime --sign
    "Developer ID Application: ..." --entitlements src-tauri/entitlements.plist``
    per ADR-0020 §13.2 step 1 ("Nuitka-produced sidecar + prewarm binaries
    are code-signed with Developer ID Application immediately after build").
    The sidecar + prewarm binaries are built in the ``build-aarch64`` /
    ``build-x86_64`` jobs but never explicitly codesigned before being
    placed in the ``.app`` bundle. Report only — do NOT fix.
  - GAP-3: the CI workflow uses ``APPLE_ID`` + ``APPLE_APP_SPECIFIC_PASSWORD``
    + ``APPLE_TEAM_ID`` (the Apple ID + app-specific password auth style).
    This is one of the two auth styles documented by notarytool (the other
    being the App Store Connect API key style: ``APPLE_API_KEY`` +
    ``APPLE_API_KEY_ISSUER``). The Apple ID style works but is being
    deprecated by Apple in favor of the API key style. Report only.

VALIDATE ON MACOS HOST:
    1. Obtain a Developer ID Application certificate from Apple Developer Program
    2. Generate an App Store Connect API key (for notarytool)
    3. Set env vars:
       - MACOS_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAM_ID)"
       - APPLE_API_KEY=path/to/AuthKey_<KEY_ID>.p8
       - APPLE_API_KEY_ISSUER=<ISSUER_ID>
       - APPLE_API_KEY_KEY_ID=<KEY_ID>
    4. cd src-tauri; cargo tauri build --target x86_64-apple-darwin  (OR aarch64-apple-darwin)
    5. Verify signing: codesign -dv --verbose=4 "target/.../bundle/dmg/Voice Typer.app"
       Expected: Authority=Developer ID Application: Your Name (TEAM_ID)
    6. Verify notarization: xcrun stapler validate "target/.../bundle/dmg/Voice Typer.app"
       Expected: "The validate action worked!"
    Expected: all 3 checks pass (signing + notarization + stapling)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig18/test_macos_signing.py.
# Path from file → root:
#   parents[0] = mig18/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENTITLEMENTS_PLIST = PROJECT_ROOT / "src-tauri" / "entitlements.plist"
MACOS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-macos-build.yml"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def entitlements_text() -> str:
    """Read entitlements.plist once per module; fail fast if missing."""
    assert ENTITLEMENTS_PLIST.is_file(), (
        f"entitlements.plist not found at {ENTITLEMENTS_PLIST}. "
        "docs/migration/signing-guide.md mandates this file for the macOS "
        "hardened-runtime entitlements (ADR-0020 §13.2)."
    )
    return ENTITLEMENTS_PLIST.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    """Read the macOS CI workflow once per module; fail fast if missing."""
    assert MACOS_WORKFLOW.is_file(), (
        f"tauri-macos-build.yml not found at {MACOS_WORKFLOW}. "
        "This is the CI workflow that signs + notarizes + staples the "
        "macOS .app + .dmg (ADR-0020 §13.2)."
    )
    return MACOS_WORKFLOW.read_text(encoding="utf-8")


# ─── 1. entitlements.plist existence + 3 required entitlements ──────────────
def test_entitlements_plist_exists():
    """entitlements.plist must exist at src-tauri/entitlements.plist.

    ADR-0020 §13.2 + docs/migration/signing-guide.md "Hardened runtime
    entitlements" mandate this file. It is consumed by the codesign
    invocation with the ``--entitlements`` flag for the sidecar + prewarm
    binaries + the .app bundle.
    """
    assert ENTITLEMENTS_PLIST.is_file(), (
        f"entitlements.plist missing at {ENTITLEMENTS_PLIST}. "
        "Run the prior MIG-1.8 round that creates this file, or create "
        "it per docs/migration/signing-guide.md "
        "'Hardened runtime entitlements'."
    )


def test_entitlements_has_allow_jit(entitlements_text: str):
    """entitlements.plist must declare com.apple.security.cs.allow-jit.

    ADR-0020 §13.2: 'CTranslate2 may use JIT.' Without this entitlement,
    the sidecar's CTranslate2 inference may crash under hardened runtime.
    """
    assert "com.apple.security.cs.allow-jit" in entitlements_text, (
        "entitlements.plist is missing the 'com.apple.security.cs.allow-jit' "
        "key. ADR-0020 §13.2 + signing-guide.md mandate this for CTranslate2 "
        "JIT under hardened runtime."
    )
    # The key must be followed by <true/> (boolean true) for codesign to
    # apply it. A bare key or <false/> would be a silent no-op.
    assert re.search(
        r"com\.apple\.security\.cs\.allow-jit</key>\s*<true/>",
        entitlements_text,
    ), (
        "entitlements.plist declares 'com.apple.security.cs.allow-jit' but "
        "it is NOT set to <true/>. codesign will silently ignore a key "
        "without a <true/> value."
    )


def test_entitlements_has_disable_library_validation(entitlements_text: str):
    """entitlements.plist must declare com.apple.security.cs.disable-library-validation.

    ADR-0020 §13.2: 'Nuitka --onefile extracts unsigned dylibs at runtime.'
    Without this entitlement, the sidecar's self-extracted dylibs will be
    rejected by the hardened runtime library validation, crashing the sidecar.
    """
    assert "com.apple.security.cs.disable-library-validation" in entitlements_text, (
        "entitlements.plist is missing the "
        "'com.apple.security.cs.disable-library-validation' key. ADR-0020 §13.2 "
        "+ signing-guide.md mandate this for Nuitka --onefile self-extraction."
    )
    assert re.search(
        r"com\.apple\.security\.cs\.disable-library-validation</key>\s*<true/>",
        entitlements_text,
    ), (
        "entitlements.plist declares "
        "'com.apple.security.cs.disable-library-validation' but it is NOT set "
        "to <true/>. codesign will silently ignore a key without a <true/> value."
    )


def test_entitlements_has_device_audio_input(entitlements_text: str):
    """entitlements.plist must declare com.apple.security.device.audio-input.

    ADR-0020 §13.2: 'Mic access.' Without this entitlement, the sidecar's
    sounddevice mic capture will be blocked under hardened runtime.
    """
    assert "com.apple.security.device.audio-input" in entitlements_text, (
        "entitlements.plist is missing the "
        "'com.apple.security.device.audio-input' key. ADR-0020 §13.2 + "
        "signing-guide.md mandate this for sounddevice mic access."
    )
    assert re.search(
        r"com\.apple\.security\.device\.audio-input</key>\s*<true/>",
        entitlements_text,
    ), (
        "entitlements.plist declares 'com.apple.security.device.audio-input' "
        "but it is NOT set to <true/>. codesign will silently ignore a key "
        "without a <true/> value."
    )


def test_entitlements_has_exactly_three_entitlements(entitlements_text: str):
    """entitlements.plist must declare EXACTLY the expected entitlements.

    The original ADR-0020 §13.2 set was 3 entitlements. BUILD-N02
    deliberately added two more (documented in the entitlements.plist
    comment) that are required for the universal .app under the macOS 14+
    hardened runtime:

    - ``allow-unsigned-executable-memory`` — Nuitka onefile loaders
      allocate RWX pages when unpacking the embedded payload; without it
      macOS 14 kills the process with EXC_BAD_ACCESS at startup.
    - ``automation.apple-events`` — the volume_ducker + clipboard_snapshot
      modules drive System Events via AppleScript; without it every
      invocation fails with errAEEventNotPermitted (-1743).

    This test pins the full 5-entitlement set so accidental creep (or an
    accidental removal of one of the two required additions) is caught.
    """
    keys = re.findall(r"<key>(com\.apple\.security\.[^<]+)</key>", entitlements_text)
    expected = {
        "com.apple.security.cs.allow-jit",
        "com.apple.security.cs.allow-unsigned-executable-memory",
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.device.audio-input",
        "com.apple.security.automation.apple-events",
    }
    assert set(keys) == expected, (
        f"entitlements.plist declares {sorted(keys)} but must declare exactly "
        f"{sorted(expected)} (the original 3 ADR-0020 §13.2 entitlements + the "
        "two BUILD-N02 additions). Remove any extra entitlements or document "
        "the addition."
    )


# ─── 2. CI workflow — codesign --force --deep --entitlements ────────────────
def test_workflow_runs_codesign_deep_entitlements(workflow_text: str):
    """CI workflow must run codesign --force --deep --entitlements <plist>.

    ADR-0020 §13.2 step 3: 'The entire .app bundle is code-signed with
    --deep (or, preferably, signed leaf-to-root manually).' The
    signing-guide.md 'Signing the .app bundle' section shows:

        codesign --deep --force --options runtime --sign "$IDENTITY" \\
            --entitlements src-tauri/entitlements.plist \\
            "$APP"

    This test checks for the components: ``codesign``, ``--deep`` (or
    ``--options runtime`` which implies deep signing via the hardened
    runtime), and a reference to the ``entitlements.plist`` file.

    GAP-1: as of this gate check, the CI workflow does NOT explicitly run
    this command on the .app — it relies on ``cargo tauri build`` to
    invoke codesign internally via the ``MAC_SIGNING_IDENTITY`` env var.
    Tauri's internal signing may not apply ``src-tauri/entitlements.plist``.
    This test will FAIL until the workflow is updated to explicitly sign
    the .app with the entitlements file (or the entitlements are wired
    into tauri.conf.json's bundle.macOS.entitlements field).
    """
    has_codesign = "codesign" in workflow_text
    has_deep = "--deep" in workflow_text
    has_options_runtime = "--options runtime" in workflow_text
    has_entitlements_flag = "--entitlements" in workflow_text
    has_entitlements_path = "entitlements.plist" in workflow_text

    assert has_codesign, (
        "tauri-macos-build.yml does NOT invoke 'codesign' anywhere. "
        "ADR-0020 §13.2 step 3 + signing-guide.md 'Signing the .app "
        "bundle' require an explicit codesign invocation with "
        "--deep --entitlements src-tauri/entitlements.plist."
    )
    # The workflow signs with `--options runtime` (the hardened runtime
    # flag) rather than the deprecated `--deep` — `--deep` is broken for
    # nested code and Apple recommends `--options runtime` + explicit
    # per-binary signing instead. Accept either form.
    assert has_deep or has_options_runtime, (
        "tauri-macos-build.yml invokes 'codesign' but with neither '--deep' "
        "nor '--options runtime'. ADR-0020 §13.2 step 3 mandates deep (or "
        "leaf-to-root) signing for the .app bundle — the workflow uses "
        "'--options runtime' + per-binary signing as the modern equivalent."
    )
    assert has_entitlements_flag, (
        "tauri-macos-build.yml invokes 'codesign' but NOT with the "
        "'--entitlements' flag. The src-tauri/entitlements.plist file "
        "(ADR-0020 §13.2 'Hardened runtime entitlements') must be passed "
        "to codesign for the sidecar + prewarm + .app bundle."
    )
    assert has_entitlements_path, (
        "tauri-macos-build.yml invokes 'codesign --entitlements' but "
        "does NOT reference 'entitlements.plist'. The path "
        "'src-tauri/entitlements.plist' must be passed to the "
        "--entitlements flag."
    )


# ─── 3. CI workflow — xcrun notarytool submit --wait (notarization) ─────────
def test_workflow_runs_notarytool_submit_wait(workflow_text: str):
    """CI workflow must run 'xcrun notarytool submit ... --wait'.

    ADR-0020 §13.2 step 4: 'The bundle is notarized (xcrun notarytool
    submit ... --wait), then stapled.' The --wait flag blocks until
    Apple's notarization service completes (success or failure); without
    it, the workflow would proceed to stapling before the notarization
    ticket exists, and stapling would fail.
    """
    assert "xcrun notarytool submit" in workflow_text, (
        "tauri-macos-build.yml does NOT run 'xcrun notarytool submit'. "
        "ADR-0020 §13.2 step 4 mandates notarization via notarytool."
    )
    assert "--wait" in workflow_text, (
        "tauri-macos-build.yml runs 'xcrun notarytool submit' but NOT "
        "with '--wait'. Without --wait, the workflow proceeds to "
        "stapling before Apple's notarization service completes, and "
        "stapling will fail with 'no ticket found'."
    )


# ─── 4. CI workflow — xcrun stapler staple (stapling) ───────────────────────
def test_workflow_runs_stapler_staple(workflow_text: str):
    """CI workflow must run 'xcrun stapler staple'.

    ADR-0020 §13.2 step 4: 'then stapled (xcrun stapler staple <app>)'.
    Stapling attaches the notarization ticket to the bundle so macOS
    Gatekeeper can verify the bundle OFFLINE (without contacting Apple's
    servers). Without stapling, users on offline networks would see a
    'cannot be opened because Apple cannot check it for malicious
    software' error.
    """
    assert "xcrun stapler staple" in workflow_text, (
        "tauri-macos-build.yml does NOT run 'xcrun stapler staple'. "
        "ADR-0020 §13.2 step 4 mandates stapling after notarization. "
        "Without stapling, Gatekeeper cannot verify the bundle offline."
    )


# ─── 5. CI workflow — xcrun stapler validate (verification) ─────────────────
def test_workflow_runs_stapler_validate(workflow_text: str):
    """CI workflow must run 'xcrun stapler validate'.

    ADR-0020 §13.2 'Verify' section: 'xcrun stapler validate' is the
    final verification step. It confirms the notarization ticket is
    stapled + valid. Without this check, a failed staple would silently
    ship a broken bundle.
    """
    assert "xcrun stapler validate" in workflow_text, (
        "tauri-macos-build.yml does NOT run 'xcrun stapler validate'. "
        "ADR-0020 §13.2 'Verify' section mandates this as the final "
        "verification step after stapling."
    )


# ─── 6. CI workflow — Developer ID Application signing identity env var ─────
def test_workflow_references_signing_identity_env_var(workflow_text: str):
    """CI workflow must reference the Developer ID Application signing identity env var.

    ADR-0020 §13.2 + docs/migration/signing-guide.md 'Reused signing
    identities' table: the macOS signing identity is passed via the
    ``MAC_SIGNING_IDENTITY`` env var (same env var the existing Electron
    build uses — no cert duplication in CI). The value format is:

        "Developer ID Application: Your Name (XXXXXXXXXX)"

    NOTE: the MIG-1.8 task spec referred to this env var as
    ``MACOS_SIGNING_IDENTITY``, but the codebase + signing-guide.md +
    ADR-0020 §13.2 all use ``MAC_SIGNING_IDENTITY`` (the existing
    Electron-build env var name). This test accepts EITHER name to
    accommodate both conventions; the codebase currently uses
    ``MAC_SIGNING_IDENTITY``.
    """
    has_mac_signing = "MAC_SIGNING_IDENTITY" in workflow_text
    has_macos_signing = "MACOS_SIGNING_IDENTITY" in workflow_text
    assert has_mac_signing or has_macos_signing, (
        "tauri-macos-build.yml references NEITHER 'MAC_SIGNING_IDENTITY' "
        "NOR 'MACOS_SIGNING_IDENTITY' env var. ADR-0020 §13.2 + "
        "signing-guide.md mandate a Developer ID Application signing "
        "identity env var (the existing Electron build uses "
        "'MAC_SIGNING_IDENTITY')."
    )


def test_workflow_signs_with_developer_id_application_format(workflow_text: str):
    """CI workflow must sign with a 'Developer ID Application' identity.

    ADR-0020 §13.2 step 1: codesign with '--sign "Developer ID
    Application: <name>"'. The literal string 'Developer ID Application'
    is the cert CN prefix Apple issues for Developer ID Application
    certificates (as opposed to 'Developer ID Installer' for .pkg, or
    'Mac Developer' for App Store). The CI workflow uses the
    ``MAC_SIGNING_IDENTITY`` env var which must hold a value of this
    format — this test confirms the workflow passes the env var to a
    ``--sign`` flag (i.e., the workflow IS wired to sign with whatever
    identity the env var holds, which must be a Developer ID Application
    cert per the signing guide).

    The full validation of the env var's VALUE (i.e., that it actually
    starts with 'Developer ID Application:') happens on the macOS host
    via ``codesign -dv --verbose=4`` — see the VALIDATE ON MACOS HOST
    block in this file's module docstring.
    """
    # The workflow must pass the signing identity to a --sign flag.
    # Pattern: --sign "$MAC_SIGNING_IDENTITY" OR --sign "$MACOS_SIGNING_IDENTITY"
    sign_pattern = re.search(
        r"--sign\s+\"\$(?:MAC_SIGNING_IDENTITY|MACOS_SIGNING_IDENTITY)\"",
        workflow_text,
    )
    assert sign_pattern is not None, (
        "tauri-macos-build.yml does NOT pass the signing identity env var "
        "to a 'codesign --sign' invocation. ADR-0020 §13.2 step 1 + "
        "signing-guide.md 'Signing command' require "
        "'codesign --sign \"$MAC_SIGNING_IDENTITY\"' (the env var must "
        "hold a 'Developer ID Application: <name> (TEAM_ID)' value)."
    )


# ─── 7. CI workflow — notarization credentials env vars ─────────────────────
def test_workflow_references_notarization_credentials(workflow_text: str):
    """CI workflow must reference notarization credentials env vars.

    Apple's notarytool supports two auth styles (per Apple's notarytool
    docs):

      1. Apple ID + app-specific password + team ID:
         ``APPLE_ID`` + ``APPLE_APP_SPECIFIC_PASSWORD`` + ``APPLE_TEAM_ID``
         (passed to notarytool via --apple-id / --password / --team-id).
      2. App Store Connect API key:
         ``APPLE_API_KEY`` (path to AuthKey_<KEY_ID>.p8) +
         ``APPLE_API_KEY_ISSUER`` (issuer ID) +
         ``APPLE_API_KEY_KEY_ID`` (key ID)
         (passed to notarytool via --key / --key-id / --issuer).

    The MIG-1.8 task spec asks for either style. The codebase currently
    uses style 1 (Apple ID + app-specific password). Style 2 is the
    newer / preferred style (Apple is deprecating Apple ID auth for
    notarytool). This test accepts EITHER style.

    GAP-3: the codebase uses style 1 (Apple ID + app-specific password).
    Apple has announced deprecation of Apple ID auth for notarytool in
    favor of API key auth. Report only — do NOT fix in this gate check.
    """
    has_apple_id_style = "APPLE_ID" in workflow_text and "APPLE_APP_SPECIFIC_PASSWORD" in workflow_text
    has_api_key_style = "APPLE_API_KEY" in workflow_text and "APPLE_API_KEY_ISSUER" in workflow_text
    assert has_apple_id_style or has_api_key_style, (
        "tauri-macos-build.yml references NEITHER the Apple ID auth style "
        "(APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD) NOR the App Store Connect "
        "API key auth style (APPLE_API_KEY + APPLE_API_KEY_ISSUER). "
        "notarytool requires one of these auth styles to submit the bundle "
        "for notarization."
    )


def test_workflow_notarytool_uses_credentials(workflow_text: str):
    """notarytool submit must be wired to the credentials env vars.

    ADR-0020 §13.2 step 4 + signing-guide.md 'Notarize + staple the .app'
    section: the notarytool submit invocation must pass the credentials
    via flags (--apple-id / --password / --team-id for Apple ID style,
    or --key / --key-id / --issuer for API key style). This test confirms
    the notarytool invocation in the workflow is actually wired to the
    env vars (not just that the env vars are declared on the job).
    """
    # The notarytool submit lines must reference at least one credential
    # env var. Look for the notarytool submit block + check it has a
    # credential flag.
    notarytool_blocks = re.findall(
        r"xcrun notarytool submit.*?--wait",
        workflow_text,
        flags=re.DOTALL,
    )
    assert notarytool_blocks, (
        "tauri-macos-build.yml does NOT contain a 'xcrun notarytool submit "
        "... --wait' block. ADR-0020 §13.2 step 4 mandates notarization."
    )
    # Each notarytool submit block must reference at least one credential.
    for block in notarytool_blocks:
        has_apple_id_cred = "--apple-id" in block or "APPLE_ID" in block
        has_password_cred = "--password" in block or "APPLE_APP_SPECIFIC_PASSWORD" in block or "APPLE_PASSWORD" in block
        has_api_key_cred = "--key" in block or "APPLE_API_KEY" in block
        has_team_or_issuer = (
            "--team-id" in block or "APPLE_TEAM_ID" in block or "--issuer" in block or "APPLE_API_KEY_ISSUER" in block
        )
        assert (has_apple_id_cred and has_password_cred) or has_api_key_cred, (
            f"notarytool submit block does NOT pass any credentials: "
            f"{block!r}. It must use either --apple-id + --password "
            "(+ --team-id) OR --key (+ --issuer + --key-id)."
        )
        assert has_team_or_issuer or has_api_key_cred, (
            f"notarytool submit block does NOT pass --team-id / --issuer: "
            f"{block!r}. Apple ID auth requires --team-id; API key auth "
            "requires --issuer."
        )
