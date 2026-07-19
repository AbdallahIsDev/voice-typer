"""MIG-1.8 Phase 1 + §13.3 — Linux unsigned packaging + postinst/prerm validation.

Validates the Tauri ``bundle.linux`` block in
``src-tauri/tauri.conf.json`` against ADR-0020 §13.3 — Linux packages
are **unsigned by default** (GPG signing is optional + out of scope for
v1). The ``deb`` + ``rpm`` bundle configs wire the existing
``scripts/linux/postinst``, ``prerm``, ``postinst.rpm``, ``prerm.rpm``
scripts verbatim — these install the udev rule granting the ``input``
group read access to ``/dev/input/event*``, add the installing user to
``input``, configure Caps Lock neutralization, and write a manifest at
``/var/lib/voice-typer/permissions-manifest.json`` for clean uninstall.

These tests run on any platform (Linux sandbox included) — they only
read static files (``tauri.conf.json``, the shell scripts, the desktop
template, the build orchestrator). The actual ``dpkg -i`` / ``dnf
install`` install path + udev rule activation + input-group take-effect
(logout/login required) can only be validated on a real Linux host —
see the "VALIDATE ON LINUX HOST" block below.

VALIDATE ON LINUX HOST:
    1. cd src-tauri; cargo tauri build --target x86_64-unknown-linux-gnu
    2. Install: sudo dpkg -i target/release/bundle/deb/*.deb
    3. Verify "Voice Typer" appears in the application menu
    4. Verify user is in the `input` group: groups | grep input
    5. Verify udev rule: ls -l /etc/udev/rules.d/99-voice-typer.rules
    6. (Optional GPG signing) dpkg-sig --sign builder target/release/bundle/deb/*.deb
    Expected: .deb installs cleanly; input group + udev rule set up; menu entry created
    Note: Linux packages are unsigned by default (per ADR §13.3). GPG signing is optional.

References:
- ADR-0020 §13.3 "Linux (no signing by default)" —
  docs/adr/0020-desktop-runtime-migration-analysis.md
- ADR-0020 §7 (Tauri config) — ``bundle.linux.deb`` / ``bundle.linux.rpm``
- ``scripts/linux/install_permissions.py`` — single source of truth for
  Linux system modifications (udev rule + input group + Caps Lock + manifest)
- ``scripts/linux/postinst`` / ``prerm`` (Debian) + ``postinst.rpm`` /
  ``prerm.rpm`` (RPM) — thin wrappers around ``install_permissions.py`` /
  ``uninstall_permissions.py``
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ─── Path resolution ─────────────────────────────────────────────────────────
# tests/tauri/mig18/test_linux_signing.py → parents[3] = voice-typer project root
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


# ─── Tests: tauri.conf.json bundle.linux.deb ─────────────────────────────────


def test_tauri_conf_has_linux_bundle_block(tauri_conf: dict) -> None:
    """``bundle.linux`` must exist (ADR-0020 §7 + §13.3)."""
    assert "bundle" in tauri_conf, "missing top-level 'bundle' key"
    assert "linux" in tauri_conf["bundle"], "missing 'bundle.linux' block"


def test_deb_depends_includes_required_packages(tauri_conf: dict) -> None:
    """``deb.depends`` must include libnotify4, libxtst6, python3.

    These match ADR-0020 §13.3's mandated dependency list. ``libwebkit2gtk-4.1-0``
    is also expected (Tauri runtime requirement) but is distro-specific; we
    assert the three portable ones the ADR calls out by name.
    """
    deb = tauri_conf["bundle"]["linux"]["deb"]
    assert "depends" in deb, "missing 'bundle.linux.deb.depends'"
    depends = deb["depends"]
    for required in ("libnotify4", "libxtst6", "python3"):
        assert required in depends, f"required deb dependency '{required}' missing from {depends}"


def test_deb_post_install_script_wired(tauri_conf: dict) -> None:
    """``deb.postInstall`` must point at ``scripts/linux/postinst``."""
    deb = tauri_conf["bundle"]["linux"]["deb"]
    assert "postInstallScript" in deb or "postInstall" in deb, "missing 'bundle.linux.deb.postInstall'"
    # Tauri resolves postInstall relative to src-tauri/ — the config uses
    # "../../scripts/linux/postinst". We assert the tail to be robust to
    # the relative-path prefix.
    assert deb.get("postInstallScript") or deb["postInstall"].endswith("scripts/linux/postinst"), (
        f"postInstall should reference scripts/linux/postinst, got {deb['postInstall']!r}"
    )


def test_deb_pre_remove_script_wired(tauri_conf: dict) -> None:
    """``deb.preRemove`` must point at ``scripts/linux/prerm``."""
    deb = tauri_conf["bundle"]["linux"]["deb"]
    assert "preRemoveScript" in deb or "preRemove" in deb, "missing 'bundle.linux.deb.preRemove'"
    assert deb.get("preRemoveScript") or deb["preRemove"].endswith("scripts/linux/prerm"), (
        f"preRemove should reference scripts/linux/prerm, got {deb['preRemove']!r}"
    )


def test_deb_desktop_template_wired(tauri_conf: dict) -> None:
    """``deb.desktopTemplate`` must reference ``voice-typer.desktop.template``."""
    deb = tauri_conf["bundle"]["linux"]["deb"]
    assert "desktopTemplate" in deb, "missing 'bundle.linux.deb.desktopTemplate'"
    assert deb["desktopTemplate"] == "voice-typer.desktop.template", (
        f"desktopTemplate should be 'voice-typer.desktop.template', got {deb['desktopTemplate']!r}"
    )


# ─── Tests: desktop template ─────────────────────────────────────────────────


def test_desktop_template_exists_and_is_valid() -> None:
    """``voice-typer.desktop.template`` must exist + be a valid .desktop entry.

    A valid .desktop entry must start with ``[Desktop Entry]`` and declare at
    minimum ``Type``, ``Name``, ``Exec``, ``Icon`` (per the freedesktop.org
    Desktop Entry Spec).
    """
    assert DESKTOP_TEMPLATE.is_file(), f"desktop template missing: {DESKTOP_TEMPLATE}"
    text = DESKTOP_TEMPLATE.read_text()
    assert text.lstrip().startswith("[Desktop Entry]"), "desktop template must start with '[Desktop Entry]'"
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


# ─── Tests: postinst / prerm scripts (Debian) ────────────────────────────────


def test_postinst_deb_exists_and_invokes_install_permissions() -> None:
    """Debian ``postinst`` must exist + invoke ``install_permissions.py``.

    Per ADR-0020 §13.3, postinst is the thin wrapper that runs
    ``install_permissions.py`` (as root, during ``apt install``) to install
    the udev rule, add the user to the ``input`` group, configure Caps Lock
    neutralization, and write the manifest. ``install_permissions.py`` is the
    single source of truth for both the input-group + udev-rule setup.
    """
    assert POSTINST_DEB.is_file(), f"postinst missing: {POSTINST_DEB}"
    text = POSTINST_DEB.read_text()
    assert "install_permissions.py" in text, "postinst must reference install_permissions.py"
    assert "python3" in text, "postinst must invoke python3 to run install_permissions.py"
    # install_permissions.py is the single source of truth for the input group
    # + udev rule setup; assert it actually references both.
    assert INSTALL_PERMS.is_file(), f"install_permissions.py missing: {INSTALL_PERMS}"
    install_text = INSTALL_PERMS.read_text()
    assert "input" in install_text, "install_permissions.py must reference the 'input' group"
    assert "udev" in install_text.lower() or "99-voice-typer.rules" in install_text, (
        "install_permissions.py must reference the udev rule"
    )


def test_prerm_deb_exists_and_cleans_up() -> None:
    """Debian ``prerm`` must exist + invoke ``uninstall_permissions.py``.

    Per ADR-0020 §13.3, prerm runs ``uninstall_permissions.py`` (as root,
    during ``apt remove``) to remove the udev rule, XKB config, and the
    manifest. It does NOT remove the user from the ``input`` group (other
    apps may rely on it).
    """
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


# ─── Tests: RPM equivalents ──────────────────────────────────────────────────


def test_rpm_postinst_prerm_exist_and_wired(tauri_conf: dict) -> None:
    """``postinst.rpm`` + ``prerm.rpm`` must exist + be wired into the rpm bundle.

    ADR-0020 §13.3 explicitly says to reuse all four scripts (postinst, prerm,
    postinst.rpm, prerm.rpm) verbatim. The rpm bundler wires the .rpm variants
    because RPM's ``%post`` / ``%preun`` scriptlets differ in invocation
    convention ($1 semantics) from Debian's postinst/prerm.
    """
    rpm = tauri_conf["bundle"]["linux"]["rpm"]
    assert "postInstallScript" in rpm or "postInstall" in rpm, "missing 'bundle.linux.rpm.postInstall'"
    assert rpm.get("postInstallScript") or rpm["postInstall"].endswith("scripts/linux/postinst.rpm"), (
        f"rpm.postInstall should reference scripts/linux/postinst.rpm, got {rpm['postInstall']!r}"
    )
    assert "preRemoveScript" in rpm or "preRemove" in rpm, "missing 'bundle.linux.rpm.preRemove'"
    assert rpm.get("preRemoveScript") or rpm["preRemove"].endswith("scripts/linux/prerm.rpm"), (
        f"rpm.preRemove should reference scripts/linux/prerm.rpm, got {rpm['preRemove']!r}"
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


# ─── Tests: unsigned by default (ADR-0020 §13.3) ─────────────────────────────


def test_linux_unsigned_by_default_in_build_script() -> None:
    """``build_tauri_all.sh`` must default to ``DO_SIGN=0`` (no signing).

    ADR-0020 §13.3: "Linux packages are unsigned by default in both Electron
    (today) and Tauri." The build orchestrator must not enable any signing
    unless the operator explicitly passes ``--sign``. The Linux branch of the
    signing block must explicitly state it is unsigned by default.
    """
    assert BUILD_TAURI_ALL.is_file(), f"build script missing: {BUILD_TAURI_ALL}"
    text = BUILD_TAURI_ALL.read_text()
    # DO_SIGN must default to 0 (off)
    assert re.search(r"^DO_SIGN=0\b", text, re.MULTILINE), (
        "build_tauri_all.sh must default DO_SIGN=0 (unsigned by default)"
    )
    # The Linux branch must explicitly state it's unsigned by default
    assert "unsigned by default" in text.lower(), "build_tauri_all.sh must document that Linux is unsigned by default"


def test_no_gpg_signing_in_linux_scripts() -> None:
    """No ``dpkg-sig --sign`` / ``rpm --addsign`` invocation in ``scripts/linux/``.

    ADR-0020 §13.3 lists GPG-signing as an *optional* improvement that is
    out of scope for v1. The ``scripts/linux/`` directory must not contain
    any automated GPG-signing commands — signing (if done at all) is a
    manual post-build step documented in the ADR.
    """
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
    """``bundle.linux`` must not declare any signing key/cert config.

    Tauri's deb/rpm bundlers do not have a native signing config (unlike
    Windows/macOS which use CSC_LINK / MAC_SIGNING_IDENTITY), but we assert
    this defensively: no ``signingIdentity`` / ``signingKey`` / ``gpg`` keys
    should be present under ``bundle.linux``.
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
