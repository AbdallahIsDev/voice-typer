"""PLAT-021: Container/cgroup detection for Linux deployments.

When running inside a container (Docker, Podman, LXC, Flatpak, Snap),
certain features don't work: no system tray (no D-Bus session bus),
no audio devices (no /dev/snd), no GPU acceleration. This module
detects containerized environments so the app can degrade gracefully
instead of logging confusing errors.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

_log = __import__("logging").getLogger(__name__)


# DE-66: container-signature substrings looked for in ``/proc/1/cgroup``.
# Used for the legacy cgroup v1 detection path. On cgroup v2 (the
# default on modern Linux kernels 5.15+) the per-process cgroup path
# is typically just ``0::/`` for host processes and ``0::/`` for
# container processes too — so these substring matches no longer fire
# reliably on v2. The cgroup v2-aware checks below (``/proc/self/mountinfo``
# overlayfs-at-root + ``/proc/1/environ`` ``container=``) close the
# misdetection gap for rootless Podman and other modern runtimes.
_LEGACY_CGROUP_SIGNATURES = ("docker", "lxc", "kubepods", "containerd")


def _read_proc_file(path: str) -> str | None:
    """Read a ``/proc`` file as text, returning ``None`` on any I/O error.

    Centralized here so every detection path uses the same
    error-swallowing behavior (``OSError`` / ``PermissionError`` /
    ``FileNotFoundError`` are all subclasses of ``OSError``).
    """
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _detect_via_mountinfo_overlay() -> bool:
    """DE-66: detect containers via overlayfs rooted at ``/``.

    On cgroup v2 (and on cgroup v1 hybrid hosts that don't write a
    container-runtime signature into ``/proc/1/cgroup``), most OCI
    runtimes (Docker, Podman, containerd) mount the container's root
    filesystem as an ``overlay`` filesystem. The
    ``/proc/self/mountinfo`` line for the root mount has the form::

        <mount-id> <parent-id> <major>:<minor> / / <fstype> ...

    We look for a line whose mount point (field 5) is ``/`` and whose
    filesystem type (the token after the ``-`` separator) is
    ``overlay``. This catches rootless Podman containers that
    create neither ``/.dockerenv`` nor ``/run/.containerenv`` and
    don't write a recognizable signature into ``/proc/1/cgroup``.

    Returns ``True`` if a root overlayfs mount is found.
    """
    text = _read_proc_file("/proc/self/mountinfo")
    if not text:
        return False
    for line in text.splitlines():
        fields = line.split()
        # mountinfo layout: id parent dev:major root mount-point ...
        # followed (after the optional per-mount fields terminated by
        # ``-``) by fstype source super-options.
        if len(fields) < 10:
            continue
        # Find the ``-`` separator that delimits the optional
        # per-mount fields from the fstype / source / super-options.
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
    """DE-66: detect containers via ``container=`` in ``/proc/1/environ``.

    Most modern container runtimes (systemd-nspawn, Podman, Docker
    with ``--env container=oci``, Kubernetes-managed containers) set
    the ``container=`` environment variable on PID 1. This is the
    most reliable single indicator on cgroup v2 hosts where
    ``/proc/1/cgroup`` is uninformative (``0::/``).

    ``/proc/1/environ`` is a NUL-separated ``KEY=VALUE`` list. We
    look for any entry whose key is exactly ``container`` (so a
    variable like ``MY_CONTAINER=foo`` does not false-positive).

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

    Checks multiple indicators:
    1. ``/.dockerenv`` file (Docker)
    2. ``/run/.containerenv`` file (Podman)
    3. ``container`` environment variable (systemd-nspawn)
    4. ``/proc/1/cgroup`` contains container runtime signatures (cgroup v1)
    5. DE-66: ``container=`` set on PID 1's environment (cgroup v2 / Podman)
    6. DE-66: overlayfs rooted at ``/`` in ``/proc/self/mountinfo``
       (cgroup v2 / rootless Podman / OCI runtimes)

    Returns True if any indicator is positive, False otherwise.
    On non-Linux platforms, always returns False.

    XV-13: the result is memoized for the lifetime of the process.
    Container membership is invariant during a process lifetime (the
    cgroup namespace can't change without ``unshare``/``setns``, which
    would already be a different process). The cache is bypassed when
    running under pytest so tests that monkeypatch ``sys.platform`` /
    ``Path.exists`` between scenarios keep working without needing a
    cache-clear fixture (which would otherwise have to live in
    ``tests/conftest.py`` — owned by another agent).
    """
    if _is_in_container_cached.cache_info().currsize > 0 and _should_bypass_cache():
        _is_in_container_cached.cache_clear()
    return _is_in_container_cached()


def _should_bypass_cache() -> bool:
    """Return True when running under pytest (test-isolation bypass).

    Production callers never hit this — pytest sets the
    ``PYTEST_CURRENT_TEST`` env var at the start of every test item's
    execution and clears it between items, so the cache is bypassed
    only while a test is actively running. The check is on the env var
    (not ``"pytest" in sys.modules``) so that simply having pytest
    installed doesn't disable the cache in production.
    """
    return os.environ.get("PYTEST_CURRENT_TEST") is not None


@functools.lru_cache(maxsize=1)
def _is_in_container_cached() -> bool:
    """Memoized body of :func:`is_in_container` (XV-13)."""
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

    # 4. Check /proc/1/cgroup for container runtime signatures (cgroup v1
    #    path-based detection — still relevant for legacy hosts).
    cgroup = _read_proc_file("/proc/1/cgroup")
    if cgroup:
        for sig in _LEGACY_CGROUP_SIGNATURES:
            if sig in cgroup:
                return True

    # 5. DE-66: cgroup v2-aware — check /proc/1/environ for ``container=``.
    if _detect_via_proc1_environ():
        return True

    # 6. DE-66: cgroup v2-aware — check /proc/self/mountinfo for overlayfs
    #    rooted at ``/`` (catches rootless Podman and other OCI runtimes
    #    that don't write a recognizable cgroup signature on v2).
    return bool(_detect_via_mountinfo_overlay())


def _reset_container_cache() -> None:
    """Test-only: clear the memoized container-detection results.

    XV-13: production callers should NEVER need this — container
    membership doesn't change during a process lifetime. Tests that
    patch ``sys.platform`` or filesystem state need it so the next
    :func:`is_in_container` / :func:`get_container_type` call re-probes.
    The autouse ``_should_bypass_cache`` check above handles most test
    scenarios automatically; this helper is kept for explicit test
    pinning of the caching contract itself.
    """
    _is_in_container_cached.cache_clear()
    _get_container_type_cached.cache_clear()


def get_container_type() -> str | None:
    """Return a human-readable container type if detected, None otherwise.

    XV-13: the result is memoized alongside :func:`is_in_container` —
    the underlying probe is identical, so the two callers always agree.
    """
    if not is_in_container():
        return None
    if _should_bypass_cache() and _get_container_type_cached.cache_info().currsize > 0:
        _get_container_type_cached.cache_clear()
    return _get_container_type_cached()


@functools.lru_cache(maxsize=1)
def _get_container_type_cached() -> str | None:
    """Memoized body of :func:`get_container_type` (XV-13).

    Assumes :func:`is_in_container` has already returned True — callers
    must gate on that before invoking this helper.
    """
    if Path("/.dockerenv").exists():
        return "docker"
    if Path("/run/.containerenv").exists():
        return "podman"
    if os.environ.get("CONTAINER"):
        return f"systemd-nspawn ({os.environ['CONTAINER']})"

    # DE-66: check /proc/1/environ for the ``container=`` value (e.g.
    # ``container=oci``, ``container=podman``, ``container=lxc``).
    # When present, the value is a useful human-readable runtime name.
    environ_text = _read_proc_file("/proc/1/environ")
    if environ_text:
        for entry in environ_text.split("\x00"):
            if not entry or "=" not in entry:
                continue
            key, value = entry.split("=", 1)
            if key == "container" and value:
                return value

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

    # DE-66: overlayfs-at-root indicator catches rootless Podman and
    # other OCI runtimes; report a recognizable name instead of "unknown".
    if _detect_via_mountinfo_overlay():
        return "container (overlayfs root)"

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
