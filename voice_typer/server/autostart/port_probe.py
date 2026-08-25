"""Backend IPC-port readiness polling for the autostart launcher.

``_wait_for_backend_ready`` (alias ``_wait_for_ipc_ready``) bounds how
long the launcher waits for the freshly-spawned backend to bind its
IPC port before exiting.
"""

from __future__ import annotations

import logging
import socket
import time

# C-CROSS-3: explicit dotted logger name — see log_files.py for why
# ``__name__`` cannot be used here.
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _is_port_open(host: str, port: int) -> bool:
    """Return True if *host:port* accepts TCP connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


# How long to wait for the freshly-spawned backend to bind its IPC port
# before the launcher exits. The launcher's job is to spawn + detach the
# child; the OS autostart parent (Windows Run key, macOS LaunchAgent,
# Linux ``.desktop``) waits for the launcher to exit before considering
# login complete. Polling the IPC port lets the launcher exit AS SOON AS
# the backend is ready (typically 200-500 ms on a warm start with
# prewarm) instead of a fixed 2 s sleep — saving up to 1.5 s on the
# user's login critical path. The 5 s ceiling preserves the original
# "give the child time to detach" safety net for slow systems where the
# backend takes longer to start (cold start without prewarm, slow disk,
# antivirus scan).
_POST_SPAWN_PORT_POLL_TIMEOUT = 5.0
_POST_SPAWN_PORT_POLL_INTERVAL = 0.1


def _wait_for_backend_ready(
    timeout: float = _POST_SPAWN_PORT_POLL_TIMEOUT,
    *,
    deadline_s: float | None = None,
    poll_interval_s: float | None = None,
) -> bool | None:
    """Bounded poll for the backend's IPC port to open after spawning.

    Polls ``_is_port_open`` every ``_POST_SPAWN_PORT_POLL_INTERVAL``
    seconds (or ``poll_interval_s`` if provided) for up to ``timeout``
    seconds (or ``deadline_s`` if provided). Returns as soon as the
    port opens (early-exit on fast systems) or after the timeout
    (preserves the original "give the child time to detach" safety
    net on slow systems). Never raises — a port that never opens is
    the backend's problem to surface (crash dialog, log), not the
    launcher's.

    Two calling conventions are supported:

    * Legacy: ``_wait_for_backend_ready(timeout=5.0)`` — used by the
      call sites in :func:`launch` (returns ``None``).
    * Test-friendly: ``_wait_for_ipc_ready(deadline_s=5.0,
      poll_interval_s=0.25)`` — returns ``True`` on port-open,
      ``False`` on deadline. See
      ``tests/test_perf_fixes.py::TestWaitForIpcReady``.

    The IPC port is re-read from the backend PID file on every
    iteration (via ``_read_ipc_port_from_pid_file``) so the poll picks
    up the actual port the backend bound to (which may differ from
    :data:`IPC_PORT` if 9876 was busy and the backend auto-incremented).

    The loop is bounded by an iteration count (derived from
    ``deadline / interval``) rather than a ``time.monotonic()`` deadline
    so that tests which monkeypatch ``time.sleep`` to a no-op don't
    busy-wait for the full ``deadline`` in real time — the loop runs
    ``int(deadline / interval)`` iterations and exits regardless of
    wall-clock elapsed time.

    Collaborators (``IPC_HOST``, ``IPC_PORT``, the PID-file reader and
    the port probe) are resolved through the facade module at call time
    so tests that monkeypatch them on
    ``voice_typer.server.autostart_launcher`` keep intercepting here.
    """
    from voice_typer.server import autostart_launcher as _pkg

    effective_deadline = _POST_SPAWN_PORT_POLL_TIMEOUT if deadline_s is None else deadline_s
    effective_interval = _POST_SPAWN_PORT_POLL_INTERVAL if poll_interval_s is None else poll_interval_s
    # If neither explicit kwarg was passed, this is the legacy
    # call-site path (launch()) which does not care about the return
    # value.  In that case return None for backwards compatibility.
    return_bool = deadline_s is not None
    max_iterations = max(1, int(effective_deadline / effective_interval))
    for _ in range(max_iterations):
        ipc_port = _pkg._read_ipc_port_from_pid_file() or _pkg.IPC_PORT
        if _pkg._is_port_open(_pkg.IPC_HOST, ipc_port):
            return True if return_bool else None
        time.sleep(effective_interval)
    return False if return_bool else None


# legacy / alternate-name alias. The test suite in
# ``tests/test_perf_fixes.py::TestWaitForIpcReady`` expects this
# exact name; the production code (which uses the
# ``_wait_for_backend_ready`` name throughout) is the canonical
# implementation. Both names point to the same callable so either
# contract works.
_wait_for_ipc_ready = _wait_for_backend_ready
