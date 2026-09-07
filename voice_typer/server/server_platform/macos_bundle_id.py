"""Resolve the macOS bundle identifier of the host app at runtime.

The Python backend runs as a child process of the desktop host — the
Electron app or the Tauri host — which on macOS is a ``*.app`` bundle.
The Accessibility re-grant notification (``startup_tasks.py``) tells the
user to run ``tccutil reset Accessibility <bundle-id>``, and that bundle
ID must be the REAL one of the currently-running host. Instead of
hardcoding a bundle identifier, this module walks the parent-process
chain from the backend up to the nearest ``*.app`` bundle and reads
``CFBundleIdentifier`` from its ``Contents/Info.plist`` — so both the
Electron and Tauri builds (and any future bundle-identifier change) show
the correct ``tccutil`` command without code edits.

Resolution is best-effort: dev-mode runs (launched from a terminal with
no ``.app`` in the process chain) and non-macOS hosts return ``None`` so
callers can fall back to a generic message instead of showing a wrong
``tccutil`` command.

The Linux sibling of this walk lives in
:mod:`voice_typer.server.server_platform.linux_proc_walk` (same bounded
chain-walk semantics against the real ``/proc`` tree; Linux has no
``.app`` bundles, so its detection is a documented no-op).

This module is also the single source of truth for ``tccutil`` command
construction (TCC-002 — see :func:`tccutil_reset_command`): every
consumer that needs a ``tccutil reset Accessibility <bundle-id>`` string
or argv list MUST go through the helpers below so a future change
(service naming, extra flags, ``tccutil`` replacement) lands in one
place.
"""

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

    TCC-002: the single source of truth for ``tccutil`` command
    construction. Every consumer — the a11y re-grant notification
    (``startup_tasks.py``), the onboarding walkthrough commands
    (``onboarding.py``), the ``reset_macos_accessibility`` IPC handler
    (``system_handlers.py``) and the permission tray notification
    (``permissions/checker.py``) — MUST build the command through this
    helper so a future change (service naming, extra flags, a
    ``tccutil`` replacement) lands in exactly one place.

    ``service`` is a TCC service name (``"Accessibility"``,
    ``"Microphone"``, ...); ``bundle_id`` is the host app's
    ``CFBundleIdentifier`` (normally runtime-resolved via
    :func:`resolve_host_bundle_id` — never hardcoded).

    Returns the argv list form (for ``subprocess``-style APIs).
    """
    return ["tccutil", "reset", service, bundle_id]


def tccutil_reset_command_str(service: str, bundle_id: str) -> str:
    """Return the ``tccutil`` reset command as a single-line shell string.

    TCC-002: mirrors :func:`tccutil_reset_command` in string form for
    user-facing messages — e.g. ``"tccutil reset Accessibility
    com.example.app"`` (with spaces in the bundle id preserved). The
    two forms must stay in lockstep; both derive from
    :func:`tccutil_reset_command` so there is exactly one construction
    point.
    """
    return " ".join(tccutil_reset_command(service, bundle_id))


def _process_chain_line(pid: int) -> str:
    """Run ``ps -p <pid> -o ppid= -o comm=``; return the raw line (or "").

    Output format: ``<PPID> <executable path>`` (headers suppressed by
    the ``=`` suffix). ``comm`` (not ``command``) is used deliberately:
    it is a single column containing the executable PATH, so a bundle
    path like ``/Applications/Voice Typer.app/Contents/MacOS/Voice Typer``
    survives with its spaces intact (``command`` would need argv
    tokenization, which breaks on spaces in the path). Returns "" on any
    failure so the walk treats the process as unresolvable and stops.
    """
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
    """Return the ``*.app`` bundle root for an executable path, or ``None``.

    e.g. ``/Applications/Voice Typer.app/Contents/MacOS/Voice Typer``
    -> ``Path("/Applications/Voice Typer.app")``
    """
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

    macOS-only (returns ``None`` on other platforms). Walks the
    parent-process chain (bounded by ``_MAX_CHAIN_DEPTH``) looking for
    the nearest ancestor whose executable lives inside a ``*.app``
    bundle, then reads ``CFBundleIdentifier`` from that bundle's
    ``Contents/Info.plist``.

    Returns ``None`` when the bundle ID cannot be determined (non-macOS
    host, dev-mode run without an ``.app`` in the chain, missing /
    unreadable Info.plist) — callers fall back to a generic re-grant
    message in that case rather than showing a wrong ``tccutil`` command.
    """
    if not is_macos():
        return None
    return _resolve_host_bundle_id()


def _resolve_host_bundle_id(start_pid: int | None = None) -> str | None:
    """Walk the process chain (``start_pid`` injectable for tests).

    ``start_pid`` defaults to the backend's own parent
    (``os.getppid()``) — the host app in both the Electron and Tauri
    spawn paths.
    """
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
        # everything after the PPID is the exe path — no tokenization.
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
