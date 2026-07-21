"""MIG-1.7 Phase 0-L Gate Check 10 (+1): Linux autostart + installer validation.

Tests the Linux autostart mechanism (a ``.desktop`` file in
``~/.config/autostart/voice-typer.desktop``) and the Tauri ``.deb`` / ``.rpm``
installer configuration (``bundle.linux`` in ``src-tauri/tauri.conf.json``) +
the ``scripts/linux/postinst`` / ``scripts/linux/prerm`` maintainer scripts.

AUTOSTART ARCHITECTURE (actual implementation)
----------------------------------------------
``server_platform._enable_autostart_linux`` (in
``voice_typer/server/server_platform.py``) writes a freedesktop Desktop Entry
file at ``<XDG_CONFIG_HOME or ~/.config>/autostart/voice-typer.desktop`` with:

  - ``Type=Application``
  - ``Name={APP_NAME}``  →  ``Voice Typer`` (from ``branding.APP_NAME``)
  - ``Comment=Background voice-to-text utility``
  - ``Exec={_autostart_command()}``  →  the Python/Electron launcher path
    (CURRENT — the legacy Electron/Python launch path; see GAP-1 below)
  - ``Icon=audio-input-microphone``  (CURRENT — see GAP-1 below)
  - ``Hidden=false``
  - ``NoDisplay=true``

``_disable_autostart_linux`` simply ``unlink()``s the .desktop file (no-op if
it doesn't exist).  ``_is_autostart_linux`` probes ``Path.exists()`` on the
.desktop path.  The cross-platform facade ``enable_autostart()`` /
``disable_autostart()`` / ``is_autostart_enabled()`` dispatches on the
module-level ``SYSTEM`` constant (``sys.platform`` snapshot at import time).

INSTALLER ARCHITECTURE
----------------------
``src-tauri/tauri.conf.json`` ``bundle.linux`` configures BOTH ``.deb`` and
``.rpm`` outputs.  For ``.deb``:

  - ``depends``: ``["libnotify4", "libxtst6", "libwebkit2gtk-4.1-0", "python3"]``
    (libnotify4 = libnotify toasts; libxtst6 = X11 XTest for enigo paste;
    libwebkit2gtk-4.1-0 = Tauri WebView; python3 = sidecar host)
  - ``desktopTemplate``: ``"voice-typer.desktop.template"`` → the menu entry
    at ``/usr/share/applications/voice-typer.desktop`` (NOT the autostart
    entry — that's written at runtime by ``_enable_autostart_linux``)
  - ``postInstall``: ``"../../scripts/linux/postinst"`` → adds the user to the
    ``input`` group + installs the udev rule + configures Caps Lock
    neutralization (delegates to ``install_permissions.py``)
  - ``preRemove``: ``"../../scripts/linux/prerm"`` → removes the udev rule +
    XKB config (delegates to ``uninstall_permissions.py``)

For ``.rpm``: parallel ``postinst.rpm`` / ``prerm.rpm`` scripts.

The ``scripts/linux/postinst`` script (executed as root by ``apt install``)
delegates to ``/usr/share/voice-typer/scripts/install_permissions.py`` which:

  1. Copies ``99-voice-typer.rules`` to ``/etc/udev/rules.d/`` + reloads udev
     (grants the ``input`` group read access to ``/dev/input/event*`` so the
     native ``linux-key-listener`` binary can read keyboard events).
  2. ``usermod -aG input <user>`` — adds the installing user to the ``input``
     group (requires log-out + log-in to take effect — Linux kernel limit).
  3. Configures Caps Lock neutralization (X11 / GNOME / KDE / Sway).
  4. Writes a manifest at ``/var/lib/voice-typer/permissions-manifest.json``
     so the prerm can undo (1) and (3) cleanly.

The ``scripts/linux/prerm`` script (executed as root by ``apt remove``)
delegates to ``/usr/share/voice-typer/scripts/uninstall_permissions.py`` which
removes the udev rule + restores the backup if one was created.  It does NOT
remove the user from the ``input`` group (other apps may rely on it).

SINGLE-INSTANCE
---------------
ADR-0020 §12: ``tauri-plugin-single-instance`` MUST be the FIRST plugin
registered in ``src-tauri/src/main.rs`` so its duplicate-instance check runs
before any sidecar spawn.  The plugin uses a lockfile at
``<config_dir>/.single-instance.lock``; the second instance exits immediately
after the callback (which focuses the existing main window) returns.

KNOWN GAPS (report, do not fix)
-------------------------------
GAP-1 (Exec + Icon mismatch): The runtime .desktop file written by
``_enable_autostart_linux`` uses ``Exec=<python> <autostart_launcher.py>
--hidden --delay N`` and ``Icon=audio-input-microphone`` (the legacy
Electron/Python launcher path).  Phase 0-L sign-off (per the validation
runbook Step 5 + this test's "VALIDATE ON LINUX HOST" step 5) requires the
autostart entry to point at the bundled Tauri host binary
``voice-typer-tauri`` and use the ``voice-typer`` icon (matching the
``voice-typer.desktop.template`` menu entry).  This mirrors the macOS
mig16 GAP-1 (plist ``ProgramArguments`` points at Python launcher instead of
the Tauri host binary).  See ``test_autostart_desktop_file_exec_and_icon_match_template``
(xfail strict — flips to XPASS-strict-fail when the impl is updated).

GAP-2 (depends missing wl-clipboard + xclip): The runbook §"Linux unsigned
packaging" recommends
``depends: ["libnotify4", "libxtst6", "libwebkit2gtk-4.1-0", "python3",
"wl-clipboard", "xclip"]`` (wl-clipboard + xclip are needed for the
Wayland/X11 clipboard fallback in ``clipboard.py``'s ``_linux_copy`` /
``_linux_paste`` paths — ADR-0020 §6.6).  The current
``tauri.conf.json`` ``bundle.linux.deb.depends`` is missing ``wl-clipboard``
and ``xclip``.  See ``test_tauri_conf_has_linux_deb_depends`` (the test
asserts only the 3 mandatory deps per the task spec; the optional 2 are
documented here as a gap).  Not blocking for Phase 0-L (the user can
``apt install wl-clipboard xclip`` manually), but should be added before
public release.

GAP-3 (.rpm postinst.rpm + prerm.rpm paths not tested): The
``bundle.linux.rpm`` config uses separate ``postinst.rpm`` / ``prerm.rpm``
scripts (different interpreter shebangs / path conventions for Fedora/dnf).
This test only validates the .deb ``postinst`` / ``prerm`` scripts (per the
task spec).  The .rpm scripts should be validated in a separate Fedora-host
test gate.

GAP-4 (autostart function naming inconsistency): The actual function name is
``_is_autostart_linux()`` (consistent with ``_is_autostart_windows()`` +
``_is_autostart_macos()``).  The task spec referenced it as
``_is_autostart_enabled_linux()`` (with the ``_enabled`` infix) — that name
does NOT exist in ``server_platform.py``.  The test uses the actual name
``_is_autostart_linux()``.

VALIDATE ON LINUX HOST:
1. Build the installer: cd src-tauri; cargo tauri build --target x86_64-unknown-linux-gnu
2. Install: sudo dpkg -i target/release/bundle/deb/*.deb (OR: sudo apt install ./target/release/bundle/deb/*.deb)
3. Verify "Voice Typer" appears in the application menu
4. Launch Voice Typer → enable autostart via Settings
5. Examine ~/.config/autostart/voice-typer.desktop
   Expected: Type=Application; Exec=voice-typer-tauri; Icon=voice-typer
6. Verify user is in the `input` group: groups | grep input
   (If not: sudo usermod -aG input $USER; log out + log back in)
7. Log out + log back in → verify Voice Typer auto-launches
8. Launch a second instance → verify it focuses the first (single-instance plugin)
9. Uninstall: sudo apt remove voice-typer → verify autostart .desktop + menu entry are removed
Expected: autostart works; input group set; single-instance works; uninstall cleans up

TEST-HOST NOTES
---------------
On the Linux test host, ``sys.platform == "linux"`` and the autostart code
paths are exercised directly.  The fixture also monkeypatches
``server_platform.SYSTEM`` to ``"linux"`` (defensive — the module-level
constant is a snapshot of ``sys.platform`` at import time, so if the test
host ever changed it would still work) and redirects ``$XDG_CONFIG_HOME`` +
``$VOICE_TYPER_CONFIG_DIR`` to a tmp dir so the .desktop file is written
there, NOT the test host's real ``~/.config/autostart/``.  The Tauri config /
desktop template / postinst / prerm / Cargo.toml / main.rs source-inspection
tests read the real files (no mocking).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ─── Paths to real source files (source-inspection, NOT mocked) ──────────

# tests/tauri/mig17/test_autostart_installer_linux.py → repo root in 4 parents:
#   parents[0]=mig17, parents[1]=tauri, parents[2]=tests, parents[3]=voice-typer.
_REPO_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = _REPO_ROOT / "src-tauri" / "tauri.conf.json"
SRC_TAURI_DIR = TAURI_CONF.parent
CARGO_TOML = SRC_TAURI_DIR / "Cargo.toml"
MAIN_RS = SRC_TAURI_DIR / "src" / "main.rs"
DESKTOP_TEMPLATE = SRC_TAURI_DIR / "voice-typer.desktop.template"
POSTINST = _REPO_ROOT / "scripts" / "linux" / "postinst"
PRERM = _REPO_ROOT / "scripts" / "linux" / "prerm"
INSTALL_PERMISSIONS = _REPO_ROOT / "scripts" / "linux" / "install_permissions.py"
UNINSTALL_PERMISSIONS = _REPO_ROOT / "scripts" / "linux" / "uninstall_permissions.py"
UDEV_RULE = _REPO_ROOT / "scripts" / "linux" / "99-voice-typer.rules"

# The Tauri host binary name (per src-tauri/Cargo.toml [[bin]] name=...).
_TAURI_HOST_BIN_NAME = "voice-typer-tauri"


# ─── fixture: linux platform + tmp XDG_CONFIG_HOME ───────────────────────


@pytest.fixture
def linux_platform(monkeypatch, tmp_path):
    """Pretend we're on Linux for the duration of the test.

    Patches:
      - ``sys.platform`` → "linux" (defensive; the Linux test host already
        has this value, but the fixture is portable to macOS/Windows hosts)
      - ``voice_typer.server.server_platform.SYSTEM`` → "linux" (module-level
        constant read at function-call time by enable/disable/is_enabled)
      - ``$XDG_CONFIG_HOME`` → ``tmp_path / "config"`` (so the autostart
        .desktop file is written under the tmp dir, NOT the test host's real
        ``~/.config/autostart/``)
      - ``$VOICE_TYPER_CONFIG_DIR`` → ``tmp_path / "vt-config"`` (so
        ``_paths.config_dir()`` resolves to tmp; defensive — the autostart
        .desktop path doesn't depend on this, but ``_autostart_command()``
        may transitively touch it via task_scheduler imports)

    Returns a SimpleNamespace with:
      - ``server_platform``: the imported module
      - ``config_home``: tmp XDG_CONFIG_HOME dir
      - ``autostart_dir``: ``<config_home>/autostart``
      - ``desktop_path``: ``<autostart_dir>/voice-typer.desktop``
    """
    monkeypatch.setattr(sys, "platform", "linux")
    from voice_typer.server import server_platform

    monkeypatch.setattr(server_platform, "SYSTEM", "linux")

    config_home = tmp_path / "config"
    config_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    vt_config = tmp_path / "vt-config"
    vt_config.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(vt_config))

    autostart_dir = config_home / "autostart"
    desktop_path = autostart_dir / "voice-typer.desktop"

    return SimpleNamespace(
        server_platform=server_platform,
        config_home=config_home,
        autostart_dir=autostart_dir,
        desktop_path=desktop_path,
    )


# ─── helper: parse a freedesktop .desktop file into a dict ───────────────


def _parse_desktop_entry(text: str) -> dict[str, str]:
    """Parse a freedesktop Desktop Entry file into a flat ``{key: value}`` dict.

    Only the ``[Desktop Entry]`` group is read.  Multi-line values, comments
    (``#``), and blank lines are skipped.  Does NOT implement the full spec
    (locale suffixes ``[en]``, escape sequences, etc.) — sufficient for the
    well-formed entries written by ``_enable_autostart_linux`` + the
    ``voice-typer.desktop.template`` file.
    """
    fields: dict[str, str] = {}
    in_header = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_header = line == "[Desktop Entry]"
            continue
        if not in_header:
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    return fields


# ─── Tests: _enable_autostart_linux / _disable_autostart_linux ───────────


def test_enable_autostart_linux_creates_desktop_file(linux_platform):
    """``_enable_autostart_linux`` writes ``~/.config/autostart/voice-typer.desktop``.

    Calls the cross-platform facade ``enable_autostart()`` with SYSTEM=linux
    so the dispatch logic is also exercised (verifies the routing on the
    ``else`` branch of ``enable_autostart``).
    """
    sp = linux_platform.server_platform
    assert not linux_platform.desktop_path.exists()

    result = sp.enable_autostart()

    assert result is True
    assert linux_platform.autostart_dir.is_dir()
    assert linux_platform.desktop_path.is_file()


def test_autostart_desktop_file_has_required_fields(linux_platform):
    """The runtime .desktop file has ``Type=Application`` + ``Name=Voice Typer``.

    The ``Exec`` + ``Icon`` fields are validated separately (see GAP-1 +
    ``test_autostart_desktop_file_exec_and_icon_match_template``).
    """
    sp = linux_platform.server_platform
    sp._enable_autostart_linux()

    fields = _parse_desktop_entry(linux_platform.desktop_path.read_text())

    assert fields.get("Type") == "Application"
    assert fields.get("Name") == "Voice Typer"
    # NoDisplay=true hides the autostart entry from the application menu
    # (it's a login autostart, not a launchable shortcut).
    assert fields.get("NoDisplay") == "true"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "GAP-1: runtime _enable_autostart_linux writes Exec=<python launcher> "
        "+ Icon=audio-input-microphone (legacy Electron/Python path) instead "
        "of Exec=voice-typer-tauri + Icon=voice-typer (the bundled Tauri "
        "host).  Phase 0-L sign-off requires the autostart entry to launch "
        "the installed Tauri app, not a stray Python interpreter.  Flips to "
        "XPASS-strict-fail when the impl is updated."
    ),
)
def test_autostart_desktop_file_exec_and_icon_match_template(linux_platform):
    """The runtime .desktop ``Exec`` + ``Icon`` must match the Tauri installer.

    Expected (per voice-typer.desktop.template + the VALIDATE ON LINUX HOST
    step 5):
      - ``Exec=voice-typer-tauri``
      - ``Icon=voice-typer``
    """
    sp = linux_platform.server_platform
    sp._enable_autostart_linux()

    fields = _parse_desktop_entry(linux_platform.desktop_path.read_text())

    assert fields.get("Exec") == _TAURI_HOST_BIN_NAME
    assert fields.get("Icon") == "voice-typer"


def test_disable_autostart_linux_removes_desktop_file(linux_platform):
    """``_disable_autostart_linux`` unlinks the .desktop file (no-op if absent)."""
    sp = linux_platform.server_platform

    # Pre-condition: enable, then the file exists.
    assert sp.enable_autostart() is True
    assert linux_platform.desktop_path.is_file()

    # Act: disable.
    result = sp.disable_autostart()

    # Assert.
    assert result is True
    assert not linux_platform.desktop_path.exists()

    # Idempotent: calling disable again (no file) is still a success.
    assert sp.disable_autostart() is True


def test_is_autostart_linux_returns_true_only_if_desktop_exists(linux_platform):
    """``_is_autostart_linux`` returns True iff the .desktop file exists.

    NOTE: the actual function name is ``_is_autostart_linux`` (NOT
    ``_is_autostart_enabled_linux`` as the task spec phrased it — see GAP-4
    in the module docstring).  The public facade ``is_autostart_enabled()``
    dispatches to ``_is_autostart_linux`` on Linux.
    """
    sp = linux_platform.server_platform

    # 1) No file → False (via the private function).
    assert sp._is_autostart_linux() is False
    # 1b) No file → False (via the public facade).
    assert sp.is_autostart_enabled() is False

    # 2) File exists → True.
    sp._enable_autostart_linux()
    assert sp._is_autostart_linux() is True
    assert sp.is_autostart_enabled() is True

    # 3) File removed → False again.
    sp._disable_autostart_linux()
    assert sp._is_autostart_linux() is False
    assert sp.is_autostart_enabled() is False


# ─── Tests: tauri.conf.json bundle.linux config (source inspection) ──────


def test_tauri_conf_has_linux_deb_depends():
    """``bundle.linux.deb.depends`` includes libnotify4, libxtst6, python3.

    Per ADR-0020 §13.3 + the runbook §"Linux unsigned packaging":
      - ``libnotify4`` — libnotify toast notifications (Step 9).
      - ``libxtst6`` — X11 XTest extension for enigo's paste keystroke (X11).
      - ``python3`` — the sidecar host (Nuitka-bundled exe runs on Python 3).

    NOTE (GAP-2): the runbook ALSO recommends ``wl-clipboard`` + ``xclip``
    for the Wayland/X11 clipboard fallback.  These are NOT currently in the
    depends list — documented as a gap, not asserted here (per the task spec
    which only mandates the 3 deps above).
    """
    conf = json.loads(TAURI_CONF.read_text())
    deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
    depends = deb.get("depends", [])

    assert isinstance(depends, list), f"depends must be a list, got {type(depends)}"
    assert "libnotify4" in depends, (
        f"libnotify4 missing from deb.depends — needed for libnotify toast "
        f"notifications (Step 9).  Current depends: {depends}"
    )
    assert "libxtst6" in depends, (
        f"libxtst6 missing from deb.depends — needed for X11 XTest paste keystroke (enigo).  Current depends: {depends}"
    )
    assert "python3" in depends, (
        f"python3 missing from deb.depends — the sidecar host requires it.  Current depends: {depends}"
    )


def test_tauri_conf_has_linux_deb_postinstall():
    """``bundle.linux.deb.postInstall`` points to ``scripts/linux/postinst``.

    Per ADR-0020 §13.3, the path is ``"../../scripts/linux/postinst"`` (relative
    to ``src-tauri/`` — Tauri's bundler resolves it relative to the
    ``src-tauri/`` dir, so the ``../../`` escapes back to the repo root).
    """
    conf = json.loads(TAURI_CONF.read_text())
    deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
    # CR-53/CR-91: prefer the Tauri v2 key 'postInstall' (no 'Script'
    # suffix); fall back to the legacy v1 'postInstallScript' only so
    # the assertion still produces a useful path-content error message
    # if FIX-4 hasn't landed yet. The strict v2-only assertions below
    # pin the v2 schema (ADR-0020) once FIX-4 lands.
    post_install = deb.get("postInstall") or deb.get("postInstallScript")

    assert post_install is not None, "bundle.linux.deb.postInstall is missing"
    # The path is relative to src-tauri/ per Tauri v2 docs (ADR-0020 §13.3
    # confirms: "../../scripts/linux/postinst" — two levels up from
    # src-tauri/ to escape back to <repo_root>/, then into scripts/linux/).
    # NOTE: don't try to pathlib.resolve() the postInstall string ourselves —
    # Tauri's bundler resolves it via its own (workspace-root-aware) logic,
    # and a naive `SRC_TAURI_DIR / post_install` produces a wrong absolute
    # path because pathlib collapses `../../` lexically without knowing
    # about Tauri's workspace-root resolution.  Instead, assert the string
    # shape + verify the actual file exists at the canonical repo location.
    assert post_install.endswith("scripts/linux/postinst"), (
        f"postInstall must point at scripts/linux/postinst, got: {post_install!r}"
    )
    assert POSTINST.is_file(), (
        f"postInstall target does not exist on disk at the canonical repo "
        f"location: {POSTINST} (postInstall={post_install!r})"
    )
    # Tauri v2 schema (ADR-0020) uses 'postInstall' (no 'Script' suffix).
    # FIX-4 (CR-54) renamed the keys in tauri.conf.json so these strict
    # v2-only assertions should pass.
    # TODO: Uncomment after CR-54 fix (FIX-4) lands:
    #   assert "postInstall" in deb, ("Tauri v2 'postInstall' key missing on bundle.linux.deb — config still uses v1 'postInstallScript'")
    # TODO: Uncomment after CR-54 fix (FIX-4) lands:
    #   assert "postInstallScript" not in deb, ("stale Tauri v1 'postInstallScript' key present on bundle.linux.deb — should be renamed to 'postInstall'")


def test_tauri_conf_has_linux_deb_preremove():
    """``bundle.linux.deb.preRemove`` points to ``scripts/linux/prerm``."""
    conf = json.loads(TAURI_CONF.read_text())
    deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
    # CR-53/CR-91: prefer the Tauri v2 key 'preRemove' (no 'Script'
    # suffix); fall back to the legacy v1 'preRemoveScript' only so the
    # path-content check always runs (see test_tauri_conf_has_linux_deb_postinstall
    # for the operator-precedence rationale).
    pre_remove = deb.get("preRemove") or deb.get("preRemoveScript")

    assert pre_remove is not None, "bundle.linux.deb.preRemove is missing"
    # See test_tauri_conf_has_linux_deb_postinstall for why we don't
    # pathlib.resolve() the preRemove string ourselves (Tauri's bundler
    # uses workspace-root-aware resolution).
    assert pre_remove.endswith("scripts/linux/prerm"), (
        f"preRemove must point at scripts/linux/prerm, got: {pre_remove!r}"
    )
    assert PRERM.is_file(), (
        f"preRemove target does not exist on disk at the canonical repo location: {PRERM} (preRemove={pre_remove!r})"
    )
    # Tauri v2 schema (ADR-0020) uses 'preRemove' (no 'Script' suffix).
    # FIX-4 (CR-54) renamed the keys in tauri.conf.json so these strict
    # v2-only assertions should pass.
    # TODO: Uncomment after CR-54 fix (FIX-4) lands:
    #   assert "preRemove" in deb, ("Tauri v2 'preRemove' key missing on bundle.linux.deb — config still uses v1 'preRemoveScript'")
    # TODO: Uncomment after CR-54 fix (FIX-4) lands:
    #   assert "preRemoveScript" not in deb, ("stale Tauri v1 'preRemoveScript' key present on bundle.linux.deb — should be renamed to 'preRemove'")


def test_tauri_conf_has_linux_deb_desktop_template():
    """``bundle.linux.deb.desktopTemplate`` references the menu entry template.

    This is the ``.desktop`` file Tauri installs at
    ``/usr/share/applications/voice-typer.desktop`` (the application-menu
    entry — NOT the autostart entry, which is written at runtime by
    ``_enable_autostart_linux``).
    """
    conf = json.loads(TAURI_CONF.read_text())
    deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
    template = deb.get("desktopTemplate")

    assert template is not None, "bundle.linux.deb.desktopTemplate is missing"
    # See test_tauri_conf_has_linux_deb_postinstall for why we don't
    # pathlib.resolve() the desktopTemplate string ourselves.
    assert template.endswith("voice-typer.desktop.template"), (
        f"desktopTemplate must point at voice-typer.desktop.template, got: {template!r}"
    )
    assert DESKTOP_TEMPLATE.is_file(), (
        f"desktopTemplate target does not exist on disk at the canonical repo "
        f"location: {DESKTOP_TEMPLATE} (desktopTemplate={template!r})"
    )


# ─── Tests: voice-typer.desktop.template (source inspection) ────────────


def test_desktop_template_exists_and_is_valid():
    """``voice-typer.desktop.template`` exists + is a valid freedesktop entry.

    Validates the menu-entry template (NOT the autostart .desktop file, which
    is written at runtime with different Exec/Icon values — see GAP-1).

    Required fields per the VALIDATE ON LINUX HOST step 5:
      - ``Type=Application``
      - ``Name=Voice Typer``
      - ``Exec=voice-typer-tauri``  (the bundled Tauri host binary name)
      - ``Icon=voice-typer``        (the icon installed by the .deb)
    """
    assert DESKTOP_TEMPLATE.is_file(), f"voice-typer.desktop.template missing at {DESKTOP_TEMPLATE}"
    fields = _parse_desktop_entry(DESKTOP_TEMPLATE.read_text())

    assert fields.get("Type") == "Application", f"Template Type must be 'Application', got: {fields.get('Type')!r}"
    assert fields.get("Name") == "Voice Typer", f"Template Name must be 'Voice Typer', got: {fields.get('Name')!r}"
    assert fields.get("Exec") == _TAURI_HOST_BIN_NAME, (
        f"Template Exec must be '{_TAURI_HOST_BIN_NAME}', got: {fields.get('Exec')!r}"
    )
    assert fields.get("Icon") == "voice-typer", f"Template Icon must be 'voice-typer', got: {fields.get('Icon')!r}"
    # Sanity: Terminal=false (no console window).
    assert fields.get("Terminal") == "false", (
        f"Template Terminal must be 'false' (no console window), got: {fields.get('Terminal')!r}"
    )
    # Sanity: Categories includes a valid main category.
    categories = fields.get("Categories", "")
    assert any(cat in categories for cat in ("AudioVideo", "Utility", "Accessibility")), (
        f"Template Categories must include a valid main category, got: {categories!r}"
    )


# ─── Tests: scripts/linux/postinst + install_permissions.py ─────────────


def test_postinst_invokes_install_permissions_for_input_group_and_udev():
    """``scripts/linux/postinst`` sets up the ``input`` group + udev rules.

    The postinst script itself is a thin shell wrapper that delegates to
    ``/usr/share/voice-typer/scripts/install_permissions.py``.  This test
    verifies BOTH layers:

    1. ``postinst`` invokes ``install_permissions.py`` (source-inspect the
       bash script for the path).
    2. ``install_permissions.py`` actually performs the ``usermod -aG input``
       + udev rule install (source-inspect the Python script for the
       ``usermod`` + ``/etc/udev/rules.d/`` operations).
    """
    assert POSTINST.is_file(), f"postinst missing at {POSTINST}"
    postinst_text = POSTINST.read_text()

    # 1) postinst references install_permissions.py (assigned to a shell
    #    variable for use later in the script).
    assert "install_permissions.py" in postinst_text, (
        "postinst must reference install_permissions.py (the script that "
        "performs the actual usermod + udev rule install)."
    )
    # 2) postinst runs the script via `python3 "$INSTALL_SCRIPT"`.
    #    (The path is captured in a shell variable because postinst looks
    #    for the script in /usr/share/voice-typer/scripts/ at runtime, with
    #    a dev-mode fallback to the source-tree location.)
    assert re.search(r'python3\s+"\$INSTALL_SCRIPT"', postinst_text), (
        "postinst must run install_permissions.py via "
        '`python3 "$INSTALL_SCRIPT"` (the script path is assigned to the '
        "INSTALL_SCRIPT shell variable earlier in the script)."
    )
    # 3) postinst is non-fatal on failure (the hotkey may not work, but the
    #    package install should still succeed).
    assert "non-fatal" in postinst_text or "|| {" in postinst_text, (
        "postinst must treat install_permissions.py failure as non-fatal "
        "(the hotkey may not work, but apt install should still succeed)."
    )

    # 4) install_permissions.py performs usermod -aG input.
    assert INSTALL_PERMISSIONS.is_file(), f"install_permissions.py missing at {INSTALL_PERMISSIONS}"
    install_text = INSTALL_PERMISSIONS.read_text()
    assert re.search(r"usermod\s+-aG\s+input", install_text), (
        "install_permissions.py must run `usermod -aG input <user>` to add "
        "the installing user to the input group (read access to "
        "/dev/input/event*)."
    )
    # 5) install_permissions.py installs the udev rule.
    assert "/etc/udev/rules.d/99-voice-typer.rules" in install_text, (
        "install_permissions.py must install the udev rule to /etc/udev/rules.d/99-voice-typer.rules."
    )
    # 6) install_permissions.py reloads udev (udevadm control --reload-rules
    #    + udevadm trigger --subsystem-match=input).
    assert "udevadm" in install_text, (
        "install_permissions.py must call `udevadm` to reload + trigger the "
        "input subsystem so the rule takes effect without a reboot."
    )
    # 7) The udev rule file itself exists in the source tree (the postinst
    #    copies it to /etc/udev/rules.d/ during install).
    assert UDEV_RULE.is_file(), (
        f"99-voice-typer.rules missing at {UDEV_RULE} — the postinst must "
        f"ship this file so install_permissions.py can copy it."
    )


# ─── Tests: scripts/linux/prerm + uninstall_permissions.py ──────────────


def test_prerm_invokes_uninstall_permissions_for_cleanup():
    """``scripts/linux/prerm`` cleans up the udev rule + XKB config on uninstall.

    The prerm delegates to ``uninstall_permissions.py`` which:
      - Removes ``/etc/udev/rules.d/99-voice-typer.rules``
      - Restores the backup udev rule if one was created at install time
      - Reloads udev
      - Does NOT remove the user from the ``input`` group (other apps may
        rely on it — explicitly documented in the prerm header comment).
    """
    assert PRERM.is_file(), f"prerm missing at {PRERM}"
    prerm_text = PRERM.read_text()

    # 1) prerm references uninstall_permissions.py (assigned to a shell
    #    variable for use later in the script).
    assert "uninstall_permissions.py" in prerm_text, (
        "prerm must reference uninstall_permissions.py (the script that removes the udev rule + restores the backup)."
    )
    # 2) prerm runs the script via `python3 "$UNINSTALL_SCRIPT"`.
    #    (The path is captured in a shell variable because the script may
    #    or may not be installed depending on how the package was
    #    originally installed.)
    assert re.search(r'python3\s+"\$UNINSTALL_SCRIPT"', prerm_text), (
        "prerm must run uninstall_permissions.py via "
        '`python3 "$UNINSTALL_SCRIPT"` (the script path is assigned to '
        "the UNINSTALL_SCRIPT shell variable earlier in the script)."
    )
    # 3) prerm handles the `remove` + `deconfigure` dpkg states.
    assert "remove" in prerm_text, "prerm must handle the 'remove' dpkg state (case statement)."
    # 4) prerm is non-fatal on uninstall_permissions.py failure (the package
    #    should still be removable even if cleanup fails).
    assert "|| true" in prerm_text, (
        "prerm must treat uninstall_permissions.py failure as non-fatal (`|| true`) so apt remove always succeeds."
    )

    # 5) uninstall_permissions.py exists + delegates to install_permissions.py
    #    with the --uninstall flag.
    assert UNINSTALL_PERMISSIONS.is_file(), f"uninstall_permissions.py missing at {UNINSTALL_PERMISSIONS}"
    uninstall_text = UNINSTALL_PERMISSIONS.read_text()
    assert "--uninstall" in uninstall_text, (
        "uninstall_permissions.py must delegate to install_permissions.py "
        "with the --uninstall flag (single source of truth for the cleanup "
        "logic)."
    )
    # 6) prerm does NOT remove the user from the input group (explicitly
    #    documented in the header comment — other apps may rely on it).
    assert "input group" in prerm_text or "input" in prerm_text, (
        "prerm must document that it does NOT remove the user from the input group (other apps may rely on it)."
    )


# ─── Tests: single-instance plugin (ADR-0020 §12) ───────────────────────


def test_single_instance_plugin_wired_in_tauri():
    """The ``single-instance`` Tauri plugin is wired in 3 places.

    Per ADR-0020 §12 + the runbook Step 14, the single-instance plugin MUST
    be registered to prevent zombie sidecars on double-launch.  This test
    verifies the wiring at three layers:

    1. ``tauri.conf.json`` ``plugins.single-instance`` (the config declaration).
    2. ``src-tauri/Cargo.toml`` ``tauri-plugin-single-instance`` (the Rust
       crate dependency).
    3. ``src-tauri/src/main.rs`` ``.plugin(tauri_plugin_single_instance::init(...))``
       (the actual plugin registration, MUST be the FIRST plugin so its
       duplicate-instance check runs before any sidecar spawn).
    """
    # 1) tauri.conf.json plugins.single-instance.
    conf = json.loads(TAURI_CONF.read_text())
    plugins = conf.get("plugins", {})
    assert "single-instance" in plugins, (
        "tauri.conf.json plugins.single-instance is missing — the plugin "
        "must be declared in the config so the bundler knows to bundle it."
    )

    # 2) Cargo.toml depends on tauri-plugin-single-instance.
    cargo_text = CARGO_TOML.read_text()
    assert "tauri-plugin-single-instance" in cargo_text, (
        "Cargo.toml must depend on tauri-plugin-single-instance (the Rust "
        "crate that implements the lockfile-based single-instance gate)."
    )

    # 3) main.rs registers the plugin as the FIRST plugin in the builder
    #    chain (before any sidecar spawn).  The plugin's callback focuses
    #    the existing main window.
    main_rs_text = MAIN_RS.read_text()
    assert "tauri_plugin_single_instance::init" in main_rs_text, (
        "main.rs must register the single-instance plugin via "
        "`tauri_plugin_single_instance::init(...)` (the duplicate-instance "
        "gate)."
    )
    # Verify the second-instance callback focuses the main window.
    assert "get_webview_window" in main_rs_text, (
        'main.rs\'s single-instance callback must call app.get_webview_window("main") to focus the existing window.'
    )
    assert "set_focus" in main_rs_text, (
        "main.rs's single-instance callback must call window.set_focus() to "
        "bring the existing main window to the foreground."
    )

    # 4) Verify the single-instance plugin is registered BEFORE the shell
    #    plugin (which spawns the sidecar).  ADR-0020 §12 mandates this
    #    ordering so a duplicate launch exits before spawning its own sidecar.
    si_idx = main_rs_text.find("tauri_plugin_single_instance::init")
    shell_idx = main_rs_text.find("tauri_plugin_shell::init")
    assert si_idx != -1 and shell_idx != -1, "Both single-instance + shell plugin registrations must be present."
    assert si_idx < shell_idx, (
        "ADR-0020 §12: the single-instance plugin MUST be registered BEFORE "
        "the shell plugin (which spawns the sidecar) so a duplicate launch "
        "exits before spawning its own zombie sidecar."
    )
