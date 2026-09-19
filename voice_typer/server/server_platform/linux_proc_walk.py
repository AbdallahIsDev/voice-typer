"""Walk the REAL Linux parent-process chain via ``/proc``."""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from voice_typer.server.platform_utils import is_linux
from voice_typer.server.server_platform.platform_flags import _MAX_CHAIN_DEPTH

log = logging.getLogger(__name__)

# Default ``/proc`` mount point; injectable (via ``proc_root``) for
_PROC_ROOT = Path("/proc")


def _stat_ppid(stat_text: str) -> int | None:
    """Parse the parent PID (field 4) from a ``/proc/<pid>/stat`` line."""
    rparen = stat_text.rfind(")")
    if rparen < 0:
        return None
    fields = stat_text[rparen + 1 :].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _cmdline_exe(cmdline: bytes) -> str | None:
    """Return argv[0] (the executable path) from ``/proc/<pid>/cmdline``."""
    if not cmdline:
        return None
    try:
        argv0 = cmdline.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
    except Exception:  # pragma: no cover - defensive; bytes cannot raise decode errors with replace
        return None
    return argv0 or None


def _read_proc_entry(pid: int, proc_root: str | Path = _PROC_ROOT) -> tuple[int | None, str | None]:
    """Read ``(ppid, argv0)`` for ``pid`` from ``/proc`` (best-effort)."""
    proc = Path(proc_root)
    ppid: int | None = None
    exe: str | None = None
    with contextlib.suppress(OSError):
        ppid = _stat_ppid((proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace"))
    with contextlib.suppress(OSError):
        exe = _cmdline_exe((proc / str(pid) / "cmdline").read_bytes())
    return ppid, exe


def resolve_linux_host_bundle_id() -> str | None:
    """Resolve the (non-existent) Linux host-bundle ID, the walk is the point."""
    if not is_linux():
        return None
    return _resolve_linux_host_bundle_id()


def _resolve_linux_host_bundle_id(start_pid: int | None = None, proc_root: str | Path = _PROC_ROOT) -> str | None:
    """Walk the real ``/proc`` chain (``start_pid`` injectable for tests)."""
    pid = os.getppid() if start_pid is None else start_pid
    for _ in range(_MAX_CHAIN_DEPTH):
        if pid is None or pid <= 1:
            break
        ppid, exe = _read_proc_entry(pid, proc_root)
        log.debug("[PROC-CHAIN] linux hop pid=%s ppid=%s exe=%s", pid, ppid, exe)
        if ppid is None:
            # Unreadable / malformed stat, the chain ends here. (On a
            break
        pid = ppid
    log.debug("[PROC-CHAIN] linux walk terminated cleanly (depth bound / chain end)")
    return None
