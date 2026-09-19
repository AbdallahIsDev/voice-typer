"""
Phase 0-M Gate Check 10 (+1): macOS autostart + installer validation.
KNOWN GAPS (report, do not fix)
"""

from __future__ import annotations

import json
import plistlib
import re
import sys
import urllib.request  # noqa: F401  (side-effect: cache in sys.modules)

# PRE-IMPORT: ``_enable_autostart_macos`` calls
import xml.sax.saxutils  # noqa: F401  (side-effect: cache in sys.modules)
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = _REPO_ROOT / "src-tauri" / "tauri.conf.json"
ENTITLEMENTS_PLIST = _REPO_ROOT / "src-tauri" / "entitlements.plist"
SRC_TAURI_DIR = TAURI_CONF.parent
CARGO_TOML = SRC_TAURI_DIR / "Cargo.toml"
MAIN_RS = SRC_TAURI_DIR / "src" / "main.rs"
CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "tauri-macos-build.yml"

# The Tauri host binary name (per src-tauri/Cargo.toml [[bin]] name=...).
_TAURI_HOST_BIN_NAME = "voice-typer-tauri"


@pytest.fixture
def darwin_platform(monkeypatch, tmp_path):
    """Pretend we're on macOS for the duration of the test."""
    monkeypatch.setattr(sys, "platform", "darwin")
    from voice_typer.server import server_platform
    from voice_typer.server.server_platform import platform_flags

    monkeypatch.setattr(platform_flags, "SYSTEM", "darwin")

    # Redirect Path.home() to a tmp dir so the plist is written there.
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server_platform.Path, "home", lambda: home)

    # Redirect _paths.config_dir() to tmp via the env override.
    config_dir = tmp_path / "config" / "voice-typer"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(config_dir))

    # Mock subprocess.run (imported locally inside _enable/_disable_autostart_macos)
    launchctl_calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        launchctl_calls.append(list(args))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)

    plist_path = home / "Library" / "LaunchAgents" / "com.voicetyper.plist"
    return SimpleNamespace(
        server_platform=server_platform,
        home=home,
        plist_path=plist_path,
        config_dir=config_dir,
        launchctl_calls=launchctl_calls,
    )


def _parse_plist(plist_path: Path) -> dict:
    """Parse a LaunchAgent plist XML file into a dict (plistlib format)."""
    assert plist_path.exists(), f"plist not found at {plist_path}"
    with plist_path.open("rb") as fh:
        return plistlib.load(fh)


def test_enable_autostart_macos_creates_launchagent_plist(darwin_platform):
    """``_enable_autostart_macos()`` writes a LaunchAgent plist at"""
    sp = darwin_platform.server_platform
    plist_path = darwin_platform.plist_path

    assert not plist_path.exists(), "precondition: plist should not exist yet"
    assert sp._enable_autostart_macos() is True

    # The plist must exist at the canonical per-user LaunchAgents path.
    assert plist_path.exists(), (
        f"plist must be written to {plist_path} (the per-user LaunchAgents "
        f"dir; NOT /Library/LaunchDaemons which needs root)"
    )
    assert (plist_path.stat().st_mode & 0o777) == 0o600, "plist must be chmod 0o600 (per-user, no group/other access)"

    bootstrap_calls = [c for c in darwin_platform.launchctl_calls if "bootstrap" in c]
    assert len(bootstrap_calls) == 1, (
        "must call `launchctl bootstrap gui/<uid> <plist>` exactly once after "
        f"writing the plist (got {len(bootstrap_calls)} bootstrap calls: {bootstrap_calls})"
    )
    assert bootstrap_calls[0][0] == "launchctl"
    assert bootstrap_calls[0][1] == "bootstrap"
    assert bootstrap_calls[0][2].startswith("gui/"), "launchctl bootstrap must target the per-user gui/<uid> domain"
    assert str(plist_path) in bootstrap_calls[0], (
        "launchctl bootstrap must reference the plist path that was just written"
    )
    load_calls = [c for c in darwin_platform.launchctl_calls if "load" in c]
    assert load_calls == [], (
        f"legacy `launchctl load` fallback must NOT fire when bootstrap succeeds (got: {load_calls})"
    )


def test_plist_uses_run_at_load_true(darwin_platform):
    """The plist must set ``RunAtLoad=true`` so launchd starts the job at"""
    sp = darwin_platform.server_platform
    sp._enable_autostart_macos()

    data = _parse_plist(darwin_platform.plist_path)
    assert data.get("Label") == "com.voicetyper", (
        "Label must be 'com.voicetyper' (matches the plist filename + the "
        "label passed to `launchctl bootout` in _disable_autostart_macos)"
    )
    assert data.get("RunAtLoad") is True, (
        "RunAtLoad must be true so launchd fires the job at login (NOT a "
        "Calendar/Event/Boot trigger, those would fire at the wrong time "
        "or require root)"
    )
    assert data.get("KeepAlive") is False, (
        "KeepAlive must be false, the launcher is one-shot at login, NOT a "
        "long-running daemon that launchd should restart on exit"
    )


def test_plist_program_arguments_current_behavior_python_launcher(darwin_platform):
    """CURRENT BEHAVIOR (GAP-1): the plist's ``ProgramArguments`` is"""
    sp = darwin_platform.server_platform
    sp._enable_autostart_macos()

    data = _parse_plist(darwin_platform.plist_path)
    args = data.get("ProgramArguments")
    assert isinstance(args, list), "ProgramArguments must be an array"
    assert len(args) >= 2, (
        f"current impl writes [sys.executable, launcher_path], at least 2 elements expected (got {len(args)}: {args})"
    )
    # First arg is the Python interpreter (sys.executable at write time).
    assert "python" in args[0].lower(), (
        f"current impl's first ProgramArgument must be the Python interpreter "
        f"(sys.executable); got {args[0]!r}. NOTE: this is GAP-1, the MIG-1.6 "
        f"expectation is for the first arg to be the Tauri host binary "
        f"({_TAURI_HOST_BIN_NAME!r}), not Python. See the companion xfail test."
    )
    # Second arg is the autostart_launcher.py script.
    assert args[1].endswith("autostart_launcher.py"), (
        f"current impl's second ProgramArgument must be autostart_launcher.py; "
        f"got {args[1]!r}. NOTE: GAP-1, MIG-1.6 expects a single-element "
        f"ProgramArguments pointing at the Tauri host binary."
    )


# XFAIL strict, flips to XPASS-strict-fail when  is fixed, prompting


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MIG-1.6 GAP-1: _enable_autostart_macos() currently writes a plist "
        "whose ProgramArguments points to sys.executable (Python) + "
        "autostart_launcher.py, the LEGACY predecessor path. Phase 0-M "
        "sign-off requires the plist to point at the Tauri host binary "
        "(/Applications/Voice Typer.app/Contents/MacOS/voice-typer-tauri) "
        "so a user who installs the DMG auto-launches the *bundled* Tauri "
        "app at login (not a stray Python interpreter that may not exist "
        "on a clean install). When _enable_autostart_macos is updated to "
        "emit the Tauri host path, this test will XPASS, remove the "
        "xfail marker at that time."
    ),
)
def test_plist_program_arguments_point_to_tauri_host_binary(darwin_platform):
    """EXPECTED (per validation runbook §6 step 5): the plist's"""
    sp = darwin_platform.server_platform
    sp._enable_autostart_macos()

    data = _parse_plist(darwin_platform.plist_path)
    args = data.get("ProgramArguments")
    assert isinstance(args, list), "ProgramArguments must be an array"

    args_blob = " ".join(args)
    assert _TAURI_HOST_BIN_NAME in args_blob, (
        f"ProgramArguments must reference the Tauri host binary "
        f"({_TAURI_HOST_BIN_NAME!r}, per Cargo.toml [[bin]] name); "
        f"got args={args!r}"
    )
    # Must NOT point at the Python interpreter (the legacy path).
    assert not any("python" in a.lower() for a in args), (
        f"ProgramArguments must NOT reference the Python interpreter (the "
        f"legacy predecessor launch path); got args={args!r}. A clean macOS "
        f"install with only the DMG may not have a compatible Python."
    )
    # Must NOT reference autostart_launcher.py.
    assert not any("autostart_launcher.py" in a for a in args), (
        f"ProgramArguments must NOT reference autostart_launcher.py; got args={args!r}"
    )


# ─── Test 5: _disable_autostart_macos removes plist + unloads via bootout ─


def test_disable_autostart_macos_removes_plist_and_unloads(darwin_platform):
    """``_disable_autostart_macos()`` unloads the running job via"""
    sp = darwin_platform.server_platform
    plist_path = darwin_platform.plist_path

    # Setup: enable first so the plist exists.
    sp._enable_autostart_macos()
    assert plist_path.exists()
    darwin_platform.launchctl_calls.clear()  # reset capture

    # Disable.
    assert sp._disable_autostart_macos() is True

    # Plist must be deleted.
    assert not plist_path.exists(), (
        "disable must delete the plist file (otherwise launchd re-loads it "
        "on next login even though the user disabled autostart)"
    )

    bootout_calls = [c for c in darwin_platform.launchctl_calls if "bootout" in c]
    assert len(bootout_calls) == 1, (
        f"must call `launchctl bootout gui/<uid>/com.voicetyper` exactly once (modern API); got {bootout_calls}"
    )
    assert bootout_calls[0][0] == "launchctl"
    assert bootout_calls[0][1] == "bootout"
    target = bootout_calls[0][2]
    assert target.startswith("gui/") and target.endswith("/com.voicetyper"), (
        f"bootout target must be 'gui/<uid>/com.voicetyper'; got {target!r}"
    )

    remove_calls = [c for c in darwin_platform.launchctl_calls if "remove" in c]
    assert len(remove_calls) == 1, (
        f"must call `launchctl remove com.voicetyper` as a legacy fallback (for macOS < 10.10); got {remove_calls}"
    )
    assert remove_calls[0] == ["launchctl", "remove", "com.voicetyper"], (
        f"launchctl remove must use the label 'com.voicetyper'; got {remove_calls[0]}"
    )


# ─── Test 6: _is_autostart_enabled_macos returns True only if plist exists


def test_is_autostart_enabled_macos_returns_true_only_if_plist_exists(darwin_platform):
    """``_is_autostart_macos()`` returns True iff the plist file exists at"""
    sp = darwin_platform.server_platform
    plist_path = darwin_platform.plist_path

    # 1. No plist → False.
    assert not plist_path.exists()
    assert sp._is_autostart_macos() is False, "is_autostart_enabled must return False when the plist doesn't exist"

    # 2. Enable → plist exists → True.
    sp._enable_autostart_macos()
    assert plist_path.exists()
    assert sp._is_autostart_macos() is True, "is_autostart_enabled must return True when the plist exists"

    # 3. Disable → plist deleted → False.
    sp._disable_autostart_macos()
    assert not plist_path.exists()
    assert sp._is_autostart_macos() is False, "is_autostart_enabled must return False after disable removes the plist"

    # 4. Manually touch the plist (without launchctl load) → still True
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict><key>Label</key><string>com.voicetyper</string></dict></plist>'
    )
    assert sp._is_autostart_macos() is True, (
        "is_autostart_enabled must return True if the plist file exists, "
        "even if launchctl hasn't loaded it (file-existence probe, NOT a "
        "launchd state query)"
    )


def test_is_autostart_macos_false_when_plist_program_path_missing(darwin_platform):
    """A plist whose ``ProgramArguments`` point at a deleted interpreter /"""
    sp = darwin_platform.server_platform
    plist_path = darwin_platform.plist_path
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<plist version="1.0"><dict>'
        "<key>Label</key><string>com.voicetyper</string>"
        "<key>ProgramArguments</key><array>"
        "<string>/nonexistent/venv/bin/python</string>"
        "<string>/nonexistent/autostart_launcher.py</string>"
        "</array></dict></plist>",
        encoding="utf-8",
    )
    assert sp._is_autostart_macos() is False, "plist pointing at a deleted interpreter must report autostart disabled"


def test_is_autostart_macos_true_when_plist_program_paths_exist(darwin_platform):
    """A plist whose ``ProgramArguments`` point at real paths must report"""
    sp = darwin_platform.server_platform
    plist_path = darwin_platform.plist_path
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    import sys

    plist_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<plist version="1.0"><dict>'
        "<key>Label</key><string>com.voicetyper</string>"
        "<key>ProgramArguments</key><array>"
        f"<string>{sys.executable}</string>"
        # /bin/true is a Linux-ism, macOS has no /bin/true (its true(1)
        "<string>/bin/ls</string>"
        "</array></dict></plist>",
        encoding="utf-8",
    )
    assert sp._is_autostart_macos() is True, "plist pointing at existing paths must report autostart enabled"


def test_tauri_conf_has_macos_bundle_or_dmg_app_defaults():
    """explicit ``bundle.macOS`` block exists (with ``signingIdentity`` /"""
    assert TAURI_CONF.exists(), f"tauri.conf.json missing at {TAURI_CONF}"
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))

    assert "bundle" in conf, "must have a top-level bundle block"
    bundle = conf["bundle"]
    assert bundle.get("active") is True, "bundle.active must be true"

    # Either explicit bundle.macOS OR targets includes dmg/app.
    has_macos = "macOS" in bundle
    targets = bundle.get("targets")
    # Tauri v2's "all" targets produces ["app", "dmg"] on macOS by default.
    targets_include_dmg_app = targets == "all" or (isinstance(targets, list) and ("dmg" in targets or "app" in targets))
    assert has_macos or targets_include_dmg_app, (
        "bundle.macOS must exist OR bundle.targets must include dmg/app "
        f"(got targets={targets!r}, has_macOS={has_macos}). Without this, "
        f"the macOS build won't produce a DMG installer."
    )

    # The .app bundle's main binary name is voice-typer-tauri (per Cargo.toml).
    assert CARGO_TOML.exists(), f"Cargo.toml missing at {CARGO_TOML}"
    cargo = CARGO_TOML.read_text(encoding="utf-8")
    assert re.search(r'name\s*=\s*"voice-typer-tauri"', cargo), (
        "Cargo.toml [[bin]] name must be 'voice-typer-tauri' (the .app's "
        "Contents/MacOS/ executable name, referenced by GAP-1's fix)"
    )


def test_entitlements_plist_has_three_required_entitlements():
    """``src-tauri/entitlements.plist`` declares the hardened-runtime"""
    assert ENTITLEMENTS_PLIST.exists(), f"entitlements.plist missing at {ENTITLEMENTS_PLIST}"
    with ENTITLEMENTS_PLIST.open("rb") as fh:
        data = plistlib.load(fh)

    assert data.get("com.apple.security.cs.allow-jit") is True, (
        "com.apple.security.cs.allow-jit must be true (CTranslate2 JIT)"
    )
    assert data.get("com.apple.security.cs.disable-library-validation") is True, (
        "com.apple.security.cs.disable-library-validation must be true (Nuitka onefile unsigned dylib extraction)"
    )
    assert data.get("com.apple.security.device.audio-input") is True, (
        "com.apple.security.device.audio-input must be true (microphone "
        "access via sounddevice, required by TCC on macOS 11+)"
    )
    assert data.get("com.apple.security.cs.allow-unsigned-executable-memory") is True, (
        "com.apple.security.cs.allow-unsigned-executable-memory must be true (Nuitka onefile RWX pages, BUILD-N02)"
    )
    assert data.get("com.apple.security.automation.apple-events") is True, (
        "com.apple.security.automation.apple-events must be true (AppleScript "
        "volume ducking + clipboard foreground detection, BUILD-N02)"
    )

    # Sanity: no entitlements beyond the 5 documented ones (guard against
    expected_keys = {
        "com.apple.security.cs.allow-jit",
        "com.apple.security.cs.allow-unsigned-executable-memory",
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.device.audio-input",
        "com.apple.security.automation.apple-events",
    }
    actual_keys = set(data.keys())
    extra = actual_keys - expected_keys
    missing = expected_keys - actual_keys
    assert not extra, (
        f"entitlements.plist has EXTRA entitlements beyond the 5 documented: "
        f"{extra}. Each entitlement broadens the hardened-runtime attack "
        f"surface, remove any that aren't strictly required."
    )
    assert not missing, f"entitlements.plist is MISSING documented entitlements: {missing}"


def test_installer_includes_sidecar_prewarm_native_listener_resources():
    """standalone prewarm binary was retired, plan-runtime-pack-split"""
    assert TAURI_CONF.exists(), f"tauri.conf.json missing at {TAURI_CONF}"
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    bundle = conf["bundle"]

    # 1. Sidecar: externalBin (Tauri appends the target triple at runtime
    external_bin = bundle.get("externalBin", [])
    assert "bin/python-sidecar" in external_bin, (
        "externalBin must include bin/python-sidecar (Tauri appends the "
        "target triple to find bin/python-sidecar-{x86_64,aarch64}-apple-darwin)"
    )
    assert "bin/voice-typer-worker" in external_bin, (
        "externalBin must include bin/voice-typer-worker (the ML worker "
        "exe owns the warm phase since the prewarm binary was retired, "
        "plan-runtime-pack-split §6.2)"
    )

    # 2. Native listener + retired-prewarm guard: resources.
    resources = bundle.get("resources", [])
    resources_blob = "\n".join(resources)

    # §6.2), their resource entries must NOT come back.
    stale_prewarm = [r for r in resources if "prewarm" in r]
    assert not stale_prewarm, (
        f"resources must NOT include prewarm binaries (retired per plan-runtime-pack-split §6.2): {stale_prewarm}"
    )

    # Native macOS key-listener (ADR-0020 §6.4, compiled CGEventTap hook
    assert "native/macos-key-listener" in resources_blob, (
        "resources must include native/macos-key-listener (ADR-0020 §6.4, "
        "compiled CGEventTap hook for the dictation toggle hotkey)"
    )


def test_single_instance_plugin_enforced():
    """Single-instance is enforced via ``tauri-plugin-single-instance``"""
    # 1. tauri.conf.json declares the plugin.
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    plugins = conf.get("plugins", {})
    assert "single-instance" in plugins, "plugins.single-instance must be declared in tauri.conf.json"

    # 2. Cargo.toml has the dependency.
    assert CARGO_TOML.exists(), f"Cargo.toml missing at {CARGO_TOML}"
    cargo = CARGO_TOML.read_text(encoding="utf-8")
    assert "tauri-plugin-single-instance" in cargo, "Cargo.toml must depend on tauri-plugin-single-instance"

    # 3. main.rs registers the plugin FIRST + focuses the main window.
    assert MAIN_RS.exists(), f"main.rs missing at {MAIN_RS}"
    main_rs = MAIN_RS.read_text(encoding="utf-8")
    assert "tauri_plugin_single_instance::init" in main_rs, (
        "main.rs must call tauri_plugin_single_instance::init() to register the single-instance plugin"
    )
    first_plugin_idx = main_rs.find(".plugin(")
    single_instance_idx = main_rs.find(".plugin(tauri_plugin_single_instance::init")
    assert first_plugin_idx != -1, "no .plugin() calls found in main.rs"
    assert single_instance_idx != -1, "single-instance plugin registration not found"
    assert single_instance_idx == first_plugin_idx, (
        "single-instance must be the FIRST .plugin() call in the Builder "
        "chain (ADR-0020 §12 ordering) so a second launch doesn't spawn a "
        "zombie sidecar before the duplicate check runs"
    )
    # The sidecar spawn happens inside the .setup() hook (which calls
    setup_idx = main_rs.find(".setup(")
    if setup_idx != -1:
        assert single_instance_idx < setup_idx, (
            "single-instance plugin must be registered BEFORE the .setup() "
            "hook (where the sidecar is spawned), ADR-0020 §12 ordering"
        )

    # 4. The callback focuses the existing main window (show + set_focus).
    assert "get_webview_window" in main_rs and "set_focus" in main_rs, (
        "single-instance callback must show + focus the existing main "
        "window (second launch → focus first, no duplicate window)"
    )
    # The callback should reference the "main" window label (the dashboard).
    init_block_end = main_rs.find("}))", single_instance_idx)
    if init_block_end != -1:
        init_block = main_rs[single_instance_idx:init_block_end]
        assert '"main"' in init_block or "'main'" in init_block, (
            "single-instance callback must target the 'main' window label"
        )


def test_ci_workflow_runs_codesign_notarytool_stapler():
    """
    ``.github/workflows/tauri-macos-build.yml`` runs the full code
    Source-inspection of the YAML (no mocking). The workflow jobs are
    """
    assert CI_WORKFLOW.exists(), f"CI workflow missing at {CI_WORKFLOW}"
    yaml_text = CI_WORKFLOW.read_text(encoding="utf-8")

    # 1. codesign, signs the .dmg with the Developer ID identity.
    assert "codesign" in yaml_text, (
        "CI workflow must invoke `codesign` to sign the .dmg (and/or .app) with the Developer ID identity"
    )
    assert "MAC_SIGNING_IDENTITY" in yaml_text, (
        "CI workflow must reference $MAC_SIGNING_IDENTITY (the Developer ID "
        "Application certificate Common Name, passed to `codesign --sign`)"
    )

    # 2. notarytool, submits the .app + .dmg to Apple's notarization service.
    assert "xcrun notarytool submit" in yaml_text, (
        "CI workflow must invoke `xcrun notarytool submit` to submit the .app + .dmg to Apple's notarization service"
    )
    assert "--apple-id" in yaml_text, (
        "notarytool submit must pass --apple-id (the Apple ID of the Developer Program member)"
    )
    assert "--team-id" in yaml_text, "notarytool submit must pass --team-id (the Developer Program team ID)"
    assert "--wait" in yaml_text, (
        "notarytool submit must pass --wait so the workflow blocks until "
        "Apple's notarization service returns a verdict (otherwise the "
        "staple step would run before the ticket is available)"
    )

    # 3. stapler, staples the notarization ticket to the .app + .dmg
    assert "xcrun stapler staple" in yaml_text, (
        "CI workflow must invoke `xcrun stapler staple` for both the .app "
        "and the .dmg (so the notarization ticket is embedded, required "
        "for offline Gatekeeper validation)"
    )
    assert "xcrun stapler validate" in yaml_text, (
        "CI workflow must invoke `xcrun stapler validate` to verify the "
        "stapled ticket (the runbook §7.5 pass criterion is 'The validate "
        "action worked!')"
    )

    # 4. The notarize step must be conditional on secrets being present
    assert "MAC_SIGNING_IDENTITY != ''" in yaml_text or ("env.MAC_SIGNING_IDENTITY" in yaml_text), (
        "the notarize step must be guarded on $MAC_SIGNING_IDENTITY being "
        "non-empty (so fork PRs without access to secrets don't fail)"
    )

    # 5. Sanity: the workflow builds BOTH arches (universal .app + .dmg).
    assert "universal-apple-darwin" in yaml_text, (
        "CI workflow must build a universal .app + .dmg (--target universal-apple-darwin, combines x86_64 + aarch64)"
    )
    assert "aarch64-apple-darwin" in yaml_text, "CI workflow must build the aarch64 (Apple Silicon) sidecar + prewarm"
    assert "x86_64-apple-darwin" in yaml_text, (
        "CI workflow must build the x86_64 (Intel, via Rosetta 2) sidecar + prewarm"
    )

    # 6. The workflow jobs are enabled (`if: true`, GAP-3 closed, ):
    assert "if: false" not in yaml_text, (
        "GAP-3: the workflow must have NO `if: false` guards left, all 3 "
        "jobs (build-aarch64, build-x86_64, build-tauri-universal) are "
        "ENABLED (`if: true`) so the signing + notarization path can be "
        "exercised via workflow_dispatch (GATE STATUS header: 'ENABLED'). "
        "Phase 0-M host validation per docs/migration/macos-validation-runbook.md "
        "is still required before CI push/PR triggers are uncommented."
    )
    assert "if: true" in yaml_text, (
        "GAP-3: the workflow jobs must be ENABLED (`if: true`) for the "
        "Phase 0-M validation dispatch (see the GATE STATUS header)."
    )
    assert "GATE STATUS" in yaml_text, (
        "the workflow must carry a GATE STATUS header block documenting the Phase 0-M validation handoff."
    )
    assert "Phase 0-M" in yaml_text, "the workflow must reference Phase 0-M (the macOS Phase 0 spike gate)."
