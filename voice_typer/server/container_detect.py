"""PLAT-021: Container/cgroup detection for Linux deployments.

When running inside a container (Docker, Podman, LXC, Flatpak, Snap),
certain features don't work: no system tray (no D-Bus session bus),
no audio devices (no /dev/snd), no GPU acceleration. This module
detects containerized environments so the app can degrade gracefully
instead of logging confusing errors.
"""
from __future__ import annotations

import os
from pathlib import Path

_log = __import__("logging").getLogger(__name__)


def is_in_container() -> bool:
    """Detect if the process is running inside a container.

    Checks multiple indicators:
    1. ``/.dockerenv`` file (Docker)
    2. ``/run/.containerenv`` file (Podman)
    3. ``container`` environment variable (systemd-nspawn)
    4. ``/proc/1/cgroup`` contains container runtime signatures

    Returns True if any indicator is positive, False otherwise.
    On non-Linux platforms, always returns False.
    """
    import sys

    if not sys.platform.startswith("linux"):
        return False

    # 1. Docker creates /.dockerenv in containers
    if Path("/.dockerenv").exists():
        return True

    # 2. Podman creates /run/.containerenv
    if Path("/run/.containerenv").exists():
        return True

    # 3. systemd-nspawn sets the `container` env var
    if os.environ.get("CONTAINER"):
        return True

    # 4. Check /proc/1/cgroup for container runtime signatures
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
        # Docker, Kubernetes, LXC all use cgroup paths containing these strings
        container_signatures = ["docker", "lxc", "kubepods", "containerd"]
        for sig in container_signatures:
            if sig in cgroup:
                return True
    except (OSError, PermissionError):
        pass

    return False


def get_container_type() -> str | None:
    """Return a human-readable container type if detected, None otherwise."""
    if not is_in_container():
        return None

    if Path("/.dockerenv").exists():
        return "docker"
    if Path("/run/.containerenv").exists():
        return "podman"
    if os.environ.get("CONTAINER"):
        return f"systemd-nspawn ({os.environ['CONTAINER']})"

    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
        if "kubepods" in cgroup:
            return "kubernetes"
        if "containerd" in cgroup:
            return "containerd"
        if "lxc" in cgroup:
            return "lxc"
    except (OSError, PermissionError):
        pass

    return "unknown"


def warn_if_in_container() -> None:
    """Log a warning if running in a container, listing features that may be unavailable."""
    container_type = get_container_type()
    if container_type is None:
        return

    _log.warning(
        "[PLAT-021] Running inside a %s container. "
        "The following features may be unavailable: "
        "system tray (no D-Bus session), "
        "audio capture (no /dev/snd), "
        "GPU acceleration (no device passthrough), "
        "global hotkeys (no display server). "
        "Dictation via IPC will still work if audio is piped externally.",
        container_type,
    )
