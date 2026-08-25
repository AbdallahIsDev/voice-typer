"""PID-file helpers for the autostart launcher.

Owns ``~/<config>/run/autostart.pid`` (launcher + child PIDs) and the
forward-compatible backend-port probe that reads the backend PID file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from voice_typer.server import _paths

# C-CROSS-3: explicit dotted logger name — see log_files.py for why
# ``__name__`` cannot be used here.
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _read_ipc_port_from_pid_file() -> int | None:
    """MED-Y /  (partial): read the backend's IPC
    port from the backend PID file if the writer included a ``port=``
    line.

    The backend PID file is the canonical source of truth for "is a
    Voice Typer backend running on this machine?" — it is written by
    :func:`voice_typer.server.single_instance._write_backend_pid_file`
    after the single-instance mutex is acquired. The current writer
    emits ONLY the PID (``{pid}\\n``); a future change to that function
    will extend the format to also include ``port=<n>\\n`` so the
    autostart launcher does not have to assume the default port 9876
    (which may have been auto-incremented if 9876 was busy).

    This function parses the PID file looking for a ``port=<n>`` line.
    Returns the port as an int if found, otherwise ``None`` (the caller
    falls back to :data:`IPC_PORT`).

    NOTE: the full fix requires :mod:`voice_typer.server.single_instance`
    to write the port line — see ``review.md`` MED-Y.
    This function alone is forward-compatible: once
    ``single_instance._write_backend_pid_file`` is updated to also emit
    the port, the autostart launcher will pick it up without further
    changes here.

    The function never raises — a missing/unreadable/malformed PID file
    simply yields ``None`` and the caller falls back to the default.
    """
    try:
        from voice_typer.server.app import _backend_pid_file

        pid_file = _backend_pid_file()
        if not pid_file.exists():
            return None
        # Read the file once and scan for a ``port=<n>`` line. We
        # tolerate both the legacy single-int format (no port line)
        # and the future multi-line
        # ``launcher=..\\nchild=..\\nport=..\\n`` format.
        text = pid_file.read_text()
    except OSError:
        return None
    except Exception:
        # Defensive: the PID file is shared state and a partial write
        # could surface as any error. Never let a port-read failure
        # propagate to launch() — fall back to IPC_PORT instead.
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
            # most OSes; >65535 is out of range).
            if 1 <= port <= 65535:
                return port
    return None


# Where to write the PID file (under the app's data dir).
# delegates to voice_typer.server._paths so the path respects the
# platform-aware _config_dir() logic (Windows %APPDATA%, macOS
# ~/Library/Application Support, Linux $XDG_DATA_HOME, the
# VOICE_TYPER_CONFIG_DIR override, and the legacy ~/.voice-typer
# migration check) instead of the previous hardcoded Path.home() /
# ".voice-typer".
def _config_dir() -> Path:
    """Return the voice-typer data directory.

    thin wrapper around :func:`voice_typer.server._paths.config_dir`
        kept for backwards compatibility — tests monkeypatch this name to
        redirect PID-file writes to a tmp dir.
    """
    return _paths.config_dir()


def _pid_file() -> Path:
    """Return the path to the autostart launcher's PID file.

    derives from :func:`_config_dir` so tests that monkeypatch
        this module's ``_config_dir`` continue to redirect the PID file as
        well. (The previous ``_paths.pid_file`` helper was removed in
    production code never adopted it; this local helper
        remains the single source of truth for the autostart PID file.)
    """
    from voice_typer.server import autostart_launcher as _pkg

    return _pkg._config_dir() / "run" / "autostart.pid"


def _write_pid_file(launcher_pid: int, child_pid: int | None) -> None:
    """Persist our PID + the child's PID in ``autostart.pid``.

    SEC-003: Uses _secure_atomic_write to ensure 0o600 permissions
    on POSIX and O_NOFOLLOW symlink protection.

    The child PID is captured at the call sites via
    ``getattr(child, "pid", None)``; a non-int value (a test-double
    return, an exotic spawn result) must NEVER be persisted — it would
    poison the file with a garbage ``child=`` value that no reader can
    parse. Non-int child PIDs are normalized to ``None``.
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
