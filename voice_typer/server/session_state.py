"""Session-liveness marker — distinguishes genuine crashes from expected restarts.

The backend writes a small ``session_active`` marker file in the config
directory when a real session begins (:meth:`StartupSequence.run`) and
removes it on every clean-shutdown path (``ShutdownController._do_cleanup``
and ``_do_fast_cleanup``). At the next launch, the crash check
(``crash_handler.report_pending_crash``) only surfaces the "previous
session crashed" notification when the marker is STILL present — i.e. the
previous process ended WITHOUT a clean shutdown.

Why this is needed
------------------
Before this marker, any leftover ``crash_diagnostics.*.txt`` /
``python_crash.*.txt`` file triggered the "Previous Session Crashed"
notification — even when the previous session exited cleanly. Daemon
threads that raise during interpreter teardown (socket close, watchdogs
being killed, ``--reload`` / backend-restart kills) write
``python_crash`` markers that are NOT application crashes, and a clean
quit, a backend restart/reload, or a Windows logoff fast-cleanup must
not be reported as a crash.

Contract
--------
* ``mark_session_active(config_dir)`` — called when a real session
  begins, AFTER the previous session's crash check has consumed its
  state.
* ``clear_session_marker(config_dir)`` — called on every clean-shutdown
  path (quit / restart_app / atexit safety net / Windows logoff fast
  path). Idempotent.
* ``was_previous_session_abnormal(config_dir)`` — True iff the marker
  survived, i.e. the previous process was terminated without running
  the clean-shutdown path.

Reuses the config-dir + secure-atomic-write machinery already used by
``single_instance._write_backend_pid_file`` — no new persistence
mechanism is introduced.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

#: Marker filename inside the config dir. Kept separate from
#: ``backend.pid`` (single-instance) and the ``*.reported`` sidecars
#: (crash archive) so the three concerns stay independently testable.
SESSION_MARKER_FILENAME = "session_active"


def session_marker_path(config_dir: Path) -> Path:
    """Path to the session marker inside ``config_dir``."""
    return Path(config_dir) / SESSION_MARKER_FILENAME


def was_previous_session_abnormal(config_dir: Path) -> bool:
    """True iff the previous session did NOT shut down cleanly.

    The marker is written at session start and removed on clean
    shutdown, so its presence at launch means the previous process was
    terminated without the clean-shutdown path running (crash, SIGKILL,
    power loss) — or this is a leftover from a session that never
    reached the marker-write point (startup aborted before ``run()``
    marked the session active).
    """
    try:
        return session_marker_path(config_dir).exists()
    except OSError:
        log.debug("[SESSION] could not check session marker", exc_info=True)
        return False


def mark_session_active(config_dir: Path) -> None:
    """Write the session-active marker (best-effort).

    Called at the start of a real session — AFTER the previous session's
    crash check has consumed the previous marker state. Content carries
    the PID + start timestamp for diagnostics. Atomic write
    (``_secure_atomic_write``) so the marker is never half-written.
    """
    try:
        from voice_typer.server.config import _secure_atomic_write

        marker = session_marker_path(config_dir)
        marker.parent.mkdir(parents=True, exist_ok=True)
        content = f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n"
        # durability=False — recreated every launch; atomic rename still
        # guarantees no torn reads (same policy as backend.pid).
        _secure_atomic_write(marker, content, durability=False)
    except Exception as exc:
        log.debug("[SESSION] could not write session marker: %s", exc)


def clear_session_marker(config_dir: Path) -> None:
    """Remove the session-active marker (best-effort, idempotent).

    Called on every clean-shutdown path. A missing marker file is a
    no-op (the file may never have been written this session).
    """
    try:
        marker = session_marker_path(config_dir)
        if marker.exists():
            marker.unlink()
    except OSError as exc:
        log.debug("[SESSION] could not remove session marker: %s", exc)
    except Exception:
        log.debug("[SESSION] could not remove session marker", exc_info=True)


__all__ = [
    "SESSION_MARKER_FILENAME",
    "clear_session_marker",
    "mark_session_active",
    "session_marker_path",
    "was_previous_session_abnormal",
]
