"""Container/cgroup detection for Linux deployments."""

from __future__ import annotations

import functools
import os
from pathlib import Path

from voice_typer.server.platform_utils import is_linux

_log = __import__("logging").getLogger(__name__)


# container-signature substrings looked for in ``/proc/1/cgroup``.
_LEGACY_CGROUP_SIGNATURES = ("docker", "lxc", "kubepods", "containerd")


# cgroup signature → human-readable container type. Shared by the single
_CGROUP_TYPE_NAMES = {
    "docker": "docker",
    "lxc": "lxc",
    "kubepods": "kubernetes",
    "containerd": "containerd",
}


def _read_proc_file(path: str) -> str | None:
    """Read a ``/proc`` file as text, returning ``None`` on any I/O error."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _detect_via_mountinfo_overlay() -> bool:
    """detect containers via overlayfs rooted at ``/``.

    Returns ``True`` if a root overlayfs mount is found.
    """
    text = _read_proc_file("/proc/self/mountinfo")
    if not text:
        return False
    for line in text.splitlines():
        fields = line.split()
        # mountinfo layout: id parent dev:major root mount-point ...
        if len(fields) < 10:
            continue
        # Find the ``-`` separator that delimits the optional
        try:
            sep_idx = fields.index("-")
        except ValueError:
            continue
        if sep_idx + 1 >= len(fields):
            continue
        mount_point = fields[4]
        fstype = fields[sep_idx + 1]
        if mount_point == "/" and fstype == "overlay":
            return True
    return False


def _detect_via_proc1_environ() -> bool:
    """detect containers via ``container=`` in ``/proc/1/environ``.

    Returns ``True`` if the ``container=`` variable is set on PID 1.
    """
    text = _read_proc_file("/proc/1/environ")
    if not text:
        return False
    for entry in text.split("\x00"):
        if not entry:
            continue
        # entry is ``KEY=VALUE``; we want KEY == "container".
        if "=" not in entry:
            continue
        key, _value = entry.split("=", 1)
        if key == "container":
            return True
    return False


def is_in_container() -> bool:
    """Detect if the process is running inside a container.

    Returns True if any indicator is positive, False otherwise.
    """
    if _is_in_container_cached.cache_info().currsize > 0 and _should_bypass_cache():
        _is_in_container_cached.cache_clear()
    return _is_in_container_cached()


def _should_bypass_cache() -> bool:
    """Return True when running under pytest (test-isolation bypass)."""
    return os.environ.get("PYTEST_CURRENT_TEST") is not None


def _probe_container() -> str | None:
    """Single canonical container probe.

    Returns a human-readable container type, or ``None`` when not in a
    """
    if not is_linux():
        return None

    # 1. Docker creates /.dockerenv in containers
    if Path("/.dockerenv").exists():
        return "docker"

    # 2. Podman creates /run/.containerenv
    if Path("/run/.containerenv").exists():
        return "podman"

    # 3. systemd-nspawn sets the `container` env var
    env_container = os.environ.get("CONTAINER")
    if env_container:
        return f"systemd-nspawn ({env_container})"

    # 4. Check /proc/1/cgroup for container runtime signatures (cgroup v1
    cgroup = _read_proc_file("/proc/1/cgroup")
    if cgroup:
        for sig in _LEGACY_CGROUP_SIGNATURES:
            if sig in cgroup:
                return _CGROUP_TYPE_NAMES.get(sig, sig)

    # 5. cgroup v2-aware, ``container=`` on PID 1's environ carries the
    environ_text = _read_proc_file("/proc/1/environ")
    if environ_text:
        for entry in environ_text.split("\x00"):
            if not entry or "=" not in entry:
                continue
            key, value = entry.split("=", 1)
            if key == "container" and value:
                return value

    # 6. cgroup v2-aware, overlayfs rooted at ``/`` catches rootless
    if _detect_via_mountinfo_overlay():
        return "container (overlayfs root)"

    return None


def _reset_container_cache() -> None:
    """Test-only: clear the memoized container-detection results."""
    _is_in_container_cached.cache_clear()
    _get_container_type_cached.cache_clear()


def get_container_type() -> str | None:
    """Return a human-readable container type if detected, None otherwise."""
    if not is_in_container():
        return None
    if _should_bypass_cache() and _get_container_type_cached.cache_info().currsize > 0:
        _get_container_type_cached.cache_clear()
    return _get_container_type_cached()


@functools.lru_cache(maxsize=1)
def _is_in_container_cached() -> bool:
    """Memoized boolean body of :func:`is_in_container`."""
    return _probe_container() is not None


@functools.lru_cache(maxsize=1)
def _get_container_type_cached() -> str | None:
    """Memoized type body of :func:`get_container_type`."""
    probe = _probe_container()
    return probe if probe is not None else "unknown"


def warn_if_in_container() -> None:
    """Log a warning if running in a container, listing features that may be unavailable."""
    container_type = get_container_type()
    if container_type is None:
        return

    _log.warning(
        "[CONTAINER] Running inside a %s container. "
        "The following features may be unavailable: "
        "system tray (no D-Bus session), "
        "audio capture (no /dev/snd), "
        "GPU acceleration (no device passthrough), "
        "global hotkeys (no display server). "
        "Dictation via IPC will still work if audio is piped externally.",
        container_type,
    )
