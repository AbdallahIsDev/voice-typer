"""Phase 1 + §13.3: Linux unsigned packaging + postinst/prerm validation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_TAURI = PROJECT_ROOT / "src-tauri"
SCRIPTS_LINUX = PROJECT_ROOT / "scripts" / "linux"
SCRIPTS_BUILD = PROJECT_ROOT / "scripts" / "build"
TAURI_CONF = SRC_TAURI / "tauri.conf.json"
DESKTOP_TEMPLATE = SRC_TAURI / "voice-typer.desktop.template"
POSTINST_DEB = SCRIPTS_LINUX / "postinst"
PRERM_DEB = SCRIPTS_LINUX / "prerm"
POSTINST_RPM = SCRIPTS_LINUX / "postinst.rpm"
PRERM_RPM = SCRIPTS_LINUX / "prerm.rpm"
INSTALL_PERMS = SCRIPTS_LINUX / "install_permissions.py"
UNINSTALL_PERMS = SCRIPTS_LINUX / "uninstall_permissions.py"
BUILD_TAURI_ALL = SCRIPTS_BUILD / "build_tauri_all.sh"


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load tauri.conf.json once per module."""
    return json.loads(TAURI_CONF.read_text())


def test_tauri_conf_has_linux_bundle_block(tauri_conf: dict) -> None:
    """``bundle.linux`` must exist (ADR-0020 §7 + §13.3)."""
    assert "bundle" in tauri_conf, "missing top-level 'bundle' key"
    assert "linux" in tauri_conf["bundle"], "missing 'bundle.linux' block"


def test_deb_depends_includes_required_packages(tauri_conf: dict) -> None:
    """``deb.depends`` must include libnotify4, libxtst6, python3."""
    deb = tauri_conf["bundle"]["linux"]["deb"]
    assert "depends" in deb, "missing 'bundle.linux.deb.depends'"
    depends = deb["depends"]
    for required in ("libnotify4", "libxtst6", "python3"):
        assert required in depends, f"required deb dependency '{required}' missing from {depends}"


def test_deb_post_install_script_wired(tauri_conf: dict) -> None:
    """``deb.postInstallScript`` must point at ``scripts/linux/postinst``."""
    deb = tauri_conf["bundle"]["linux"]["deb"]
    assert "postInstallScript" in deb, (
        "bundle.linux.deb.postInstallScript missing, Tauri v2 requires the 'postInstallScript' key"
    )
    assert "postInstall" not in deb, (
        "stale short-form 'postInstall' key present on bundle.linux.deb, should use Tauri v2 'postInstallScript'"
    )
    post_install = deb["postInstallScript"]
    assert post_install is not None, "bundle.linux.deb.postInstallScript must be set"
    # Tauri resolves postInstallScript relative to src-tauri/, the config uses
    assert post_install.endswith("scripts/linux/postinst"), (
        f"postInstallScript should reference scripts/linux/postinst, got {post_install!r}"
    )


def test_deb_pre_remove_script_wired(tauri_conf: dict) -> None:
    """``deb.preRemoveScript`` must point at ``scripts/linux/prerm``."""
    deb = tauri_conf["bundle"]["linux"]["deb"]
    assert "preRemoveScript" in deb, (
        "bundle.linux.deb.preRemoveScript missing, Tauri v2 requires the 'preRemoveScript' key"
    )
    assert "preRemove" not in deb, (
        "stale short-form 'preRemove' key present on bundle.linux.deb, should use Tauri v2 'preRemoveScript'"
    )
    pre_remove = deb["preRemoveScript"]
    assert pre_remove is not None, "bundle.linux.deb.preRemoveScript must be set"
    assert pre_remove.endswith("scripts/linux/prerm"), (
        f"preRemoveScript should reference scripts/linux/prerm, got {pre_remove!r}"
    )


def test_deb_desktop_template_wired(tauri_conf: dict) -> None:
    """``deb.desktopTemplate`` must reference ``voice-typer.desktop.template``."""
    deb = tauri_conf["bundle"]["linux"]["deb"]
    assert "desktopTemplate" in deb, "missing 'bundle.linux.deb.desktopTemplate'"
    assert deb["desktopTemplate"] == "voice-typer.desktop.template", (
        f"desktopTemplate should be 'voice-typer.desktop.template', got {deb['desktopTemplate']!r}"
    )


def test_desktop_template_exists_and_is_valid() -> None:
    """``voice-typer.desktop.template`` must exist + be a valid .desktop entry."""
    assert DESKTOP_TEMPLATE.is_file(), f"desktop template missing: {DESKTOP_TEMPLATE}"
    text = DESKTOP_TEMPLATE.read_text()
    assert "[Desktop Entry]" in text, "desktop template must contain a '[Desktop Entry]' header"
    # Parse as INI-like key=value lines under the [Desktop Entry] header.
    keys: dict[str, str] = {}
    in_header = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_header = line == "[Desktop Entry]"
            continue
        if in_header and "=" in line:
            k, _, v = line.partition("=")
            keys[k.strip()] = v.strip()
    for required_key in ("Type", "Name", "Exec", "Icon"):
        assert required_key in keys, f"desktop template missing required key '{required_key}'"
    assert keys["Type"] == "Application", f"desktop template Type must be 'Application', got {keys['Type']!r}"
    exec_lower = keys["Exec"].lower()
    assert "voice-typer" in exec_lower or "voice_typer" in exec_lower, (
        f"desktop template Exec should reference voice-typer binary, got {keys['Exec']!r}"
    )


def test_postinst_deb_exists_and_invokes_install_permissions() -> None:
    """Debian ``postinst`` must exist + invoke ``install_permissions.py``."""
    assert POSTINST_DEB.is_file(), f"postinst missing: {POSTINST_DEB}"
    text = POSTINST_DEB.read_text()
    assert "install_permissions.py" in text, "postinst must reference install_permissions.py"
    assert "python3" in text, "postinst must invoke python3 to run install_permissions.py"
    assert INSTALL_PERMS.is_file(), f"install_permissions.py missing: {INSTALL_PERMS}"
    install_text = INSTALL_PERMS.read_text()
    assert "input" in install_text, "install_permissions.py must reference the 'input' group"
    assert "udev" in install_text.lower() or "99-voice-typer.rules" in install_text, (
        "install_permissions.py must reference the udev rule"
    )


def test_prerm_deb_exists_and_cleans_up() -> None:
    """Debian ``prerm`` must exist + invoke ``uninstall_permissions.py``."""
    assert PRERM_DEB.is_file(), f"prerm missing: {PRERM_DEB}"
    text = PRERM_DEB.read_text()
    assert "uninstall_permissions.py" in text, "prerm must reference uninstall_permissions.py for cleanup"
    assert UNINSTALL_PERMS.is_file(), f"uninstall_permissions.py missing: {UNINSTALL_PERMS}"


def test_postinst_prerm_are_shell_scripts() -> None:
    """postinst + prerm must be bash scripts with shebangs (executable by dpkg)."""
    for script in (POSTINST_DEB, PRERM_DEB):
        text = script.read_text()
        assert text.startswith("#!/bin/bash") or text.startswith("#!/bin/sh"), (
            f"{script.name} must start with a bash/sh shebang"
        )


def test_rpm_postinst_prerm_exist_and_wired(tauri_conf: dict) -> None:
    """``postinst.rpm`` + ``prerm.rpm`` must exist + be wired into the rpm bundle."""
    rpm = tauri_conf["bundle"]["linux"]["rpm"]
    # Tauri v2 uses long-form keys `postInstallScript` / `preRemoveScript`
    assert "postInstallScript" in rpm, (
        "bundle.linux.rpm.postInstallScript missing, Tauri v2 requires the 'postInstallScript' key"
    )
    assert "postInstall" not in rpm, (
        "stale short-form 'postInstall' key present on bundle.linux.rpm, should use Tauri v2 'postInstallScript'"
    )
    rpm_post_install = rpm["postInstallScript"]
    assert rpm_post_install is not None, "bundle.linux.rpm.postInstallScript must be set"
    assert rpm_post_install.endswith("scripts/linux/postinst.rpm"), (
        f"rpm.postInstallScript should reference scripts/linux/postinst.rpm, got {rpm_post_install!r}"
    )

    assert "preRemoveScript" in rpm, (
        "bundle.linux.rpm.preRemoveScript missing, Tauri v2 requires the 'preRemoveScript' key"
    )
    assert "preRemove" not in rpm, (
        "stale short-form 'preRemove' key present on bundle.linux.rpm, should use Tauri v2 'preRemoveScript'"
    )
    rpm_pre_remove = rpm["preRemoveScript"]
    assert rpm_pre_remove is not None, "bundle.linux.rpm.preRemoveScript must be set"
    assert rpm_pre_remove.endswith("scripts/linux/prerm.rpm"), (
        f"rpm.preRemoveScript should reference scripts/linux/prerm.rpm, got {rpm_pre_remove!r}"
    )
    assert POSTINST_RPM.is_file(), f"postinst.rpm missing: {POSTINST_RPM}"
    assert PRERM_RPM.is_file(), f"prerm.rpm missing: {PRERM_RPM}"
    rpm_post_text = POSTINST_RPM.read_text()
    assert "install_permissions.py" in rpm_post_text, "postinst.rpm must reference install_permissions.py"
    rpm_pre_text = PRERM_RPM.read_text()
    assert "uninstall_permissions.py" in rpm_pre_text, "prerm.rpm must reference uninstall_permissions.py"


def test_rpm_depends_includes_python3(tauri_conf: dict) -> None:
    """``rpm.depends`` must include python3 (needed by install_permissions.py)."""
    rpm = tauri_conf["bundle"]["linux"]["rpm"]
    assert "depends" in rpm, "missing 'bundle.linux.rpm.depends'"
    assert "python3" in rpm["depends"], f"rpm.depends must include 'python3', got {rpm['depends']}"


def test_linux_unsigned_by_default_in_build_script() -> None:
    """``build_tauri_all.sh`` must default to ``DO_SIGN=0`` (no signing)."""
    assert BUILD_TAURI_ALL.is_file(), f"build script missing: {BUILD_TAURI_ALL}"
    text = BUILD_TAURI_ALL.read_text()
    # DO_SIGN must default to 0 (off)
    assert re.search(r"^DO_SIGN=0\b", text, re.MULTILINE), (
        "build_tauri_all.sh must default DO_SIGN=0 (unsigned by default)"
    )
    # The Linux branch must explicitly state it's unsigned by default
    assert "unsigned by default" in text.lower(), "build_tauri_all.sh must document that Linux is unsigned by default"


def test_no_gpg_signing_in_linux_scripts() -> None:
    """No ``dpkg-sig --sign`` / ``rpm --addsign`` invocation in ``scripts/linux/``."""
    assert SCRIPTS_LINUX.is_dir(), f"scripts/linux missing: {SCRIPTS_LINUX}"
    for script in SCRIPTS_LINUX.iterdir():
        if not script.is_file():
            continue
        text = script.read_text(errors="ignore")
        assert "dpkg-sig --sign" not in text, (
            f"{script.name} must not invoke 'dpkg-sig --sign' (Linux is unsigned by default per ADR §13.3)"
        )
        assert "rpm --addsign" not in text, (
            f"{script.name} must not invoke 'rpm --addsign' (Linux is unsigned by default per ADR §13.3)"
        )


def test_tauri_conf_linux_has_no_signing_config(tauri_conf: dict) -> None:
    """
    ``bundle.linux`` must not declare any signing key/cert config.
    Tauri's deb/rpm bundlers do not have a native signing config (unlike
    """
    linux = tauri_conf["bundle"]["linux"]
    forbidden = ("signingIdentity", "signingKey", "gpg", "gpgKey", "signingCert")
    found: list[str] = []
    for container in (linux, linux.get("deb", {}), linux.get("rpm", {})):
        if not isinstance(container, dict):
            continue
        for key in forbidden:
            if key in container:
                found.append(key)
    assert not found, f"bundle.linux must not declare signing config (unsigned by default per ADR §13.3); found {found}"
