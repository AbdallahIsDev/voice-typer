"""Autostart PID file helpers."""

from __future__ import annotations

import logging
from pathlib import Path

from voice_typer.server import _paths

# C-CROSS-3: explicit dotted logger name: see log_files.py for why
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _read_ipc_port_from_pid_file() -> int | None:
    """MED-Y: read the backend's IPC port from the backend PID file.

    Returns the port as an int if found, otherwise ``None`` (the caller
    """
    try:
        # Resolve via the runtime-neutral backend_pid leaf (light),
        from voice_typer.server.backend_pid import _backend_pid_file

        pid_file = _backend_pid_file()
        if not pid_file.exists():
            return None
        # Read the file once and scan for a ``port=<n>`` line. We
        text = pid_file.read_text()
    except OSError:
        return None
    except Exception:
        # Defensive: the PID file is shared state and a partial write
        return None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("port="):
            port_str = line[len("port=") :].strip()
            try:
                port = int(port_str)
            except ValueError:
                continue
            # Reject obviously invalid port numbers (0 is reserved on
            if 1 <= port <= 65535:
                return port
    return None


# Where to write the PID file (under the app's data dir).
def _config_dir() -> Path:
    """Return the voice-typer data directory."""
    return _paths.config_dir()


def _pid_file() -> Path:
    """Return the path to the autostart launcher's PID file."""
    from voice_typer.server import autostart_launcher as _pkg

    return _pkg._config_dir() / "run" / "autostart.pid"


def _write_pid_file(launcher_pid: int, child_pid: int | None) -> None:
    """SEC-003: Uses _secure_atomic_write to ensure 0o600 permissions
    poison the file with a garbage ``child=`` value that no reader can
    """
    from voice_typer.server import autostart_launcher as _pkg

    if not isinstance(launcher_pid, int):
        launcher_pid = 0
    if not isinstance(child_pid, int):
        child_pid = None
    try:
        _pkg._config_dir().mkdir(parents=True, exist_ok=True)
        _pkg._pid_file().parent.mkdir(exist_ok=True, mode=0o700)
        from voice_typer.server.config import _secure_atomic_write

        _secure_atomic_write(
            _pkg._pid_file(),
            f"launcher={launcher_pid}\nchild={child_pid or ''}\n",
        )
    except OSError as exc:
        log.warning("[AUTOSTART] could not write pid file: %s", exc)
