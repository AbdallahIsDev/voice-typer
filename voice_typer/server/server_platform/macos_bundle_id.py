"""Resolve the macOS bundle identifier of the host app at runtime."""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
from pathlib import Path

from voice_typer.server.platform_utils import is_macos
from voice_typer.server.server_platform.platform_flags import _MAX_CHAIN_DEPTH

log = logging.getLogger(__name__)


def tccutil_reset_command(service: str, bundle_id: str) -> list[str]:
    """Build the ``tccutil reset <service> <bundle-id>`` argv list.

    Returns the argv list form (for ``subprocess``-style APIs).
    """
    return ["tccutil", "reset", service, bundle_id]


def tccutil_reset_command_str(service: str, bundle_id: str) -> str:
    """Return the ``tccutil`` reset command as a single-line shell string."""
    return " ".join(tccutil_reset_command(service, bundle_id))


def _process_chain_line(pid: int) -> str:
    """Run ``ps -p <pid> -o ppid= -o comm=``; return the raw line (or "")."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid=", "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def app_bundle_root(exe_path: str) -> Path | None:
    """Return the ``*.app`` bundle root for an executable path, or ``None``."""
    path = Path(exe_path)
    for i, part in enumerate(path.parts):
        if part.endswith(".app"):
            return Path(*path.parts[: i + 1])
    return None


def read_bundle_identifier(bundle_root: Path) -> str | None:
    """Read ``CFBundleIdentifier`` from the bundle's ``Contents/Info.plist``."""
    plist_path = bundle_root / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as fh:
            info = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        log.debug("[BUNDLE-ID] cannot read %s", plist_path, exc_info=True)
        return None
    value = info.get("CFBundleIdentifier")
    return value if isinstance(value, str) and value else None


def resolve_host_bundle_id() -> str | None:
    """Resolve the macOS bundle ID of the host app that launched this backend.

    Returns ``None`` when the bundle ID cannot be determined (non-macOS
    """
    if not is_macos():
        return None
    return _resolve_host_bundle_id()


def _resolve_host_bundle_id(start_pid: int | None = None) -> str | None:
    """Walk the process chain (``start_pid`` injectable for tests)."""
    pid = os.getppid() if start_pid is None else start_pid
    for _ in range(_MAX_CHAIN_DEPTH):
        if pid is None or pid <= 1:
            break
        line = _process_chain_line(pid)
        if not line:
            break
        parts = line.split(None, 1)
        if not parts:
            break
        try:
            parent_pid = int(parts[0])
        except ValueError:
            break
        # ``comm`` is the whole executable path (spaces preserved), so
        exe = parts[1] if len(parts) > 1 else ""
        if exe:
            root = app_bundle_root(exe)
            if root is not None:
                bundle_id = read_bundle_identifier(root)
                if bundle_id:
                    log.info("[BUNDLE-ID] resolved host bundle ID %s from %s", bundle_id, root)
                    return bundle_id
        pid = parent_pid
    log.debug("[BUNDLE-ID] could not resolve host bundle ID (dev-mode run?)")
    return None
