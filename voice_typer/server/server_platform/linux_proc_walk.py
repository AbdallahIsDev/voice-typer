"""Walk the REAL Linux parent-process chain via ``/proc``.

The Linux sibling of :mod:`voice_typer.server.server_platform
.macos_bundle_id`: the macOS walker runs ``ps -p <pid> -o ppid= -o
comm=`` to climb from the backend to the nearest ``*.app`` bundle;
this module climbs the same bounded parent chain on Linux by reading
``/proc/<pid>/stat`` (parent PID — field 4) and ``/proc/<pid>/cmdline``
(argv[0] — the executable the process was launched with).

Bundle-detection semantics (honest): Linux has NO ``*.app`` bundles, so
host-bundle detection is a documented no-op — the walker exercises the
full bounded chain (same ``_MAX_CHAIN_DEPTH`` semantics as macOS) and
returns ``None`` per the current design. The WALK is the point: it
validates that the ``/proc`` chain is readable and terminates cleanly
(never raises, never loops), which CI exercises against the real
``/proc`` tree after ``cargo tauri build`` in
``tauri-linux-build.yml``. A future Flatpak (``/app/...``) or Snap
(``/snap/...``) host-detection scheme would plug into the walk without
changing its termination guarantees.

Linux permission re-grants are udev/polkit-based (not TCC-based), so
callers treat a ``None`` result as "no host bundle to re-grant" — the
macOS ``tccutil`` path simply has no Linux counterpart today.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from voice_typer.server.platform_utils import is_linux
from voice_typer.server.server_platform.macos_bundle_id import _MAX_CHAIN_DEPTH

log = logging.getLogger(__name__)

# Default ``/proc`` mount point; injectable (via ``proc_root``) for
# tests that run on hosts without ``/proc`` (Windows CI box).
_PROC_ROOT = Path("/proc")


def _stat_ppid(stat_text: str) -> int | None:
    """Parse the parent PID (field 4) from a ``/proc/<pid>/stat`` line.

    ``/proc/<pid>/stat`` is: ``<pid> (<comm>) <state> <ppid> ...``. The
    ``comm`` field is wrapped in parentheses and may itself contain
    spaces (kernel ``comm`` is 15 chars and can include spaces), so the
    robust parse is: split after the LAST ``)``, then the second
    whitespace-separated field of the remainder is the ppid (field 1 of
    the remainder is the state char). Returns ``None`` on any
    malformed input so the walk treats the hop as unresolvable and
    stops.
    """
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
    """Return argv[0] (the executable path) from ``/proc/<pid>/cmdline``.

    ``cmdline`` is NUL-separated argv; argv[0] is the executable the
    process was launched with (mirrors the macOS ``ps -o comm=``
    semantics of "the executable path"). Returns ``None`` when empty
    or undecodable.
    """
    if not cmdline:
        return None
    try:
        argv0 = cmdline.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
    except Exception:  # pragma: no cover - defensive; bytes cannot raise decode errors with replace
        return None
    return argv0 or None


def _read_proc_entry(pid: int, proc_root: str | Path = _PROC_ROOT) -> tuple[int | None, str | None]:
    """Read ``(ppid, argv0)`` for ``pid`` from ``/proc`` (best-effort).

    Reads ``/proc/<pid>/stat`` + ``/proc/<pid>/cmdline``. Each read is
    individually guarded: a process that exits mid-read (or a host
    without ``/proc`` at ``proc_root``) yields ``None`` for that field
    rather than an exception — mirroring ``_process_chain_line``'s
    "return \"\" on any failure" contract in the macOS walker.
    """
    proc = Path(proc_root)
    ppid: int | None = None
    exe: str | None = None
    with contextlib.suppress(OSError):
        ppid = _stat_ppid((proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace"))
    with contextlib.suppress(OSError):
        exe = _cmdline_exe((proc / str(pid) / "cmdline").read_bytes())
    return ppid, exe


def resolve_linux_host_bundle_id() -> str | None:
    """Resolve the (non-existent) Linux host-bundle ID — the walk is the point.

    Linux-only (returns ``None`` on other platforms without touching
    ``/proc``). Walks the real parent-process chain via ``/proc``,
    bounded by ``_MAX_CHAIN_DEPTH`` (shared with the macOS walker), and
    always returns ``None``: Linux has no ``*.app`` bundles to detect
    per the current design (see the module docstring). The walk itself
    is exercised end-to-end — a regression that makes the chain walk
    crash or loop is caught even though the return value is constant.
    """
    if not is_linux():
        return None
    return _resolve_linux_host_bundle_id()


def _resolve_linux_host_bundle_id(start_pid: int | None = None, proc_root: str | Path = _PROC_ROOT) -> str | None:
    """Walk the real ``/proc`` chain (``start_pid`` injectable for tests).

    ``start_pid`` defaults to the backend's own parent
    (``os.getppid()``) — the Tauri host in the bundled launch path,
    mirroring the macOS walker. Every hop is logged at DEBUG so a
    failing chain is traceable. Terminates cleanly on: ``pid <= 1``
    (reached init), an unreadable stat entry (process exited between
    hops), a malformed entry, or the depth bound. Always returns
    ``None`` on Linux today (no bundle detection — see the module
    docstring).
    """
    pid = os.getppid() if start_pid is None else start_pid
    for _ in range(_MAX_CHAIN_DEPTH):
        if pid is None or pid <= 1:
            break
        ppid, exe = _read_proc_entry(pid, proc_root)
        log.debug("[PROC-CHAIN] linux hop pid=%s ppid=%s exe=%s", pid, ppid, exe)
        if ppid is None:
            # Unreadable / malformed stat — the chain ends here. (On a
            # host without ``/proc`` every hop ends here immediately:
            # graceful no-op termination.)
            break
        pid = ppid
    log.debug("[PROC-CHAIN] linux walk terminated cleanly (depth bound / chain end)")
    return None
