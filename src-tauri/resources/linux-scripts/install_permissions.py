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
  2 = (reserved — historical "no target user" code, now unified with 5)
  3 = udev rule installation failed
  4 = usermod failed
  5 = no target user detected (run via sudo -E / pkexec) OR XKB / session config failed

Note: when this script is invoked via pkexec, polkit caches the
authentication for ~5 minutes (``auth_admin_keep`` default in
``voice-typer.polkit``). Re-running within that window will not re-prompt
for a password — this is expected polkit behavior, not a bug.
"""

from __future__ import annotations

import ast
import configparser
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ``grp`` / ``pwd`` are POSIX-only stdlib modules. This script is
# Linux-only at runtime, but tests import it on all platforms (the
# module is exercised with mocked subprocess / paths on Windows CI
# hosts too), so a bare ``import grp`` would hard-fail collection
# with ModuleNotFoundError on Windows. Guard both imports; every
# function that touches ``grp`` / ``pwd`` is only reachable on
# Linux at runtime (``is_root()`` fails closed on Windows).
try:
    import grp
except ImportError:  # pragma: no cover - POSIX-only module
    grp = None  # type: ignore[assignment]

try:
    import pwd
except ImportError:  # pragma: no cover - POSIX-only module
    pwd = None  # type: ignore[assignment]

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
    # as_posix() so the prefix match is path-separator agnostic — on
    # Windows (tests / cross-dev) Path('/tmp/.mount_...') stringifies
    # with backslashes and would never match the POSIX prefix.
    return resolved.as_posix().startswith(_APPIMAGE_MOUNT_PREFIX)


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


# ─── Option-merge helpers ───────────────────────────────────────────────────
#
# These helpers read the existing Caps Lock XKB options for GNOME / KDE /
# Sway, merge ``caps:none`` in (deduped, order-preserving), and capture
# the original value so the uninstaller can restore it (instead of
# resetting to factory defaults, which would clobber user customization
# like ``altwin:swap_alt_win``).


def _parse_gsettings_array(raw: str) -> list[str]:
    """Parse the output of ``gsettings get`` for an array-of-strings setting.

    ``gsettings get`` returns values in a GVariant-style literal, e.g.:
    - ``@as []`` (empty array of type ``as``)
    - ``['caps:none']``
    - ``['caps:none', 'altwin:swap_alt_win']``

    Returns a plain Python list of strings. Empty / unparseable input
    returns ``[]`` (so the caller can treat "no value" and "empty value"
    identically when merging).
    """
    raw = raw.strip()
    if not raw or raw.startswith("@as"):
        return []
    # GVariant array literals are syntactically compatible with Python
    # list literals — use ast.literal_eval for safe parsing.
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, (list, tuple)):
            return [str(x) for x in parsed]
    except (ValueError, SyntaxError):
        pass
    # Fallback: tokenize on commas and strip quotes.
    body = raw
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    items: list[str] = []
    for token in body.split(","):
        token = token.strip().strip("'\"")
        if token:
            items.append(token)
    return items


def _format_gsettings_array(items: list[str]) -> str:
    """Format a list as a GVariant-style array literal for ``gsettings set``."""
    quoted = ", ".join(f"'{item}'" for item in items)
    return f"[{quoted}]"


def _parse_comma_options(value: str) -> list[str]:
    """Parse a comma-separated XKB options string (kxkbrc / sway)."""
    return [token.strip() for token in value.split(",") if token.strip()]


def _format_comma_options(items: list[str]) -> str:
    """Format a list as a comma-separated XKB options string."""
    return ",".join(items)


def _merge_option(existing: list[str], new: str) -> list[str]:
    """Append ``new`` to ``existing`` (deduped, order-preserving)."""
    result = list(existing)
    if new not in result:
        result.append(new)
    return result


def _find_sway_xkb_options_lines(lines: list[str]) -> list[int]:
    """Return indices of non-commented lines that set ``input * xkb_options``."""
    indices: list[int] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        if (
            len(tokens) >= 4
            and tokens[0] == "input"
            and tokens[1] == "*"
            and tokens[2] == "xkb_options"
        ):
            indices.append(idx)
    return indices


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
            # Read the existing xkb-options value via ``gsettings get``
            # so we can merge caps:none in (deduped) instead of clobbering
            # the user's other XKB options (e.g. altwin:swap_alt_win).
            # Capture the raw original in the manifest so uninstall can
            # restore it via ``gsettings set`` instead of ``gsettings reset``
            # (which would lose user customization that predated Voice Typer).
            get_proc = subprocess.run(
                [
                    "sudo", "-u", username, "gsettings", "get",
                    "org.gnome.desktop.input-sources", "xkb-options",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            original_raw = get_proc.stdout.strip() if get_proc.returncode == 0 else ""
            original_options = _parse_gsettings_array(original_raw)
            merged_options = _merge_option(original_options, "caps:none")
            merged_value = _format_gsettings_array(merged_options)
            run(
                [
                    "sudo", "-u", username, "gsettings", "set",
                    "org.gnome.desktop.input-sources", "xkb-options", merged_value,
                ],
                check=False,
            )
            log(f"Set GNOME xkb-options to {merged_value} for user '{username}'")
            result["gnome_settings_modified"] = True
            # Raw string preserved for round-trip restore via ``gsettings set``.
            result["gnome_xkb_options_original"] = original_raw
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            log(f"WARNING: gsettings failed (non-fatal): {exc}")

    # KDE: write to ~/.config/kxkbrc
    if session_type == "kde":
        try:
            user_pw = pwd.getpwnam(username)
            kxkbrc = Path(user_pw.pw_dir) / ".config" / "kxkbrc"
            kxkbrc.parent.mkdir(parents=True, exist_ok=True)
            # Parse the existing kxkbrc with configparser so we can
            # merge caps:none into the existing ``Options=`` value (deduped)
            # instead of appending a duplicate ``[Layout]`` section. Capture
            # the original Options= string in the manifest so uninstall can
            # restore it (instead of dropping the key entirely).
            existing_text = kxkbrc.read_text() if kxkbrc.exists() else ""
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str  # preserve case (KDE uses Options=, not options=)
            original_options_str = ""
            try:
                parser.read_string(existing_text)
                if parser.has_section("Layout") and parser.has_option("Layout", "Options"):
                    original_options_str = parser.get("Layout", "Options") or ""
            except (configparser.Error, ValueError):
                # File is missing section headers or is otherwise malformed —
                # treat as empty (we'll create a clean [Layout] section).
                if not parser.has_section("Layout"):
                    parser.add_section("Layout")
            existing_options = _parse_comma_options(original_options_str)
            merged_options = _merge_option(existing_options, "caps:none")
            merged_options_str = _format_comma_options(merged_options)
            if not parser.has_section("Layout"):
                parser.add_section("Layout")
            parser.set("Layout", "Options", merged_options_str)
            # Write back merged INI (``space_around_delimiters=False`` matches
            # KDE's ``Options=caps:none`` convention — no spaces around ``=``).
            with kxkbrc.open("w") as f:
                parser.write(f, space_around_delimiters=False)
            shutil.chown(kxkbrc, user_pw.pw_uid, user_pw.pw_gid)
            log(f"Set KDE kxkbrc Options to '{merged_options_str}' at {kxkbrc}")
            result["kde_config_modified"] = True
            result["kde_xkb_options_original"] = original_options_str
        except (KeyError, OSError) as exc:
            log(f"WARNING: KDE config failed (non-fatal): {exc}")

    # Sway: scan ~/.config/sway/config for ``input * xkb_options`` directives
    # and merge caps:none into the existing options (deduped)
    # if found. Replace the line (leaving a commented-out backup of the original).
    # If not found, append a new block. Capture the original line in the
    # manifest so uninstall can restore it.
    if session_type == "sway":
        try:
            user_pw = pwd.getpwnam(username)
            sway_config = Path(user_pw.pw_dir) / ".config" / "sway" / "config"
            sway_config.parent.mkdir(parents=True, exist_ok=True)
            existing = sway_config.read_text() if sway_config.exists() else ""
            lines = existing.splitlines(keepends=True)
            match_indices = _find_sway_xkb_options_lines(lines)
            marker = "# Voice Typer — Caps Lock neutralization"
            restore_marker = "# Voice Typer (original, preserved for restore):"
            if match_indices:
                # Merge caps:none into the FIRST matched line's options.
                first_idx = match_indices[0]
                original_line = lines[first_idx].rstrip()
                # Extract the option-value tail (everything after the third token).
                tail_split = original_line.split(None, 3)
                tail = tail_split[3] if len(tail_split) >= 4 else ""
                existing_options = _parse_comma_options(tail.strip())
                merged_options = _merge_option(existing_options, "caps:none")
                merged_value = _format_comma_options(merged_options)
                new_line = f"input * xkb_options {merged_value}\n"
                # Replace the matched line with a commented-out backup of the
                # original (so the user can see what Voice Typer changed) plus
                # the new merged line.
                lines[first_idx] = (
                    f"{restore_marker} {original_line}\n"
                    f"{new_line}"
                )
                # Drop subsequent duplicate ``input * xkb_options`` lines
                # (Voice Typer consolidates them into the first).
                for idx in reversed(match_indices[1:]):
                    del lines[idx]
                sway_config.write_text("".join(lines))
                log(f"Updated existing sway xkb_options line at {sway_config}")
                result["sway_config_modified"] = True
                result["sway_xkb_options_original"] = original_line
            elif marker not in existing:
                # No existing xkb_options line — append Voice Typer's marker block.
                with sway_config.open("a") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(f"\n{marker}\ninput * xkb_options caps:none\n")
                log(f"Appended caps:none to {sway_config}")
                result["sway_config_modified"] = True
                result["sway_xkb_options_original"] = ""
            else:
                log(f"caps:none already in {sway_config}")
                result["sway_config_modified"] = True
                result["sway_xkb_options_original"] = ""
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
        # Manifest version bumped 1 → 2 when caps-lock originals were added:
        # ``caps_lock_originals`` so the uninstaller can restore the
        # user's prior XKB options instead of resetting them.
        "version": 2,
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
        # Caps Lock XKB-option originals. The uninstaller
        # restores these via ``gsettings set`` / kxkbrc rewrite / sway
        # config rewrite — instead of ``gsettings reset`` (which would lose
        # the user's other XKB options) or "remove the line manually" (which
        # leaves the user to clean up). Empty string = no prior value (the
        # uninstaller removes Voice Typer's added line / key entirely).
        "caps_lock_originals": {
            "gnome_xkb_options": session_info.get("gnome_xkb_options_original", ""),
            "kde_xkb_options": session_info.get("kde_xkb_options_original", ""),
            "sway_xkb_options_line": session_info.get("sway_xkb_options_original", ""),
        },
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
        # Instead of silently continuing with username="root"
        # (which would configure the wrong home dir and skip usermod),
        # fail fast with a clear message explaining the correct
        # invocation. Exit code 5 per the platform exit-code table.
        fail(
            5,
            "no target user detected — run as: sudo -E env PKEXEC_UID=$(id -u) "
            "pkexec /usr/share/voice-typer/scripts/install_permissions.py",
        )
        return  # unreachable — fail() exits; explicit return for type narrowing

    # Explain the polkit auth_admin_keep caching window so users
    # aren't surprised when subsequent pkexec invocations don't re-prompt.
    log(
        "NOTE: when invoked via pkexec, polkit caches authentication for ~5 minutes "
        "(auth_admin_keep). Re-running within that window will not re-prompt for a password."
    )

    log(f"Installing Voice Typer keyboard permissions for user '{username}'...")

    # 1. udev rule
    udev_backup = backup_if_exists(UDEV_RULE_PATH)
    install_udev_rule()

    # 2. Add user to input group.
    #    The no-user branch fails fast — username is
    #    guaranteed to be a real user here (never "root").
    add_user_to_input_group(username)

    # 3. Caps Lock neutralization
    session_type = detect_session_type()
    session_info = configure_caps_lock_neutralization(session_type, username)

    # 4. Manifest
    write_manifest(username, session_info, udev_backup, None)

    log("")
    log("Voice Typer keyboard permissions installed successfully.")
    log(
        f"IMPORTANT: user '{username}' must log out and log back in for the 'input' group change to take effect."
    )
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


def _restore_gnome_xkb_options(manifest: dict) -> None:
    """Restore the user's original GNOME xkb-options.

    At install time we captured the raw ``gsettings get`` output in
    ``manifest["caps_lock_originals"]["gnome_xkb_options"]``. This helper
    re-applies that value via ``gsettings set`` (round-trip restore) —
    instead of ``gsettings reset``, which would lose user customization
    that predated Voice Typer (e.g. ``altwin:swap_alt_win``).

    If no prior value was saved (empty string), falls back to
    ``gsettings reset`` (returns the setting to its factory default).
    """
    username = manifest.get("target_user", "")
    if not username or username == "root":
        return
    originals = manifest.get("caps_lock_originals", {}) or {}
    original_raw = originals.get("gnome_xkb_options", "")
    try:
        if original_raw:
            run(
                [
                    "sudo", "-u", username, "gsettings", "set",
                    "org.gnome.desktop.input-sources", "xkb-options", original_raw,
                ],
                check=False,
            )
            log(f"Restored GNOME xkb-options to '{original_raw}' for user '{username}'")
        else:
            run(
                [
                    "sudo", "-u", username, "gsettings", "reset",
                    "org.gnome.desktop.input-sources", "xkb-options",
                ],
                check=False,
            )
            log(f"Reset GNOME xkb-options for user '{username}' (no prior value saved)")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        log(f"WARNING: GNOME gsettings restore failed (non-fatal): {exc}")


def _restore_kde_kxkbrc_options(manifest: dict) -> None:
    """Restore the user's original KDE kxkbrc ``Options=`` value.

    Re-writes the ``[Layout]`` section's ``Options`` key back to the
    saved value (or removes the key entirely if no prior value was saved).
    """
    username = manifest.get("target_user", "")
    if not username or username == "root":
        return
    originals = manifest.get("caps_lock_originals", {}) or {}
    original_options_str = originals.get("kde_xkb_options", "")
    try:
        user_pw = pwd.getpwnam(username)
    except KeyError:
        log(f"WARNING: cannot resolve home dir for user '{username}' — skipping KDE kxkbrc restore")
        return
    kxkbrc = Path(user_pw.pw_dir) / ".config" / "kxkbrc"
    if not kxkbrc.exists():
        log(f"NOTE: {kxkbrc} no longer exists — skipping KDE kxkbrc restore")
        return
    try:
        existing_text = kxkbrc.read_text()
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        try:
            parser.read_string(existing_text)
        except configparser.Error:
            if not parser.has_section("Layout"):
                parser.add_section("Layout")
        if not parser.has_section("Layout"):
            parser.add_section("Layout")
        if original_options_str:
            parser.set("Layout", "Options", original_options_str)
            log(f"Restored KDE kxkbrc Options to '{original_options_str}' for user '{username}'")
        else:
            if parser.has_option("Layout", "Options"):
                parser.remove_option("Layout", "Options")
                log(f"Removed KDE kxkbrc Options key for user '{username}' (no prior value)")
            else:
                log(f"NOTE: KDE kxkbrc Options key already absent for user '{username}'")
        with kxkbrc.open("w") as f:
            parser.write(f, space_around_delimiters=False)
        shutil.chown(kxkbrc, user_pw.pw_uid, user_pw.pw_gid)
    except OSError as exc:
        log(f"WARNING: KDE kxkbrc restore failed (non-fatal): {exc}")


def _restore_sway_config_options(manifest: dict) -> None:
    """Restore the user's original sway ``input * xkb_options`` line.

    At install time we either:
    1. **Replaced** an existing ``input * xkb_options`` line (saving the
       original to the manifest) and wrote a restore-marker comment above
       the new merged line, OR
    2. **Appended** a new ``# Voice Typer — Caps Lock neutralization``
       marker block + ``input * xkb_options caps:none`` line (no prior
       line existed).

    This helper reverses both:
    - In the replace case, drops the restore-marker comment and replaces
      the merged line with the saved original.
    - In the append case, removes the entire marker block.
    """
    username = manifest.get("target_user", "")
    if not username or username == "root":
        return
    originals = manifest.get("caps_lock_originals", {}) or {}
    original_line = originals.get("sway_xkb_options_line", "")
    try:
        user_pw = pwd.getpwnam(username)
    except KeyError:
        log(f"WARNING: cannot resolve home dir for user '{username}' — skipping sway config restore")
        return
    sway_config = Path(user_pw.pw_dir) / ".config" / "sway" / "config"
    if not sway_config.exists():
        log(f"NOTE: {sway_config} no longer exists — skipping sway config restore")
        return
    try:
        existing = sway_config.read_text()
        lines = existing.splitlines(keepends=True)
        marker = "# Voice Typer — Caps Lock neutralization"
        restore_marker = "# Voice Typer (original, preserved for restore):"
        new_lines: list[str] = []
        i = 0
        restored = False
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # Drop the restore-marker backup comment we wrote at install time.
            if stripped.startswith(restore_marker):
                i += 1
                continue
            # Drop Voice Typer's append-mode marker block (marker + the
            # ``input * xkb_options`` line that follows it).
            if stripped == marker:
                i += 1
                if i < len(lines):
                    nxt = lines[i].split()
                    if (
                        len(nxt) >= 3
                        and nxt[0] == "input"
                        and nxt[1] == "*"
                        and nxt[2] == "xkb_options"
                    ):
                        i += 1
                if original_line and not restored:
                    new_lines.append(original_line + "\n")
                    restored = True
                continue
            # Replace Voice Typer's rewritten ``input * xkb_options`` line
            # with the saved original (replace-mode case — no marker, just
            # the merged line we wrote below the restore-marker comment).
            if (
                not restored
                and len(stripped.split()) >= 3
                and stripped.split()[0] == "input"
                and stripped.split()[1] == "*"
                and stripped.split()[2] == "xkb_options"
            ):
                if original_line:
                    new_lines.append(original_line + "\n")
                # Skip the modified line either way (replaced above or dropped).
                i += 1
                restored = True
                continue
            new_lines.append(line)
            i += 1
        sway_config.write_text("".join(new_lines))
        shutil.chown(sway_config, user_pw.pw_uid, user_pw.pw_gid)
        if original_line:
            log(f"Restored sway xkb_options line for user '{username}'")
        else:
            log(f"Removed Voice Typer sway xkb_options block for user '{username}'")
    except OSError as exc:
        log(f"WARNING: sway config restore failed (non-fatal): {exc}")


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

        # Revert GNOME gsettings: restore the saved original value
        # via ``gsettings set`` instead of ``gsettings reset`` (which would
        # lose user customization that predated Voice Typer).
        if manifest.get("gnome_settings_modified"):
            _restore_gnome_xkb_options(manifest)

        # Revert KDE kxkbrc Options=: restore the saved original
        # value via configparser rewrite (instead of telling the user to
        # remove the line manually).
        if manifest.get("kde_config_modified"):
            _restore_kde_kxkbrc_options(manifest)

        # Revert sway config: restore the saved original line via
        # config rewrite (instead of telling the user to remove the
        # ``# Voice Typer`` block manually).
        if manifest.get("sway_config_modified"):
            _restore_sway_config_options(manifest)

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
