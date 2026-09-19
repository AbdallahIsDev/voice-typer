"""Linux filesystem (``/dev/input/event*``) permission probe for the"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

import voice_typer.server.permissions as _p

log = logging.getLogger("voice_typer.server.permissions")


def _check_linux_input_access() -> _p.PermissionState:
    """Check whether the current user can read /dev/input/event* devices.

    Returns ``GRANTED`` if the user is in the ``input`` group AND at
    """
    # Check group membership
    try:
        import grp

        input_group = grp.getgrnam("input")
        username = os.environ.get("USER") or os.environ.get("LOGNAME", "")
        if username and username not in input_group.gr_mem:
            # Also check the current process's supplementary groups
            groups = os.getgroups()
            if input_group.gr_gid not in groups:
                return _p.PermissionState.DENIED
    except (KeyError, OSError):
        # 'input' group doesn't exist on this system, definitely denied
        return _p.PermissionState.DENIED

    # Check that at least one event device is readable
    try:
        import glob

        devices = glob.glob("/dev/input/event*")
        if not devices:
            # No devices at all, can't tell (headless? container?)
            return _p.PermissionState.UNKNOWN
        for dev in devices:
            if os.access(dev, os.R_OK):
                return _p.PermissionState.GRANTED
        return _p.PermissionState.DENIED
    except OSError:
        return _p.PermissionState.UNKNOWN


def _open_linux_pkexec_prompt() -> None:
    """Run install_permissions.py via pkexec to grant keyboard permission."""
    # Find the install_permissions.py script
    install_script = _p._find_linux_install_script()
    if install_script is None:
        log.error(
            "[PERMISSION] install_permissions.py not found, "
            "cannot auto-grant Linux keyboard permission. "
            "Run scripts/linux/install_permissions.py manually as root."
        )
        return

    # Try pkexec first (modern Linux, GUI prompt via polkit).
    if shutil.which("pkexec"):
        try:
            subprocess.Popen(
                ["pkexec", str(install_script)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("[PERMISSION] Launched pkexec to install Linux permissions")
            return
        except OSError as exc:
            log.warning("[PERMISSION] pkexec failed: %s, trying fallbacks", exc)

    # Fallback: gksu (deprecated but still present on some systems)
    if shutil.which("gksu"):
        try:
            subprocess.Popen(
                ["gksu", f"{sys.executable} {install_script}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("[PERMISSION] Launched gksu to install Linux permissions")
            return
        except OSError as exc:
            log.debug("[PERMISSION] gksu launch failed: %s", exc, exc_info=True)

    # Fallback: kdesu (KDE)
    if shutil.which("kdesu"):
        try:
            subprocess.Popen(
                ["kdesu", "-t", "--", sys.executable, str(install_script)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("[PERMISSION] Launched kdesu to install Linux permissions")
            return
        except OSError as exc:
            log.debug("[PERMISSION] kdesu launch failed: %s", exc, exc_info=True)

    # Last resort: tell the user to run it manually in a terminal
    log.error(
        "[PERMISSION] No GUI sudo helper found (pkexec/gksu/kdesu). Please run: sudo %s %s",
        sys.executable,
        install_script,
    )


def _find_linux_install_script():
    """Find scripts/linux/install_permissions.py."""
    from pathlib import Path

    candidates = [
        # Dev mode: voice_typer/server/permissions/__init__.py →
        Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "linux" / "install_permissions.py",
        # Installed package (deb/rpm)
        Path("/usr/share/voice-typer/scripts/install_permissions.py"),
        # PyInstaller bundle
        Path(sys.executable).resolve().parent / "scripts" / "linux" / "install_permissions.py",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None
