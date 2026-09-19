"""
Phase 0-L Gate Check 10 (+1): Linux autostart + installer validation.
KNOWN GAPS (report, do not fix)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


@pytest.fixture
def linux_platform(monkeypatch, tmp_path):
    """Pretend we're on Linux for the duration of the test."""
    monkeypatch.setattr(sys, "platform", "linux")
    from voice_typer.server import server_platform
    from voice_typer.server.server_platform import platform_flags

    monkeypatch.setattr(platform_flags, "SYSTEM", "linux")

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


def _parse_desktop_entry(text: str) -> dict[str, str]:
    """Parse a freedesktop Desktop Entry file into a flat ``{key: value}`` dict."""
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


def test_enable_autostart_linux_creates_desktop_file(linux_platform):
    """``_enable_autostart_linux`` writes ``~/.config/autostart/voice-typer.desktop``."""
    sp = linux_platform.server_platform
    assert not linux_platform.desktop_path.exists()

    result = sp.enable_autostart()

    assert result is True
    assert linux_platform.autostart_dir.is_dir()
    assert linux_platform.desktop_path.is_file()


def test_autostart_desktop_file_has_required_fields(linux_platform):
    """The runtime .desktop file has ``Type=Application`` + ``Name=Voice Typer``."""
    sp = linux_platform.server_platform
    sp._enable_autostart_linux()

    fields = _parse_desktop_entry(linux_platform.desktop_path.read_text())

    assert fields.get("Type") == "Application"
    assert fields.get("Name") == "Voice Typer"
    # NoDisplay=true hides the autostart entry from the application menu
    assert fields.get("NoDisplay") == "true"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "GAP-1: runtime _enable_autostart_linux writes Exec=<python launcher> "
        "+ Icon=audio-input-microphone (legacy predecessor/Python path) instead "
        "of Exec=voice-typer-tauri + Icon=voice-typer (the bundled Tauri "
        "host).  Phase 0-L sign-off requires the autostart entry to launch "
        "the installed Tauri app, not a stray Python interpreter.  Flips to "
        "XPASS-strict-fail when the impl is updated."
    ),
)
def test_autostart_desktop_file_exec_and_icon_match_template(linux_platform):
    """The runtime .desktop ``Exec`` + ``Icon`` must match the Tauri installer."""
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
    """``_is_autostart_linux`` returns True iff the .desktop file exists."""
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


def test_is_autostart_linux_false_when_exec_program_missing(linux_platform):
    """A .desktop whose ``Exec=`` points at a deleted interpreter must"""
    sp = linux_platform.server_platform
    desktop_path = linux_platform.autostart_dir / "voice-typer.desktop"
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Voice Typer\n"
        "Exec=/nonexistent/venv/bin/python /nonexistent/autostart_launcher.py --hidden\n",
        encoding="utf-8",
    )
    assert sp._is_autostart_linux() is False, (
        "desktop entry pointing at a deleted interpreter must report autostart disabled"
    )


def test_is_autostart_linux_true_when_exec_program_exists(linux_platform):
    """A .desktop whose ``Exec=`` points at a real program must report"""
    sp = linux_platform.server_platform
    desktop_path = linux_platform.autostart_dir / "voice-typer.desktop"
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.write_text(
        "[Desktop Entry]\nType=Application\nName=Voice Typer\nExec=/bin/true --hidden\n",
        encoding="utf-8",
    )
    assert sp._is_autostart_linux() is True, (
        "desktop entry pointing at an existing program must report autostart enabled"
    )


def test_tauri_conf_has_linux_deb_depends():
    """``bundle.linux.deb.depends`` includes libnotify4, libxtst6, python3."""
    conf = json.loads(TAURI_CONF.read_text())
    deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
    depends = deb.get("depends", [])

    assert isinstance(depends, list), f"depends must be a list, got {type(depends)}"
    assert "libnotify4" in depends, (
        f"libnotify4 missing from deb.depends, needed for libnotify toast "
        f"notifications (Step 9).  Current depends: {depends}"
    )
    assert "libxtst6" in depends, (
        f"libxtst6 missing from deb.depends, needed for X11 XTest paste keystroke (enigo).  Current depends: {depends}"
    )
    assert "python3" in depends, (
        f"python3 missing from deb.depends, the sidecar host requires it.  Current depends: {depends}"
    )


def test_tauri_conf_has_linux_deb_postinstall():
    """``bundle.linux.deb.postInstallScript`` points to ``scripts/linux/postinst``."""
    conf = json.loads(TAURI_CONF.read_text())
    deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
    # Tauri v2 Debian/RPM bundle schema uses the long-form keys
    assert "postInstallScript" in deb, (
        "bundle.linux.deb.postInstallScript missing, Tauri v2 requires the 'postInstallScript' key"
    )
    assert "postInstall" not in deb, (
        "stale short-form 'postInstall' key present on bundle.linux.deb, should use Tauri v2 'postInstallScript'"
    )
    post_install = deb["postInstallScript"]

    assert post_install is not None, "bundle.linux.deb.postInstallScript is missing"
    # The path is relative to src-tauri/ per Tauri v2 docs (ADR-0020 §13.3
    assert post_install.endswith("scripts/linux/postinst"), (
        f"postInstallScript must point at scripts/linux/postinst, got: {post_install!r}"
    )
    assert POSTINST.is_file(), (
        f"postInstallScript target does not exist on disk at the canonical repo "
        f"location: {POSTINST} (postInstallScript={post_install!r})"
    )


def test_tauri_conf_has_linux_deb_preremove():
    """``bundle.linux.deb.preRemoveScript`` points to ``scripts/linux/prerm``."""
    conf = json.loads(TAURI_CONF.read_text())
    deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
    # Tauri v2 uses the long-form `preRemoveScript` key (NOT the v1
    assert "preRemoveScript" in deb, (
        "bundle.linux.deb.preRemoveScript missing, Tauri v2 requires the 'preRemoveScript' key"
    )
    assert "preRemove" not in deb, (
        "stale short-form 'preRemove' key present on bundle.linux.deb, should use Tauri v2 'preRemoveScript'"
    )
    pre_remove = deb["preRemoveScript"]

    assert pre_remove is not None, "bundle.linux.deb.preRemoveScript is missing"
    # See test_tauri_conf_has_linux_deb_postinstall for why we don't
    assert pre_remove.endswith("scripts/linux/prerm"), (
        f"preRemoveScript must point at scripts/linux/prerm, got: {pre_remove!r}"
    )
    assert PRERM.is_file(), (
        f"preRemove target does not exist on disk at the canonical repo location: {PRERM} (preRemove={pre_remove!r})"
    )


def test_tauri_conf_has_linux_deb_desktop_template():
    """``bundle.linux.deb.desktopTemplate`` references the menu entry template."""
    conf = json.loads(TAURI_CONF.read_text())
    deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
    template = deb.get("desktopTemplate")

    assert template is not None, "bundle.linux.deb.desktopTemplate is missing"
    # See test_tauri_conf_has_linux_deb_postinstall for why we don't
    assert template.endswith("voice-typer.desktop.template"), (
        f"desktopTemplate must point at voice-typer.desktop.template, got: {template!r}"
    )
    assert DESKTOP_TEMPLATE.is_file(), (
        f"desktopTemplate target does not exist on disk at the canonical repo "
        f"location: {DESKTOP_TEMPLATE} (desktopTemplate={template!r})"
    )


def test_desktop_template_exists_and_is_valid():
    """``voice-typer.desktop.template`` exists + is a valid freedesktop entry."""
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


def test_postinst_invokes_install_permissions_for_input_group_and_udev():
    """``scripts/linux/postinst`` sets up the ``input`` group + udev rules."""
    assert POSTINST.is_file(), f"postinst missing at {POSTINST}"
    postinst_text = POSTINST.read_text()

    # 1) postinst references install_permissions.py (assigned to a shell
    assert "install_permissions.py" in postinst_text, (
        "postinst must reference install_permissions.py (the script that "
        "performs the actual usermod + udev rule install)."
    )
    # 2) postinst runs the script via `python3 "$INSTALL_SCRIPT"`.
    assert re.search(r'python3\s+"\$INSTALL_SCRIPT"', postinst_text), (
        "postinst must run install_permissions.py via "
        '`python3 "$INSTALL_SCRIPT"` (the script path is assigned to the '
        "INSTALL_SCRIPT shell variable earlier in the script)."
    )
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
    assert "udevadm" in install_text, (
        "install_permissions.py must call `udevadm` to reload + trigger the "
        "input subsystem so the rule takes effect without a reboot."
    )
    # 7) The udev rule file itself exists in the source tree (the postinst
    assert UDEV_RULE.is_file(), (
        f"99-voice-typer.rules missing at {UDEV_RULE}, the postinst must "
        f"ship this file so install_permissions.py can copy it."
    )


def test_prerm_invokes_uninstall_permissions_for_cleanup():
    """``scripts/linux/prerm`` cleans up the udev rule + XKB config on uninstall."""
    assert PRERM.is_file(), f"prerm missing at {PRERM}"
    prerm_text = PRERM.read_text()

    # 1) prerm references uninstall_permissions.py (assigned to a shell
    assert "uninstall_permissions.py" in prerm_text, (
        "prerm must reference uninstall_permissions.py (the script that removes the udev rule + restores the backup)."
    )
    # 2) prerm runs the script via `python3 "$UNINSTALL_SCRIPT"`.
    assert re.search(r'python3\s+"\$UNINSTALL_SCRIPT"', prerm_text), (
        "prerm must run uninstall_permissions.py via "
        '`python3 "$UNINSTALL_SCRIPT"` (the script path is assigned to '
        "the UNINSTALL_SCRIPT shell variable earlier in the script)."
    )
    # 3) prerm handles the `remove` + `deconfigure` dpkg states.
    assert "remove" in prerm_text, "prerm must handle the 'remove' dpkg state (case statement)."
    # 4) prerm is non-fatal on uninstall_permissions.py failure (the package
    assert "|| true" in prerm_text, (
        "prerm must treat uninstall_permissions.py failure as non-fatal (`|| true`) so apt remove always succeeds."
    )

    # 5) uninstall_permissions.py exists + delegates to install_permissions.py
    assert UNINSTALL_PERMISSIONS.is_file(), f"uninstall_permissions.py missing at {UNINSTALL_PERMISSIONS}"
    uninstall_text = UNINSTALL_PERMISSIONS.read_text()
    assert "--uninstall" in uninstall_text, (
        "uninstall_permissions.py must delegate to install_permissions.py "
        "with the --uninstall flag (single source of truth for the cleanup "
        "logic)."
    )
    # 6) prerm does NOT remove the user from the input group (explicitly
    assert "input group" in prerm_text or "input" in prerm_text, (
        "prerm must document that it does NOT remove the user from the input group (other apps may rely on it)."
    )


def test_single_instance_plugin_wired_in_tauri():
    """The ``single-instance`` Tauri plugin is wired in 3 places."""
    # 1) tauri.conf.json plugins.single-instance.
    conf = json.loads(TAURI_CONF.read_text())
    plugins = conf.get("plugins", {})
    assert "single-instance" in plugins, (
        "tauri.conf.json plugins.single-instance is missing, the plugin "
        "must be declared in the config so the bundler knows to bundle it."
    )

    # 2) Cargo.toml depends on tauri-plugin-single-instance.
    cargo_text = CARGO_TOML.read_text()
    assert "tauri-plugin-single-instance" in cargo_text, (
        "Cargo.toml must depend on tauri-plugin-single-instance (the Rust "
        "crate that implements the lockfile-based single-instance gate)."
    )

    # 3) main.rs registers the plugin as the FIRST plugin in the builder
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
    si_idx = main_rs_text.find("tauri_plugin_single_instance::init")
    shell_idx = main_rs_text.find("tauri_plugin_shell::init")
    assert si_idx != -1 and shell_idx != -1, "Both single-instance + shell plugin registrations must be present."
    assert si_idx < shell_idx, (
        "ADR-0020 §12: the single-instance plugin MUST be registered BEFORE "
        "the shell plugin (which spawns the sidecar) so a duplicate launch "
        "exits before spawning its own zombie sidecar."
    )
