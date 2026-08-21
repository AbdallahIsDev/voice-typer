"""MIG-1.6 Phase 0-M Gate Check 10 (+1): macOS autostart + installer validation.

Tests the macOS autostart mechanism (LaunchAgent ``com.voicetyper.plist``)
and the Tauri DMG + .app installer configuration + code signing /
notarization / stapling wiring. The autostart logic lives in
``voice_typer/server/server_platform.py`` (cross-platform facade +
``_enable_autostart_macos`` / ``_disable_autostart_macos`` /
``_is_autostart_macos``). The installer config is in
``src-tauri/tauri.conf.json`` + ``src-tauri/entitlements.plist`` + the
``.github/workflows/tauri-macos-build.yml`` CI workflow.

AUTOSTART ARCHITECTURE (actual implementation)
----------------------------------------------
``server_platform._enable_autostart_macos`` writes a LaunchAgent plist
at ``~/Library/LaunchAgents/com.voicetyper.plist`` with:

  - ``Label`` = ``com.voicetyper``
  - ``ProgramArguments`` = ``[sys.executable, <repo>/voice_typer/server/autostart_launcher.py]``
    (CURRENT — the legacy Python/Electron launcher path; see GAP-1 below)
  - ``RunAtLoad`` = ``true``  (fires at login — NOT a Calendar/Event trigger)
  - ``KeepAlive`` = ``false`` (one-shot at login, not a daemon)
  - ``WorkingDirectory`` = ``$HOME`` (absolute, NOT ``~`` — NEW-XPLAT-006)
  - ``StandardOutPath`` / ``StandardErrorPath`` = ``<config_dir>/autostart.log``

After writing the plist, it registers the job via the modern
``launchctl bootstrap gui/<uid> <plist>`` (5s timeout), falling back to
the deprecated ``launchctl load <plist>`` only when bootstrap fails.
``_disable_autostart_macos`` runs
``launchctl bootout gui/<uid>/com.voicetyper`` (modern, macOS 10.10+)
then falls back to ``launchctl remove com.voicetyper`` (legacy), then
deletes the plist file. ``_is_autostart_macos`` probes the plist path
AND validates its ProgramArguments exist on disk (AUTOSTART-CMD-VALIDATE).

KNOWN GAPS (report, do not fix)
-------------------------------
GAP-1 (ProgramArguments): The current plist's ``ProgramArguments`` points
to ``sys.executable`` (Python) + ``autostart_launcher.py`` — the legacy
Electron/Python launch path. Phase 0-M sign-off (per the validation
runbook §6 + this test's "VALIDATE ON MACOS HOST" step 5) requires the
plist to point at the Tauri host binary
``/Applications/Voice Typer.app/Contents/MacOS/voice-typer-tauri`` so
that a user who installs the DMG auto-launches the *bundled* Tauri app
at login (not a stray Python interpreter that may not exist on a clean
install). See ``test_plist_program_arguments_point_to_tauri_host_binary``
(xfail strict — flips to XPASS-strict-fail when the impl is updated,
prompting marker removal).

GAP-2 (bundle.macOS): ``tauri.conf.json`` has no explicit
``bundle.macOS`` block. It relies on Tauri v2's default ``"all"`` targets
which produce DMG + .app on macOS. This is acceptable for Phase 0-M
(the test ``test_tauri_conf_has_macos_bundle_or_dmg_app_defaults``
verifies the default), but for production distribution an explicit
``bundle.macOS.signingIdentity`` (or env-driven equivalent) + the
``entitlements.plist`` reference should be wired in so the bundler
auto-signs the .app. Currently signing is performed by the CI workflow
post-build step (``codesign --force --deep ...``), not by the Tauri
bundler itself.

GAP-3 (CLOSED — workflow enabled): ``.github/workflows/tauri-macos-build.yml``
jobs are now ENABLED (``if: true``) so the Phase 0-M validation run can
execute via ``workflow_dispatch``. The workflow YAML is structurally
correct (source inspection passes). Cutover to CI-driven runs still
requires the Phase 0-M manual validation runbook
(``docs/migration/macos-validation-runbook.md``) to pass on a real macOS
host (ADR-0020 §15 + cutover-playbook.md Step 2.1).

VALIDATE ON MACOS HOST:
1. Build the installer: cd src-tauri; cargo tauri build --target x86_64-apple-darwin  (OR aarch64-apple-darwin)
2. Open target/<arch>/release/bundle/dmg/*.dmg → drag to Applications
3. Verify "Voice Typer" appears in Applications folder
4. Launch Voice Typer → enable autostart via Settings
5. Examine ~/Library/LaunchAgents/com.voicetyper.plist
   Expected: RunAtLoad=true; ProgramArguments=/Applications/Voice Typer.app/Contents/MacOS/voice-typer-tauri
6. Run: launchctl list | grep voicetyper
   Expected: com.voicetyper entry
7. Log out + log back in → verify Voice Typer auto-launches
8. Launch a second instance → verify it focuses the first (single-instance plugin)
9. Code signing: codesign -dv --verbose=4 "/Applications/Voice Typer.app"
   Expected: Authority=Developer ID Application: <name>; TeamIdentifier=<team>
10. Notarization: xcrun stapler validate "/Applications/Voice Typer.app"
    Expected: "The validate action worked!"
11. Uninstall by dragging to Trash → verify LaunchAgent plist is removed (or orphaned — document)
Expected: autostart works; single-instance works; signing + notarization valid

TEST-HOST NOTES
---------------
On the Linux test host, ``sys.platform != "darwin"`` and ``launchctl``
is unavailable, so the macOS-only code paths are exercised by
monkeypatching ``sys.platform`` + ``server_platform.SYSTEM`` to
``"darwin"``, redirecting ``Path.home()`` + ``VOICE_TYPER_CONFIG_DIR``
to a tmp dir, and mocking ``subprocess.run`` to capture ``launchctl``
invocations. The Tauri config / entitlements.plist / Cargo.toml /
main.rs / CI workflow source-inspection tests read the real files (no
mocking).
"""

from __future__ import annotations

import json
import plistlib
import re
import sys
import urllib.request  # noqa: F401  (side-effect: cache in sys.modules)

# PRE-IMPORT: ``_enable_autostart_macos`` calls
# ``from xml.sax.saxutils import escape`` at function-call time, which
# transitively imports ``urllib.request``. On the Linux test host,
# ``urllib.request`` only imports the macOS-only ``_scproxy`` extension
# when ``sys.platform == "darwin"``. The ``darwin_platform`` fixture below
# monkeypatches ``sys.platform`` to ``"darwin"``, so if ``urllib.request``
# is imported AFTER that monkeypatch, Python tries to load ``_scproxy``
# (which doesn't exist on Linux) and raises ``ModuleNotFoundError``.
# Pre-importing ``xml.sax.saxutils`` (and its transitive deps) here at
# module-load time — BEFORE any monkeypatch — caches them in
# ``sys.modules`` so the subsequent ``from xml.sax.saxutils import escape``
# inside ``_enable_autostart_macos`` is a no-op sys.modules lookup.
import xml.sax.saxutils  # noqa: F401  (side-effect: cache in sys.modules)
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ─── Paths to real source files (source-inspection, NOT mocked) ──────────

# tests/tauri/mig16/test_autostart_installer_macos.py → repo root in 4 parents:
#   parents[0]=mig16, parents[1]=tauri, parents[2]=tests, parents[3]=voice-typer.
_REPO_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = _REPO_ROOT / "src-tauri" / "tauri.conf.json"
ENTITLEMENTS_PLIST = _REPO_ROOT / "src-tauri" / "entitlements.plist"
SRC_TAURI_DIR = TAURI_CONF.parent
CARGO_TOML = SRC_TAURI_DIR / "Cargo.toml"
MAIN_RS = SRC_TAURI_DIR / "src" / "main.rs"
CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "tauri-macos-build.yml"

# The Tauri host binary name (per src-tauri/Cargo.toml [[bin]] name=...).
_TAURI_HOST_BIN_NAME = "voice-typer-tauri"


# ─── fixture: darwin platform + tmp home + mocked launchctl ──────────────


@pytest.fixture
def darwin_platform(monkeypatch, tmp_path):
    """Pretend we're on macOS for the duration of the test.

    Patches:
      - ``sys.platform`` → "darwin"
      - ``voice_typer.server.server_platform.SYSTEM`` → "darwin" (module-level
        constant read at function-call time by enable/disable/is_enabled)
      - ``Path.home()`` → ``tmp_path / "home"`` (so the LaunchAgent plist
        is written under the tmp dir, NOT the test host's real $HOME)
      - ``$VOICE_TYPER_CONFIG_DIR`` → ``tmp_path / "config" / "voice-typer"``
        (so ``_paths.config_dir()`` / ``autostart_log()`` resolve to tmp)
      - ``subprocess.run`` → captures ``launchctl`` invocations into a list
        (so the test doesn't actually run ``launchctl`` on Linux)

    Returns a SimpleNamespace with:
      - ``server_platform``: the imported module
      - ``home``: tmp home dir
      - ``plist_path``: ``<home>/Library/LaunchAgents/com.voicetyper.plist``
      - ``launchctl_calls``: list of argv lists captured from subprocess.run
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    from voice_typer.server import server_platform

    monkeypatch.setattr(server_platform, "SYSTEM", "darwin")

    # Redirect Path.home() to a tmp dir so the plist is written there.
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    # NB: server_platform.Path IS pathlib.Path (imported at module top), so
    # monkeypatching the classmethod on it affects all Path.home() callers
    # for the duration of the test (restored by monkeypatch teardown).
    monkeypatch.setattr(server_platform.Path, "home", lambda: home)

    # Redirect _paths.config_dir() to tmp via the env override.
    config_dir = tmp_path / "config" / "voice-typer"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(config_dir))

    # Mock subprocess.run (imported locally inside _enable/_disable_autostart_macos)
    # so launchctl isn't actually invoked on the Linux test host.
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


# ─── Test 1: _enable_autostart_macos creates the LaunchAgent plist ───────


def test_enable_autostart_macos_creates_launchagent_plist(darwin_platform):
    """``_enable_autostart_macos()`` writes a LaunchAgent plist at
    ``~/Library/LaunchAgents/com.voicetyper.plist`` (the canonical per-user
    launchd location, NOT ``/Library/LaunchDaemons`` which would require
    root). After writing, it registers the job with launchd via the modern
    ``launchctl bootstrap gui/<uid> <plist>`` verb (macOS 10.10+), falling
    back to the deprecated ``launchctl load <plist>`` only when bootstrap
    fails.
    """
    sp = darwin_platform.server_platform
    plist_path = darwin_platform.plist_path

    assert not plist_path.exists(), "precondition: plist should not exist yet"
    assert sp._enable_autostart_macos() is True

    # The plist must exist at the canonical per-user LaunchAgents path.
    assert plist_path.exists(), (
        f"plist must be written to {plist_path} (the per-user LaunchAgents "
        f"dir; NOT /Library/LaunchDaemons which needs root)"
    )
    # Permissions: 0o600 (owner read/write only — contains no secrets, but
    # plistlib doesn't preserve group/other bits in a useful way for launchd).
    assert (plist_path.stat().st_mode & 0o777) == 0o600, "plist must be chmod 0o600 (per-user, no group/other access)"

    # launchctl bootstrap must have been invoked with the gui domain and
    # the plist path that was just written (the mocked subprocess.run
    # returns rc=0 + empty stderr, so the legacy `load` fallback must
    # NOT fire).
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


# ─── Test 2: plist uses RunAtLoad=true (runs at login) ───────────────────


def test_plist_uses_run_at_load_true(darwin_platform):
    """The plist must set ``RunAtLoad=true`` so launchd starts the job at
    login (NOT a Calendar/Event/Boot trigger — those would fire at the
    wrong time or require root). ``KeepAlive=false`` ensures the job is
    one-shot (launchd doesn't restart it if the launcher exits).
    """
    sp = darwin_platform.server_platform
    sp._enable_autostart_macos()

    data = _parse_plist(darwin_platform.plist_path)
    assert data.get("Label") == "com.voicetyper", (
        "Label must be 'com.voicetyper' (matches the plist filename + the "
        "label passed to `launchctl bootout` in _disable_autostart_macos)"
    )
    assert data.get("RunAtLoad") is True, (
        "RunAtLoad must be true so launchd fires the job at login (NOT a "
        "Calendar/Event/Boot trigger — those would fire at the wrong time "
        "or require root)"
    )
    assert data.get("KeepAlive") is False, (
        "KeepAlive must be false — the launcher is one-shot at login, NOT a "
        "long-running daemon that launchd should restart on exit"
    )


# ─── Test 3: ProgramArguments CURRENT behavior (Python + launcher) ───────
# Documents  — see module docstring.


def test_plist_program_arguments_current_behavior_python_launcher(darwin_platform):
    """CURRENT BEHAVIOR (GAP-1): the plist's ``ProgramArguments`` is
    ``[sys.executable, <repo>/voice_typer/server/autostart_launcher.py]``
    — the legacy Electron/Python launch path. This works for the dev
    Electron app, but is NOT the production Tauri host launch path.

    This test PASSES on the current implementation and documents the gap.
    The companion test
    ``test_plist_program_arguments_point_to_tauri_host_binary`` (xfail
    strict) asserts the MIG-1.6 expected behavior.
    """
    sp = darwin_platform.server_platform
    sp._enable_autostart_macos()

    data = _parse_plist(darwin_platform.plist_path)
    args = data.get("ProgramArguments")
    assert isinstance(args, list), "ProgramArguments must be an array"
    assert len(args) >= 2, (
        f"current impl writes [sys.executable, launcher_path] — at least 2 elements expected (got {len(args)}: {args})"
    )
    # First arg is the Python interpreter (sys.executable at write time).
    assert "python" in args[0].lower(), (
        f"current impl's first ProgramArgument must be the Python interpreter "
        f"(sys.executable); got {args[0]!r}. NOTE: this is GAP-1 — the MIG-1.6 "
        f"expectation is for the first arg to be the Tauri host binary "
        f"({_TAURI_HOST_BIN_NAME!r}), not Python. See the companion xfail test."
    )
    # Second arg is the autostart_launcher.py script.
    assert args[1].endswith("autostart_launcher.py"), (
        f"current impl's second ProgramArgument must be autostart_launcher.py; "
        f"got {args[1]!r}. NOTE: GAP-1 — MIG-1.6 expects a single-element "
        f"ProgramArguments pointing at the Tauri host binary."
    )


# Test 4: ProgramArguments  EXPECTED behavior (Tauri host) ────
# XFAIL strict — flips to XPASS-strict-fail when  is fixed, prompting
# removal of this marker.


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MIG-1.6 GAP-1: _enable_autostart_macos() currently writes a plist "
        "whose ProgramArguments points to sys.executable (Python) + "
        "autostart_launcher.py — the LEGACY Electron path. Phase 0-M "
        "sign-off requires the plist to point at the Tauri host binary "
        "(/Applications/Voice Typer.app/Contents/MacOS/voice-typer-tauri) "
        "so a user who installs the DMG auto-launches the *bundled* Tauri "
        "app at login (not a stray Python interpreter that may not exist "
        "on a clean install). When _enable_autostart_macos is updated to "
        "emit the Tauri host path, this test will XPASS — remove the "
        "xfail marker at that time."
    ),
)
def test_plist_program_arguments_point_to_tauri_host_binary(darwin_platform):
    """MIG-1.6 EXPECTED (per validation runbook §6 step 5): the plist's
    ``ProgramArguments`` must point to the Tauri host binary
    (``/Applications/Voice Typer.app/Contents/MacOS/voice-typer-tauri``)
    so the user's login launches the *bundled* Tauri app — NOT a stray
    Python interpreter (which may not exist on a clean install that only
    has the DMG).

    Currently XFAIL because the impl still emits the legacy Python +
    autostart_launcher.py path (GAP-1). See the companion test
    ``test_plist_program_arguments_current_behavior_python_launcher``
    which documents the current behavior.
    """
    sp = darwin_platform.server_platform
    sp._enable_autostart_macos()

    data = _parse_plist(darwin_platform.plist_path)
    args = data.get("ProgramArguments")
    assert isinstance(args, list), "ProgramArguments must be an array"

    # Join all args into a single string for substring checks (the
    # expected form is a single-element list pointing at the .app's
    # MacOS executable, but be lenient about extra args like --hidden).
    args_blob = " ".join(args)
    assert _TAURI_HOST_BIN_NAME in args_blob, (
        f"ProgramArguments must reference the Tauri host binary "
        f"({_TAURI_HOST_BIN_NAME!r}, per Cargo.toml [[bin]] name); "
        f"got args={args!r}"
    )
    # Must NOT point at the Python interpreter (the legacy path).
    assert not any("python" in a.lower() for a in args), (
        f"ProgramArguments must NOT reference the Python interpreter (the "
        f"legacy Electron launch path); got args={args!r}. A clean macOS "
        f"install with only the DMG may not have a compatible Python."
    )
    # Must NOT reference autostart_launcher.py (the legacy launcher).
    assert not any("autostart_launcher.py" in a for a in args), (
        f"ProgramArguments must NOT reference autostart_launcher.py (the legacy Electron launcher); got args={args!r}"
    )


# ─── Test 5: _disable_autostart_macos removes plist + unloads via bootout ─


def test_disable_autostart_macos_removes_plist_and_unloads(darwin_platform):
    """``_disable_autostart_macos()`` unloads the running job via
    ``launchctl bootout gui/<uid>/com.voicetyper`` (modern, macOS 10.10+)
    with a fallback to ``launchctl remove com.voicetyper`` (legacy), then
    deletes the plist file. Unloading BEFORE deleting ensures the job
    doesn't keep running until next logout.
    """
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

    # launchctl bootout must have been called (modern API, macOS 10.10+).
    bootout_calls = [c for c in darwin_platform.launchctl_calls if "bootout" in c]
    assert len(bootout_calls) == 1, (
        f"must call `launchctl bootout gui/<uid>/com.voicetyper` exactly once (modern API); got {bootout_calls}"
    )
    assert bootout_calls[0][0] == "launchctl"
    assert bootout_calls[0][1] == "bootout"
    # bootout target format: gui/<uid>/<label>
    target = bootout_calls[0][2]
    assert target.startswith("gui/") and target.endswith("/com.voicetyper"), (
        f"bootout target must be 'gui/<uid>/com.voicetyper'; got {target!r}"
    )

    # launchctl remove must have been called as a legacy fallback.
    remove_calls = [c for c in darwin_platform.launchctl_calls if "remove" in c]
    assert len(remove_calls) == 1, (
        f"must call `launchctl remove com.voicetyper` as a legacy fallback (for macOS < 10.10); got {remove_calls}"
    )
    assert remove_calls[0] == ["launchctl", "remove", "com.voicetyper"], (
        f"launchctl remove must use the label 'com.voicetyper'; got {remove_calls[0]}"
    )


# ─── Test 6: _is_autostart_enabled_macos returns True only if plist exists


def test_is_autostart_enabled_macos_returns_true_only_if_plist_exists(darwin_platform):
    """``_is_autostart_macos()`` returns True iff the plist file exists at
    ``~/Library/LaunchAgents/com.voicetyper.plist``. This is a simple
    file-existence probe (not a ``launchctl list`` query) so it works
    even when launchd is unresponsive.
    """
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
    #    (file-existence probe, not a launchd state query).
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict><key>Label</key><string>com.voicetyper</string></dict></plist>'
    )
    assert sp._is_autostart_macos() is True, (
        "is_autostart_enabled must return True if the plist file exists, "
        "even if launchctl hasn't loaded it (file-existence probe, NOT a "
        "launchd state query)"
    )


# ─── AUTOSTART-CMD-VALIDATE: stale program paths report disabled ────────


def test_is_autostart_macos_false_when_plist_program_path_missing(darwin_platform):
    """A plist whose ``ProgramArguments`` point at a deleted interpreter /
    launcher must report autostart DISABLED (AUTOSTART-CMD-VALIDATE
    backport — mirrors the Windows ``_validate_runkey_command``
    behavior). Without this, deleting the venv leaves Settings showing
    a misleading "Autostart: enabled".
    """
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
    """A plist whose ``ProgramArguments`` point at real paths must report
    autostart enabled (the happy path of the AUTOSTART-CMD-VALIDATE
    check — a valid registration must not be flagged stale)."""
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
        # /bin/true is a Linux-ism — macOS has no /bin/true (its true(1)
        # lives at /usr/bin/true), and the validation under test checks
        # Path(...).exists(). Use /bin/ls, present on macOS AND Linux.
        "<string>/bin/ls</string>"
        "</array></dict></plist>",
        encoding="utf-8",
    )
    assert sp._is_autostart_macos() is True, "plist pointing at existing paths must report autostart enabled"


# ─── Test 7: tauri.conf.json has bundle.macOS OR DMG+.app defaults ───────


def test_tauri_conf_has_macos_bundle_or_dmg_app_defaults():
    """``tauri.conf.json`` produces a DMG + .app on macOS. Either an
    explicit ``bundle.macOS`` block exists (with ``signingIdentity`` /
    ``entitlements``), OR ``bundle.targets`` is ``"all"`` (which
    defaults to DMG + .app on macOS per Tauri v2 defaults — see
    https://tauri.app/v2/guides/build/macos/).

    GAP-2 (report, do not fix): the current config relies on the
    ``"all"`` default — no explicit ``bundle.macOS`` block. Production
    distribution should wire in ``bundle.macOS.signingIdentity`` +
    ``entitlements`` so the bundler auto-signs the .app (currently
    signing is performed by the CI workflow post-build step).
    """
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
    # This is the path that 's fix should reference in the plist:
    #   /Applications/Voice Typer.app/Contents/MacOS/voice-typer-tauri
    assert CARGO_TOML.exists(), f"Cargo.toml missing at {CARGO_TOML}"
    cargo = CARGO_TOML.read_text(encoding="utf-8")
    assert re.search(r'name\s*=\s*"voice-typer-tauri"', cargo), (
        "Cargo.toml [[bin]] name must be 'voice-typer-tauri' (the .app's "
        "Contents/MacOS/ executable name — referenced by GAP-1's fix)"
    )


# ─── Test 8: entitlements.plist has 3 required entitlements ─────────────


def test_entitlements_plist_has_three_required_entitlements():
    """``src-tauri/entitlements.plist`` declares the 3 hardened-runtime
    entitlements required for notarization (per ADR-0020 §13.2 + the
    signing guide "Hardened runtime entitlements" section):

      1. ``com.apple.security.cs.allow-jit``
         CTranslate2 (faster-whisper) may use JIT compilation.
      2. ``com.apple.security.cs.disable-library-validation``
         Nuitka onefile extracts unsigned dylibs at runtime — without
         this entitlement, the hardened runtime kills the process.
      3. ``com.apple.security.device.audio-input``
         Microphone access (sounddevice) — required by TCC on macOS 11+.
    """
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
        "access via sounddevice — required by TCC on macOS 11+)"
    )

    # Sanity: no extra entitlements beyond the 3 required (guard against
    # accidental broadening of the attack surface).
    expected_keys = {
        "com.apple.security.cs.allow-jit",
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.device.audio-input",
    }
    actual_keys = set(data.keys())
    extra = actual_keys - expected_keys
    assert not extra, (
        f"entitlements.plist has EXTRA entitlements beyond the 3 required: "
        f"{extra}. Each entitlement broadens the hardened-runtime attack "
        f"surface — remove any that aren't strictly required."
    )


# ─── Test 9: installer bundles sidecar + prewarm + native listener ──────


def test_installer_includes_sidecar_prewarm_native_listener_resources():
    """The installer bundles the sidecar binary (``externalBin``), the
    macOS prewarm binaries (``resources``), and the native macOS
    key-listener (``resources``) so the Tauri app can spawn them at
    runtime without a separate Python install.

    macOS arches (per the CI workflow):
      - x86_64-apple-darwin  (Intel, via Rosetta 2 on Apple Silicon runners)
      - aarch64-apple-darwin (Apple Silicon, native on macos-14 runners)
    """
    assert TAURI_CONF.exists(), f"tauri.conf.json missing at {TAURI_CONF}"
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    bundle = conf["bundle"]

    # 1. Sidecar: externalBin (Tauri appends the target triple at runtime
    #    to resolve bin/python-sidecar-<arch>-apple-darwin).
    external_bin = bundle.get("externalBin", [])
    assert "bin/python-sidecar" in external_bin, (
        "externalBin must include bin/python-sidecar (Tauri appends the "
        "target triple to find bin/python-sidecar-{x86_64,aarch64}-apple-darwin)"
    )

    # 2. Prewarm + native listener: resources (per-platform binaries).
    resources = bundle.get("resources", [])
    resources_blob = "\n".join(resources)

    # Prewarm binaries for BOTH macOS arches (Intel + Apple Silicon).
    assert "prewarm-x86_64-apple-darwin" in resources_blob, (
        "resources must include prewarm-x86_64-apple-darwin (Intel macOS "
        "prewarm binary — built via Rosetta 2 on the macos-14 runner)"
    )
    assert "prewarm-aarch64-apple-darwin" in resources_blob, (
        "resources must include prewarm-aarch64-apple-darwin (Apple Silicon "
        "prewarm binary — built natively on the macos-14 runner)"
    )

    # Native macOS key-listener (ADR-0020 §6.4 — compiled CGEventTap hook
    # for the dictation toggle hotkey).
    assert "native/macos-key-listener" in resources_blob, (
        "resources must include native/macos-key-listener (ADR-0020 §6.4 — "
        "compiled CGEventTap hook for the dictation toggle hotkey)"
    )


# ─── Test 10: single-instance plugin enforced (ADR-0020 §12) ────────────


def test_single_instance_plugin_enforced():
    """Single-instance is enforced via ``tauri-plugin-single-instance``
    (ADR-0020 §12). A second launch must NOT spawn a second sidecar —
    instead, the second instance forwards its argv to the first (which
    focuses the existing main window) and exits immediately.

    Source-inspection (no mocking):
      1. ``tauri.conf.json`` declares ``plugins.single-instance``.
      2. ``Cargo.toml`` depends on ``tauri-plugin-single-instance``.
      3. ``main.rs`` registers the plugin FIRST (before the .setup() hook
         where the sidecar is spawned — ADR-0020 §12 ordering requirement
         so a second launch doesn't leave a zombie sidecar).
      4. The plugin's callback focuses the existing 'main' window.
    """
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
    # ADR-0020 §12: single-instance MUST be the FIRST plugin in the
    # tauri::Builder chain so its duplicate-instance check runs before any
    # sidecar spawn (which would otherwise leave a zombie python process on
    # a double-launch). Verify it's the first .plugin( call in the file.
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
    # spawn_sidecar_and_get_port). The plugin registration (in the Builder
    # chain) must precede .setup().
    setup_idx = main_rs.find(".setup(")
    if setup_idx != -1:
        assert single_instance_idx < setup_idx, (
            "single-instance plugin must be registered BEFORE the .setup() "
            "hook (where the sidecar is spawned) — ADR-0020 §12 ordering"
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


# ─── Test 11: CI workflow runs codesign + notarytool + stapler ──────────


def test_ci_workflow_runs_codesign_notarytool_stapler():
    """``.github/workflows/tauri-macos-build.yml`` runs the full code
    signing + notarization + stapling chain (ADR-0020 §13.2). This is
    REQUIRED for distribution — a DMG with an unsigned .app will be
    rejected by Gatekeeper on user machines.

    Source-inspection of the YAML (no mocking). The workflow jobs are
    enabled (``if: true`` — GAP-3 closed) so the Phase 0-M validation
    run executes via ``workflow_dispatch``.
    """
    assert CI_WORKFLOW.exists(), f"CI workflow missing at {CI_WORKFLOW}"
    yaml_text = CI_WORKFLOW.read_text(encoding="utf-8")

    # 1. codesign — signs the .dmg with the Developer ID identity.
    #    (The .app is signed by `cargo tauri build` when
    #    bundle.macOS.signingIdentity is set, OR by an explicit
    #    `codesign --force --deep ...` step — the workflow signs the
    #    .dmg explicitly because Tauri's bundler doesn't auto-sign DMGs.)
    assert "codesign" in yaml_text, (
        "CI workflow must invoke `codesign` to sign the .dmg (and/or .app) with the Developer ID identity"
    )
    assert "MAC_SIGNING_IDENTITY" in yaml_text, (
        "CI workflow must reference $MAC_SIGNING_IDENTITY (the Developer ID "
        "Application certificate Common Name, passed to `codesign --sign`)"
    )

    # 2. notarytool — submits the .app + .dmg to Apple's notarization service.
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

    # 3. stapler — staples the notarization ticket to the .app + .dmg
    #    (so the ticket is available offline, without a round-trip to
    #    Apple's servers on first launch).
    assert "xcrun stapler staple" in yaml_text, (
        "CI workflow must invoke `xcrun stapler staple` for both the .app "
        "and the .dmg (so the notarization ticket is embedded — required "
        "for offline Gatekeeper validation)"
    )
    assert "xcrun stapler validate" in yaml_text, (
        "CI workflow must invoke `xcrun stapler validate` to verify the "
        "stapled ticket (the runbook §7.5 pass criterion is 'The validate "
        "action worked!')"
    )

    # 4. The notarize step must be conditional on secrets being present
    #    (so PRs from forks don't fail for missing secrets).
    assert "MAC_SIGNING_IDENTITY != ''" in yaml_text or ("env.MAC_SIGNING_IDENTITY" in yaml_text), (
        "the notarize step must be guarded on $MAC_SIGNING_IDENTITY being "
        "non-empty (so fork PRs without access to secrets don't fail)"
    )

    # 5. Sanity: the workflow builds BOTH arches (universal .app + .dmg).
    assert "universal-apple-darwin" in yaml_text, (
        "CI workflow must build a universal .app + .dmg (--target universal-apple-darwin — combines x86_64 + aarch64)"
    )
    assert "aarch64-apple-darwin" in yaml_text, "CI workflow must build the aarch64 (Apple Silicon) sidecar + prewarm"
    assert "x86_64-apple-darwin" in yaml_text, (
        "CI workflow must build the x86_64 (Intel, via Rosetta 2) sidecar + prewarm"
    )

    # 6. The workflow jobs are enabled (`if: true` — GAP-3 closed, TX-39):
    #    Phase 0-M validation is runnable via manual dispatch. The
    #    workflow is NOT a stub anymore; no `if: false` may remain.
    assert "if: false" not in yaml_text, (
        "GAP-3: the workflow must have NO `if: false` guards left — all 3 "
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
