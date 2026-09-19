"""Autostart port probe helpers."""

from __future__ import annotations

import logging
import socket
import time

# C-CROSS-3: explicit dotted logger name: see log_files.py for why
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _is_port_open(host: str, port: int) -> bool:
    """Return True if *host:port* accepts TCP connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


# How long to wait for the freshly-spawned backend to bind its IPC port
_POST_SPAWN_PORT_POLL_TIMEOUT = 5.0
_POST_SPAWN_PORT_POLL_INTERVAL = 0.1


def _wait_for_backend_ready(
    timeout: float = _POST_SPAWN_PORT_POLL_TIMEOUT,
    *,
    deadline_s: float | None = None,
    poll_interval_s: float | None = None,
) -> bool | None:
    """Collaborators (``IPC_HOST``, ``IPC_PORT``, the PID-file reader and
    ``voice_typer.server.autostart_launcher`` keep intercepting here.
    """
    from voice_typer.server import autostart_launcher as _pkg

    effective_deadline = _POST_SPAWN_PORT_POLL_TIMEOUT if deadline_s is None else deadline_s
    effective_interval = _POST_SPAWN_PORT_POLL_INTERVAL if poll_interval_s is None else poll_interval_s
    # If neither explicit kwarg was passed, this is the legacy
    return_bool = deadline_s is not None
    max_iterations = max(1, int(effective_deadline / effective_interval))
    for _ in range(max_iterations):
        ipc_port = _pkg._read_ipc_port_from_pid_file() or _pkg.IPC_PORT
        if _pkg._is_port_open(_pkg.IPC_HOST, ipc_port):
            return True if return_bool else None
        time.sleep(effective_interval)
    return False if return_bool else None


# legacy / alternate-name alias. The test suite in
_wait_for_ipc_ready = _wait_for_backend_ready
