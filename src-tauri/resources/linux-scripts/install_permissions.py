#!/usr/bin/env python3
"""Voice Typer — Linux keyboard permission installer.

This is the single source of truth for "what system modifications does
Voice Typer make on Linux." Called by:

- Debian ``postinst`` (as root, during ``apt install voice-typer``)
- RPM ``%post`` (as root, during ``dnf install voice-typer``)
- AppImage first-run helper (as root, via ``pkexec``)

Operations (all idempotent):
  1. Ensure the polkit-stable path
     ``/usr/share/voice-typer/scripts/install_permissions.py`` resolves
     to this script (symlink for stable installs, copy for AppImage).
     Also installs the polkit policy file to
     ``/usr/share/polkit-1/actions/org.voice-typer.policy`` so
     ``pkexec org.voice-typer.install-permissions`` resolves to the
     custom authentication prompt. This is a defensive fallback for
     AppImage installs (which have no ``postinst``) and for repair
     scenarios.
  2. Copy ``99-voice-typer.rules`` to ``/etc/udev/rules.d/``
  3. Reload udev rules and trigger input subsystem
  4. Add the current user (from SUDO_USER / PKEXEC_UID) to the ``input`` group
  5. Detect session type (X11 / GNOME / KDE / Sway) and configure Caps Lock
     neutralization appropriately
  6. Write a manifest at ``/var/lib/voice-typer/permissions-manifest.json``
     tracking what was installed (used by uninstall_permissions.py)

Usage:
  python3 install_permissions.py [--uninstall]
  python3 install_permissions.py --setup-system-paths

The ``--setup-system-paths`` flag performs ONLY the polkit-stable path
setup (step 1) and exits — it does not install udev rules, add the user
to the input group, or write a manifest. Used by the AppImage first-run
helper to register the polkit action before the user clicks "Grant
permission" in the onboarding flow.

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

# Root directory scanned as a defensive fallback when removing the
# per-user autostart ``.desktop`` file during uninstall ().
# Defaults to ``/home`` (typical Linux multi-user layout); tests
# monkeypatch this constant to redirect the scan at a temp dir.
HOME_ROOT_SCAN = Path("/home")

# Filename of the per-user autostart entry that Voice Typer creates
# (via the OS's autostart mechanism, not by this script). On uninstall
# we remove it so the DE doesn't keep trying to launch the (now-deleted)
# binary on every login.
AUTOSTART_DESKTOP_NAME = "voice-typer.desktop"

INPUT_GROUP = "input"

# Polkit-stable path: the polkit policy hard-codes this absolute path
# (see ``voice-typer.polkit``) because polkit requires an absolute,
# stable path that does not change across AppImage versions or
# .deb / .rpm upgrades. Tauri v2 installs the script at a different
# physical path (``/usr/lib/voice-typer/resources/linux-scripts/`` for
# .deb / .rpm, or inside the AppImage squashfs mount for AppImage), so
# this script self-installs a symlink (or copy, for AppImage) at the
# polkit-stable path on every invocation. The Debian / RPM ``postinst``
# also installs this symlink; the self-install here is a defensive
# fallback for AppImage installs (which have no ``postinst``) and for
# repair scenarios (e.g. the symlink was deleted manually).
POLKIT_STABLE_DIR = Path("/usr/share/voice-typer/scripts")
POLKIT_STABLE_PATH = POLKIT_STABLE_DIR / "install_permissions.py"

# The polkit policy file. Installed to the canonical polkit actions
# directory so ``pkexec org.voice-typer.install-permissions`` resolves
# to the custom authentication prompt (instead of the generic pkexec
# prompt). The Debian / RPM postinst installs this via the package
# manager; AppImage installs require this script to install it.
POLKIT_POLICY_SOURCE = Path(__file__).resolve().parent / "voice-typer.polkit"
POLKIT_POLICY_DEST = Path("/usr/share/polkit-1/actions/org.voice-typer.policy")

# AppImage squashfs mounts under ``/tmp/.mount_<name><rand>/``. The
# mount is ephemeral — it disappears when the AppImage process exits.
# A symlink to a path inside the mount would dangle after exit, so for
# AppImage runs we COPY the script to the polkit-stable path instead
# of symlinking. The copy is stable across AppImage launches.
_APPIMAGE_MOUNT_PREFIX = "/tmp/.mount_"


# ─── Helpers ───────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    """Print to stdout (captured by package managers / pkexec)."""
    print(f"[voice-typer-permissions] {msg}", flush=True)


def fail(code: int, msg: str) -> None:  # noqa: ANN401
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


def _is_running_from_appimage() -> bool:
    """Return True iff ``__file__`` resolves to a path inside an AppImage mount.

    AppImage squashfs mounts under ``/tmp/.mount_<name><rand>/``. The mount
    is ephemeral (disappears when the AppImage exits), so a symlink into
    the mount would dangle after exit. ``setup_polkit_stable_path`` uses
    this check to decide between a symlink (stable install path) and a
    copy (AppImage mount path).
    """
    try:
        resolved = Path(__file__).resolve()
    except OSError:
        return False
    return str(resolved).startswith(_APPIMAGE_MOUNT_PREFIX)


def _install_polkit_policy() -> None:
    """Install the polkit policy file to the canonical polkit actions dir.

    Idempotent: skips if the destination already matches the source
    (byte-identical). Only runs if the source policy file exists alongside
    this script (it is bundled as a Tauri resource sibling in
    ``src-tauri/resources/linux-scripts/``).
    """
    if not POLKIT_POLICY_SOURCE.is_file():
        log(f"WARNING: polkit policy source not found at {POLKIT_POLICY_SOURCE} — skipping polkit policy install")
        return

    # Idempotent: skip if destination already matches source.
    if POLKIT_POLICY_DEST.is_file():
        try:
            if POLKIT_POLICY_DEST.read_bytes() == POLKIT_POLICY_SOURCE.read_bytes():
                return  # Already up to date — no-op.
        except OSError:
            pass  # Fall through to overwrite.

    try:
        POLKIT_POLICY_DEST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(POLKIT_POLICY_SOURCE, POLKIT_POLICY_DEST)
        POLKIT_POLICY_DEST.chmod(0o644)
        log(f"Installed polkit policy to {POLKIT_POLICY_DEST}")
    except OSError as exc:
        log(f"WARNING: failed to install polkit policy (non-fatal): {exc}")


def setup_polkit_stable_path() -> None:
    """Ensure the polkit-stable path resolves to this script.

    The polkit policy (``voice-typer.polkit``) hard-codes
    ``/usr/share/voice-typer/scripts/install_permissions.py`` as the
    ``org.freedesktop.policykit.exec.path`` annotation. Polkit requires
    an absolute, stable path — it does not follow symlinks at invoke
    time, but the path must EXIST when ``pkexec
    org.voice-typer.install-permissions`` is invoked.

    For Debian / RPM installs, the package's ``postinst`` creates a
    symlink at the polkit-stable path pointing to the actually-installed
    script (under ``/usr/lib/voice-typer/resources/linux-scripts/``).
    This function is a defensive fallback for:

    1. **AppImage installs** — no ``postinst`` runs, so the polkit-stable
       path is never created. We COPY this script to the polkit-stable
       path (a symlink would dangle after the AppImage is unmounted).
    2. **Repair scenarios** — the symlink was deleted manually, or a
       previous ``postinst`` failed before the symlink step.
    3. **Dev / manual runs** — running the script directly from the
       source tree (e.g. ``sudo python3 scripts/linux/install_permissions.py``).

    The function is idempotent: re-running it does not clobber an
    existing regular file at the polkit-stable path that is already
    up to date. It also installs the polkit policy file to the canonical
    polkit actions directory (idempotent).

    Must be called as root (the polkit-stable path is under
    ``/usr/share/``). Non-root callers should be screened out before
    invocation — this function logs a warning and returns if not root.
    """
    if not is_root():
        log("WARNING: setup_polkit_stable_path() called as non-root — skipping")
        return

    this_script = Path(__file__).resolve()

    # If we're already running from the polkit-stable path, no-op
    # (the polkit policy already resolves to us).
    try:
        if this_script.samefile(POLKIT_STABLE_PATH):
            _install_polkit_policy()
            return
    except FileNotFoundError:
        pass  # polkit-stable path doesn't exist yet — fall through.

    POLKIT_STABLE_DIR.mkdir(parents=True, exist_ok=True)

    if _is_running_from_appimage():
        # AppImage mount is ephemeral — copy the script so the
        # polkit-stable path keeps resolving after the AppImage exits.
        try:
            # Don't clobber a newer copy (e.g. a re-run after upgrade).
            if POLKIT_STABLE_PATH.is_file():
                try:
                    if POLKIT_STABLE_PATH.read_bytes() == this_script.read_bytes():
                        log(f"Polkit-stable copy already up to date at {POLKIT_STABLE_PATH}")
                        _install_polkit_policy()
                        return
                except OSError:
                    pass  # Fall through to overwrite.
            shutil.copy2(this_script, POLKIT_STABLE_PATH)
            POLKIT_STABLE_PATH.chmod(0o755)
            log(f"Installed polkit-stable copy: {POLKIT_STABLE_PATH} (copied from {this_script})")
        except OSError as exc:
            log(f"WARNING: failed to install polkit-stable copy (non-fatal): {exc}")
    else:
        # Stable install path — symlink (matches postinst behavior).
        # Use ``ln -sfn`` semantics: force, symbolic, no-deref so
        # re-runs and upgrades don't leave dangling links.
        try:
            # If the polkit-stable path is a regular file (e.g. legacy
            # Electron install physically placed the script there),
            # don't clobber it — leave the existing regular file in
            # place. This matches the postinst's guard.
            if POLKIT_STABLE_PATH.is_file() and not POLKIT_STABLE_PATH.is_symlink():
                log(f"Polkit-stable path {POLKIT_STABLE_PATH} is a regular file — not clobbering")
            else:
                # Create or update the symlink.
                if POLKIT_STABLE_PATH.is_symlink():
                    current_target = os.readlink(POLKIT_STABLE_PATH)
                    if current_target == str(this_script):
                        log(f"Polkit-stable symlink already points to {this_script}")
                        _install_polkit_policy()
                        return
                    # Unlink the old symlink before re-creating.
                    POLKIT_STABLE_PATH.unlink()
                os.symlink(str(this_script), POLKIT_STABLE_PATH)
                log(f"Installed polkit-stable symlink: {POLKIT_STABLE_PATH} -> {this_script}")
        except OSError as exc:
            log(f"WARNING: failed to install polkit-stable symlink (non-fatal): {exc}")

    # Install / refresh the polkit policy file alongside the symlink.
    _install_polkit_policy()


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
                    ["sudo", "-u", target, "systemctl", "--user", "show-property", "SessionType"],
                    capture_output=True,
                    text=True,
                    timeout=5,
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
            run(
                [
                    "sudo",
                    "-u",
                    username,
                    "gsettings",
                    "set",
                    "org.gnome.desktop.input-sources",
                    "xkb-options",
                    "['caps:none']",
                ],
                check=False,
            )
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
        log(
            "WARNING: unsupported Wayland compositor — Caps Lock neutralization skipped. "
            "Use a non-Caps-Lock hotkey (e.g. Alt) or configure your compositor manually."
        )

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

    # 0. Ensure the polkit-stable path resolves to this script. This is
    # a defensive fallback for AppImage installs (no postinst runs to
    # create the symlink) and for repair scenarios (symlink deleted
    # manually). For Debian / RPM installs, the postinst already
    # created the symlink — this is an idempotent no-op.
    setup_polkit_stable_path()

    username = get_target_user()
    if not username:
        # If running as root directly (no SUDO_USER), skip usermod
        # but still install udev rule + XKB config
        log(
            "WARNING: no target user detected (SUDO_USER / PKEXEC_UID empty) — "
            "skipping usermod. Run 'sudo usermod -aG input <username>' manually."
        )
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
        log(f"IMPORTANT: user '{username}' must log out and log back in for the 'input' group change to take effect.")
    log("")


# Autostart .desktop cleanup () ─────────────────────────────────


def _unlink_autostart_desktop_at(home_dir: Path) -> None:
    """Remove the per-user autostart ``.desktop`` file under ``home_dir``.

    Looks for ``<home_dir>/.config/autostart/voice-typer.desktop`` and
    unlinks it if present. Silent no-op if absent. Logs a non-fatal
    warning on ``OSError`` (e.g. permission denied — common when the
    uninstaller runs as root but a home dir is owned by a service
    account whose ``.config`` is mode 0700).

    Called by ``_remove_autostart_desktop`` once per candidate home
    directory (the manifest's ``target_user`` plus every entry under
    ``HOME_ROOT_SCAN`` as a defensive fallback).
    """
    desktop_path = home_dir / ".config" / "autostart" / AUTOSTART_DESKTOP_NAME
    try:
        if not desktop_path.exists():
            return
    except (OSError, PermissionError) as exc:
        # ``Path.exists()`` typically returns False on EACCES, but
        # some platforms surface the error. Treat as "not removable"
        # and continue with the rest of the scan.
        log(f"WARNING: cannot stat autostart .desktop at {desktop_path} (skipping): {exc}")
        return
    try:
        desktop_path.unlink()
        log(f"Removed autostart .desktop at {desktop_path}")
    except OSError as exc:
        log(f"WARNING: failed to remove autostart .desktop at {desktop_path}: {exc}")


def _remove_autostart_desktop(target_user: str) -> None:
    """Remove the per-user autostart ``.desktop`` file for ``target_user``.

    Resolves the user's home directory via ``pwd.getpwnam`` and unlinks
    the ``.desktop`` file. As a defensive fallback for multi-user
    systems (and for cases where ``target_user`` is empty, ``"root"``,
    or unknown), also scans ``HOME_ROOT_SCAN/*`` (defaults to ``/home/*``)
    and removes any stray ``voice-typer.desktop`` files.

    Behaviour:
    - ``target_user`` empty or ``"root"`` → skip ``pwd.getpwnam``
      lookup (root doesn't have a per-user autostart entry; an empty
      value means the manifest was missing or had no ``target_user``).
    - ``pwd.getpwnam`` raises ``KeyError`` → log a non-fatal warning
      and continue to the ``HOME_ROOT_SCAN`` fallback scan.
    - Fallback scan: iterates ``HOME_ROOT_SCAN/*``, skips entries
      whose ``is_dir()`` raises ``PermissionError`` / ``OSError``
      (e.g. a service-account home dir we can't read).

    All errors are non-fatal — the uninstaller must not abort the
    rest of the cleanup just because one user's ``.desktop`` file
    couldn't be removed.
    """
    if target_user and target_user != "root":
        try:
            pw = pwd.getpwnam(target_user)
        except KeyError:
            log(f"WARNING: cannot resolve home dir for user '{target_user}' — relying on /home scan")
        else:
            _unlink_autostart_desktop_at(Path(pw.pw_dir))

    # Defensive fallback: scan HOME_ROOT_SCAN/* for stray .desktop files.
    # This catches cases where target_user was empty / "root" / unknown,
    # the user was deleted, or the manifest was missing.
    try:
        if not HOME_ROOT_SCAN.is_dir():
            return
    except (OSError, PermissionError):
        return
    try:
        for entry in HOME_ROOT_SCAN.iterdir():
            try:
                if not entry.is_dir():
                    continue
            except (OSError, PermissionError) as exc:
                log(f"WARNING: cannot stat {entry} during /home scan (skipping): {exc}")
                continue
            _unlink_autostart_desktop_at(entry)
    except (OSError, PermissionError) as exc:
        log(f"WARNING: failed to scan {HOME_ROOT_SCAN} for stray .desktop files: {exc}")


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

    # remove the per-user autostart .desktop entry so the DE
    # doesn't keep trying to launch the (now-deleted) binary on every
    # login. Runs after the manifest is read (so we know the
    # ``target_user``) and before backups are restored (so a failure
    # here doesn't skip the rest of the cleanup). Passes ``""`` when
    # the manifest is missing/corrupt — ``_remove_autostart_desktop``
    # then falls back to scanning ``HOME_ROOT_SCAN/*``.
    autostart_target_user = manifest.get("target_user", "") if manifest else ""
    _remove_autostart_desktop(autostart_target_user)

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
                    run(
                        [
                            "sudo",
                            "-u",
                            username,
                            "gsettings",
                            "reset",
                            "org.gnome.desktop.input-sources",
                            "xkb-options",
                        ],
                        check=False,
                    )
                    log(f"Reset GNOME xkb-options for user '{username}'")
                except (FileNotFoundError, subprocess.CalledProcessError):
                    pass

        # Note: we don't auto-revert KDE / Sway configs because we'd have
        # to parse them. The user can manually remove the "Voice Typer"
        # marker block. We log a message instead.
        if manifest.get("kde_config_modified"):
            log(
                "NOTE: KDE config (~/.config/kxkbrc) was modified — "
                "remove the 'Options=caps:none' line manually if desired."
            )
        if manifest.get("sway_config_modified"):
            log(
                "NOTE: Sway config (~/.config/sway/config) was modified — "
                "remove the '# Voice Typer' block manually if desired."
            )

        # Note: we do NOT remove the user from the input group. Other
        # apps may rely on it, and removing group membership is more
        # disruptive than leaving it. The user can manually run
        # 'sudo gpasswd -d <user> input' if desired.
        log(
            "NOTE: user was added to the 'input' group — not removing "
            "(other apps may rely on it). Run 'sudo gpasswd -d <user> input' "
            "to remove manually if desired."
        )

    # Remove manifest
    if MANIFEST_PATH.exists():
        MANIFEST_PATH.unlink()
        log(f"Removed {MANIFEST_PATH}")

    log("Uninstall complete.")


# ─── Entry point ───────────────────────────────────────────────────────────


def main() -> None:
    if "--uninstall" in sys.argv:
        uninstall()
    elif "--setup-system-paths" in sys.argv:
        # Standalone polkit-stable path setup — used by the AppImage
        # first-run helper to register the polkit action without
        # running the full udev / usermod / Caps Lock install.
        if not is_root():
            fail(1, "must run as root (use sudo / pkexec)")
        setup_polkit_stable_path()
        log("System paths setup complete.")
    else:
        install()


if __name__ == "__main__":
    main()
