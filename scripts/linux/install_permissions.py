#!/usr/bin/env python3
"""Voice Typer — Linux keyboard permission installer.

This is the single source of truth for "what system modifications does
Voice Typer make on Linux." Called by:

- Debian ``postinst`` (as root, during ``apt install voice-typer``)
- RPM ``%post`` (as root, during ``dnf install voice-typer``)
- AppImage first-run helper (as root, via ``pkexec``)

Operations (all idempotent):
  1. Copy ``99-voice-typer.rules`` to ``/etc/udev/rules.d/``
  2. Reload udev rules and trigger input subsystem
  3. Add the current user (from SUDO_USER / PKEXEC_UID) to the ``input`` group
  4. Detect session type (X11 / GNOME / KDE / Sway) and configure Caps Lock
     neutralization appropriately
  5. Write a manifest at ``/var/lib/voice-typer/permissions-manifest.json``
     tracking what was installed (used by uninstall_permissions.py)

Usage:
  python3 install_permissions.py [--uninstall]

Exit codes:
  0 = success
  1 = not running as root
  2 = no target user detected (SUDO_USER / PKEXEC_UID both empty)
  3 = udev rule installation failed
  4 = usermod failed
  5 = XKB / session config failed (non-fatal in some cases)
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ─── Constants ─────────────────────────────────────────────────────────────

UDEV_RULE_PATH = Path("/etc/udev/rules.d/99-voice-typer.rules")
UDEV_RULE_SOURCE = Path(__file__).resolve().parent / "99-voice-typer.rules"

XKB_CONF_PATH = Path("/etc/X11/xorg.conf.d/00-voice-typer-capslock.conf")
XKB_CONF_SOURCE = Path(__file__).resolve().parent / "00-voice-typer-capslock.conf"

MANIFEST_DIR = Path("/var/lib/voice-typer")
MANIFEST_PATH = MANIFEST_DIR / "permissions-manifest.json"

INPUT_GROUP = "input"


# ─── Helpers ───────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    """Print to stdout (captured by package managers / pkexec)."""
    print(f"[voice-typer-permissions] {msg}", flush=True)


def fail(code: int, msg: str) -> "None":  # noqa: ANN401
    log(f"ERROR: {msg}")
    sys.exit(code)


def is_root() -> bool:
    return os.geteuid() == 0


def get_target_user() -> str | None:
    """Determine which user to add to the input group.

    Order:
    1. ``SUDO_USER`` (set by sudo / apt / dnf)
    2. ``PKEXEC_UID`` (set by pkexec) — translate UID to username
    3. None (caller should handle)
    """
    sudo_user = os.environ.get("SUDO_USER", "").strip()
    if sudo_user:
        return sudo_user

    pkexec_uid = os.environ.get("PKEXEC_UID", "").strip()
    if pkexec_uid:
        try:
            return pwd.getpwuid(int(pkexec_uid)).pw_name
        except (ValueError, KeyError):
            pass

    return None


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, logging it first."""
    log(f"Running: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def backup_if_exists(path: Path) -> Path | None:
    """If path exists, back it up to path.bak. Returns the backup path or None."""
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        log(f"Backed up existing {path} → {backup}")
        return backup
    return None


# ─── Install operations ────────────────────────────────────────────────────


def install_udev_rule() -> None:
    """Copy the udev rule to /etc/udev/rules.d/ and reload udev."""
    if not UDEV_RULE_SOURCE.is_file():
        fail(3, f"udev rule source not found: {UDEV_RULE_SOURCE}")

    # Back up existing rule if it differs from ours
    if UDEV_RULE_PATH.exists():
        existing = UDEV_RULE_PATH.read_text()
        ours = UDEV_RULE_SOURCE.read_text()
        if existing != ours:
            backup_if_exists(UDEV_RULE_PATH)

    UDEV_RULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(UDEV_RULE_SOURCE, UDEV_RULE_PATH)
    UDEV_RULE_PATH.chmod(0o644)
    log(f"Installed udev rule to {UDEV_RULE_PATH}")

    # Reload udev rules
    try:
        run(["udevadm", "control", "--reload-rules"], check=False)
        run(["udevadm", "trigger", "--subsystem-match=input"], check=False)
        log("Reloaded udev rules")
    except FileNotFoundError:
        log("WARNING: udevadm not found — rules will apply on next boot")
    except subprocess.CalledProcessError as exc:
        log(f"WARNING: udevadm reload failed (non-fatal): {exc}")


def add_user_to_input_group(username: str) -> None:
    """Add the given user to the input group."""
    try:
        grp.getgrnam(INPUT_GROUP)
    except KeyError:
        fail(4, f"group '{INPUT_GROUP}' does not exist on this system")

    # Check if already a member
    try:
        input_grp = grp.getgrnam(INPUT_GROUP)
        if username in input_grp.gr_mem:
            log(f"User '{username}' is already in '{INPUT_GROUP}' group")
            return
    except KeyError:
        pass

    try:
        run(["usermod", "-aG", INPUT_GROUP, username])
        log(f"Added user '{username}' to '{INPUT_GROUP}' group")
    except FileNotFoundError:
        fail(4, "usermod not found — cannot add user to input group")
    except subprocess.CalledProcessError as exc:
        fail(4, f"usermod failed: {exc}")


def detect_session_type() -> str:
    """Detect the current session type for Caps Lock config.

    Returns one of: 'x11', 'gnome', 'kde', 'sway', 'wayland-other',
    'headless'.
    """
    xdg_session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    xdg_current_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()

    # If we're running under pkexec, the session env may not be set.
    # Try to read it from the target user's systemd session.
    if not xdg_session_type:
        target = get_target_user()
        if target:
            try:
                result = subprocess.run(
                    ["sudo", "-u", target, "systemctl", "--user", "show-property",
                     "SessionType"],
                    capture_output=True, text=True, timeout=5,
                )
                if "wayland" in result.stdout.lower():
                    xdg_session_type = "wayland"
                elif "x11" in result.stdout.lower():
                    xdg_session_type = "x11"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    if not xdg_session_type:
        # No display server detected (headless server, container, etc.)
        return "headless"

    if xdg_session_type == "x11":
        return "x11"

    if xdg_session_type == "wayland":
        if "GNOME" in xdg_current_desktop:
            return "gnome"
        if "KDE" in xdg_current_desktop:
            return "kde"
        if "SWAY" in xdg_current_desktop or "sway" in os.environ.get("SWAYSOCK", ""):
            return "sway"
        return "wayland-other"

    return "headless"


def configure_caps_lock_neutralization(session_type: str, username: str) -> dict:
    """Configure Caps Lock neutralization for the detected session type.

    Returns a dict of what was configured (for the manifest).
    """
    result = {
        "session_type": session_type,
        "xkb_conf_installed": False,
        "gnome_settings_modified": False,
        "kde_config_modified": False,
        "sway_config_modified": False,
    }

    if session_type == "headless":
        log("No display server detected — skipping Caps Lock neutralization")
        return result

    # X11: always install the XKB conf file (works for all X11 desktops)
    if session_type == "x11":
        try:
            XKB_CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
            if XKB_CONF_PATH.exists():
                existing = XKB_CONF_PATH.read_text()
                ours = XKB_CONF_SOURCE.read_text()
                if existing != ours:
                    backup_if_exists(XKB_CONF_PATH)
            shutil.copy2(XKB_CONF_SOURCE, XKB_CONF_PATH)
            XKB_CONF_PATH.chmod(0o644)
            log(f"Installed XKB config to {XKB_CONF_PATH}")
            result["xkb_conf_installed"] = True
        except OSError as exc:
            log(f"WARNING: failed to install XKB config (non-fatal): {exc}")

    # GNOME (X11 or Wayland): use gsettings
    if session_type == "gnome":
        try:
            # Run gsettings as the target user
            run(["sudo", "-u", username, "gsettings", "set",
                 "org.gnome.desktop.input-sources", "xkb-options",
                 "['caps:none']"], check=False)
            log(f"Set GNOME xkb-options to caps:none for user '{username}'")
            result["gnome_settings_modified"] = True
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            log(f"WARNING: gsettings failed (non-fatal): {exc}")

    # KDE: write to ~/.config/kxkbrc
    if session_type == "kde":
        try:
            user_pw = pwd.getpwnam(username)
            kxkbrc = Path(user_pw.pw_dir) / ".config" / "kxkbrc"
            kxkbrc.parent.mkdir(parents=True, exist_ok=True)
            # KDE's kxkbrc uses an INI-like format. The Layout Options
            # key is a comma-separated list.
            existing = kxkbrc.read_text() if kxkbrc.exists() else ""
            if "caps:none" not in existing:
                with kxkbrc.open("a") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write("[Layout]\nOptions=caps:none\n")
                log(f"Appended caps:none to {kxkbrc}")
                result["kde_config_modified"] = True
            else:
                log(f"caps:none already in {kxkbrc}")
                result["kde_config_modified"] = True
            # Fix ownership
            shutil.chown(kxkbrc, user_pw.pw_uid, user_pw.pw_gid)
        except (KeyError, OSError) as exc:
            log(f"WARNING: KDE config failed (non-fatal): {exc}")

    # Sway: append to ~/.config/sway/config (idempotent)
    if session_type == "sway":
        try:
            user_pw = pwd.getpwnam(username)
            sway_config = Path(user_pw.pw_dir) / ".config" / "sway" / "config"
            sway_config.parent.mkdir(parents=True, exist_ok=True)
            existing = sway_config.read_text() if sway_config.exists() else ""
            marker = "# Voice Typer — Caps Lock neutralization"
            if marker not in existing:
                with sway_config.open("a") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(f"\n{marker}\ninput * xkb_options caps:none\n")
                log(f"Appended caps:none to {sway_config}")
                result["sway_config_modified"] = True
            else:
                log(f"caps:none already in {sway_config}")
                result["sway_config_modified"] = True
            shutil.chown(sway_config, user_pw.pw_uid, user_pw.pw_gid)
        except (KeyError, OSError) as exc:
            log(f"WARNING: Sway config failed (non-fatal): {exc}")

    if session_type == "wayland-other":
        log("WARNING: unsupported Wayland compositor — Caps Lock neutralization skipped. "
            "Use a non-Caps-Lock hotkey (e.g. Alt) or configure your compositor manually.")

    return result


def write_manifest(
    username: str,
    session_info: dict,
    udev_backup: Path | None,
    xkb_backup: Path | None,
) -> None:
    """Write the manifest file tracking what was installed."""
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "target_user": username,
        "udev_rule": str(UDEV_RULE_PATH),
        "udev_backup": str(udev_backup) if udev_backup else None,
        "xkb_conf": str(XKB_CONF_PATH) if session_info.get("xkb_conf_installed") else None,
        "xkb_backup": str(xkb_backup) if xkb_backup else None,
        "user_added_to_group": username,
        "session_type": session_info.get("session_type"),
        "gnome_settings_modified": session_info.get("gnome_settings_modified", False),
        "kde_config_modified": session_info.get("kde_config_modified", False),
        "sway_config_modified": session_info.get("sway_config_modified", False),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    log(f"Wrote manifest to {MANIFEST_PATH}")


def install() -> None:
    """Main install entry point."""
    if not is_root():
        fail(1, "must run as root (use sudo / apt / dnf / pkexec)")

    username = get_target_user()
    if not username:
        # If running as root directly (no SUDO_USER), skip usermod
        # but still install udev rule + XKB config
        log("WARNING: no target user detected (SUDO_USER / PKEXEC_UID empty) — "
            "skipping usermod. Run 'sudo usermod -aG input <username>' manually.")
        username = "root"

    log(f"Installing Voice Typer keyboard permissions for user '{username}'...")

    # 1. udev rule
    udev_backup = backup_if_exists(UDEV_RULE_PATH)
    install_udev_rule()

    # 2. Add user to input group (skip if root)
    if username != "root":
        add_user_to_input_group(username)

    # 3. Caps Lock neutralization
    session_type = detect_session_type()
    session_info = configure_caps_lock_neutralization(session_type, username)

    # 4. Manifest
    write_manifest(username, session_info, udev_backup, None)

    log("")
    log("Voice Typer keyboard permissions installed successfully.")
    if username != "root":
        log(f"IMPORTANT: user '{username}' must log out and log back in for "
            f"the 'input' group change to take effect.")
    log("")


# ─── Uninstall ─────────────────────────────────────────────────────────────


def uninstall() -> None:
    """Remove all Voice Typer system modifications."""
    if not is_root():
        fail(1, "must run as root to uninstall")

    log("Removing Voice Typer keyboard permissions...")

    # Read the manifest to know what to remove
    manifest = None
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except json.JSONDecodeError:
            log("WARNING: manifest is corrupt — removing known paths unconditionally")

    # Remove udev rule
    if UDEV_RULE_PATH.exists():
        UDEV_RULE_PATH.unlink()
        log(f"Removed {UDEV_RULE_PATH}")

    # Reload udev
    try:
        run(["udevadm", "control", "--reload-rules"], check=False)
        run(["udevadm", "trigger", "--subsystem-match=input"], check=False)
    except FileNotFoundError:
        pass

    # Remove XKB conf
    if XKB_CONF_PATH.exists():
        XKB_CONF_PATH.unlink()
        log(f"Removed {XKB_CONF_PATH}")

    # Restore backups if they exist
    if manifest:
        udev_backup = manifest.get("udev_backup")
        if udev_backup and Path(udev_backup).exists():
            shutil.move(udev_backup, UDEV_RULE_PATH)
            log(f"Restored udev rule backup from {udev_backup}")
        xkb_backup = manifest.get("xkb_backup")
        if xkb_backup and Path(xkb_backup).exists():
            shutil.move(xkb_backup, XKB_CONF_PATH)
            log(f"Restored XKB config backup from {xkb_backup}")

        # Revert GNOME gsettings
        if manifest.get("gnome_settings_modified"):
            username = manifest.get("target_user", "")
            if username and username != "root":
                try:
                    run(["sudo", "-u", username, "gsettings", "reset",
                         "org.gnome.desktop.input-sources", "xkb-options"],
                        check=False)
                    log(f"Reset GNOME xkb-options for user '{username}'")
                except (FileNotFoundError, subprocess.CalledProcessError):
                    pass

        # Note: we don't auto-revert KDE / Sway configs because we'd have
        # to parse them. The user can manually remove the "Voice Typer"
        # marker block. We log a message instead.
        if manifest.get("kde_config_modified"):
            log("NOTE: KDE config (~/.config/kxkbrc) was modified — "
                "remove the 'Options=caps:none' line manually if desired.")
        if manifest.get("sway_config_modified"):
            log("NOTE: Sway config (~/.config/sway/config) was modified — "
                "remove the '# Voice Typer' block manually if desired.")

        # Note: we do NOT remove the user from the input group. Other
        # apps may rely on it, and removing group membership is more
        # disruptive than leaving it. The user can manually run
        # 'sudo gpasswd -d <user> input' if desired.
        log("NOTE: user was added to the 'input' group — not removing "
            "(other apps may rely on it). Run 'sudo gpasswd -d <user> input' "
            "to remove manually if desired.")

    # Remove manifest
    if MANIFEST_PATH.exists():
        MANIFEST_PATH.unlink()
        log(f"Removed {MANIFEST_PATH}")

    log("Uninstall complete.")


# ─── Entry point ───────────────────────────────────────────────────────────


def main() -> None:
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
