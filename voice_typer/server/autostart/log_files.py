"""Child stdout/stderr handle helpers for the autostart launcher.

Shared by the Tauri and Electron spawn paths (see ``tauri_spawn.py``
and ``electron_spawn.py``).
"""

from __future__ import annotations

import contextlib
import logging
import subprocess

# C-CROSS-3: this file is executed as part of a script the OS launches
# directly (``pythonw.exe autostart_launcher.py``), where ``__name__``
# would be ``"__main__"`` — use the explicit dotted logger name so
# launcher records reach the app's rotating file handler.
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _tauri_log_files() -> dict:
    """Return DEVNULL for the Tauri host's stdout/stderr (O4: no duplicate capture).

    Mirrors :func:`voice_typer.server._electron_build._electron_log_files`.
    The Tauri host + Python backend already write structured logs to
    ``logs/`` (``voice-typer-rust.log`` on the Rust side, ``voice-typer.log``
    on the Python side); raw child stdout/stderr capture
    (``tauri-stdout.log`` / ``tauri-stderr.log``) only duplicated those
    lines without adding diagnostic value.
    """
    return {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }


def _close_log_files(sk: dict) -> None:
    """Close Electron log file handles in the parent process.

    Called after ``subprocess.Popen`` to close the parent's copies of
    the stdout/stderr log files.  The child process has inherited the
    file descriptors, so the files remain open for the child's lifetime.
    Without this, the parent leaks file handles and triggers
    ``ResourceWarning`` on GC.
    """
    for key in ("stdout", "stderr"):
        fd = sk.get(key)
        if fd is not None and fd is not subprocess.DEVNULL:
            with contextlib.suppress(Exception):
                fd.close()
