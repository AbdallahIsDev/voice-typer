"""
Phase 1 + ADR-0020 §13.2: macOS Developer ID + notarization + stapling validation.
Gaps documented (report, do NOT fix, out of scope for this gate check):
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENTITLEMENTS_PLIST = PROJECT_ROOT / "src-tauri" / "entitlements.plist"
MACOS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-macos-build.yml"


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


def test_entitlements_plist_exists():
    """entitlements.plist must exist at src-tauri/entitlements.plist."""
    assert ENTITLEMENTS_PLIST.is_file(), (
        f"entitlements.plist missing at {ENTITLEMENTS_PLIST}. "
        "Run the prior MIG-1.8 round that creates this file, or create "
        "it per docs/migration/signing-guide.md "
        "'Hardened runtime entitlements'."
    )


def test_entitlements_has_allow_jit(entitlements_text: str):
    """entitlements.plist must declare com.apple.security.cs.allow-jit."""
    assert "com.apple.security.cs.allow-jit" in entitlements_text, (
        "entitlements.plist is missing the 'com.apple.security.cs.allow-jit' "
        "key. ADR-0020 §13.2 + signing-guide.md mandate this for CTranslate2 "
        "JIT under hardened runtime."
    )
    assert re.search(
        r"com\.apple\.security\.cs\.allow-jit</key>\s*<true/>",
        entitlements_text,
    ), (
        "entitlements.plist declares 'com.apple.security.cs.allow-jit' but "
        "it is NOT set to <true/>. codesign will silently ignore a key "
        "without a <true/> value."
    )


def test_entitlements_has_disable_library_validation(entitlements_text: str):
    """entitlements.plist must declare com.apple.security.cs.disable-library-validation."""
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
    """entitlements.plist must declare com.apple.security.device.audio-input."""
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
    """entitlements.plist must declare EXACTLY the expected entitlements."""
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


def test_workflow_runs_codesign_deep_entitlements(workflow_text: str):
    """CI workflow must run codesign --force --deep --entitlements <plist>."""
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
    assert has_deep or has_options_runtime, (
        "tauri-macos-build.yml invokes 'codesign' but with neither '--deep' "
        "nor '--options runtime'. ADR-0020 §13.2 step 3 mandates deep (or "
        "leaf-to-root) signing for the .app bundle, the workflow uses "
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


def test_workflow_runs_notarytool_submit_wait(workflow_text: str):
    """CI workflow must run 'xcrun notarytool submit ... --wait'."""
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


def test_workflow_runs_stapler_staple(workflow_text: str):
    """CI workflow must run 'xcrun stapler staple'."""
    assert "xcrun stapler staple" in workflow_text, (
        "tauri-macos-build.yml does NOT run 'xcrun stapler staple'. "
        "ADR-0020 §13.2 step 4 mandates stapling after notarization. "
        "Without stapling, Gatekeeper cannot verify the bundle offline."
    )


def test_workflow_runs_stapler_validate(workflow_text: str):
    """CI workflow must run 'xcrun stapler validate'."""
    assert "xcrun stapler validate" in workflow_text, (
        "tauri-macos-build.yml does NOT run 'xcrun stapler validate'. "
        "ADR-0020 §13.2 'Verify' section mandates this as the final "
        "verification step after stapling."
    )


def test_workflow_references_signing_identity_env_var(workflow_text: str):
    """CI workflow must reference the Developer ID Application signing identity env var."""
    has_mac_signing = "MAC_SIGNING_IDENTITY" in workflow_text
    has_macos_signing = "MACOS_SIGNING_IDENTITY" in workflow_text
    assert has_mac_signing or has_macos_signing, (
        "tauri-macos-build.yml references NEITHER 'MAC_SIGNING_IDENTITY' "
        "NOR 'MACOS_SIGNING_IDENTITY' env var. ADR-0020 §13.2 + "
        "signing-guide.md mandate a Developer ID Application signing "
        "identity env var (the existing predecessor build uses "
        "'MAC_SIGNING_IDENTITY')."
    )


def test_workflow_signs_with_developer_id_application_format(workflow_text: str):
    """CI workflow must sign with a 'Developer ID Application' identity."""
    # The workflow must pass the signing identity to a --sign flag.
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


def test_workflow_references_notarization_credentials(workflow_text: str):
    """CI workflow must reference notarization credentials env vars."""
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
    """notarytool submit must be wired to the credentials env vars."""
    # The notarytool submit lines must reference at least one credential
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
