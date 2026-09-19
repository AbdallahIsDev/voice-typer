"""Autostart log path helpers."""

from __future__ import annotations

import contextlib
import logging
import subprocess

# C-CROSS-3: this file is executed as part of a script the OS launches
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _tauri_log_files() -> dict:
    """Return DEVNULL for the Tauri host's stdout/stderr (O4: no duplicate capture)."""
    return {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }


def _close_log_files(sk: dict) -> None:
    """Close predecessor log file handles in the parent process."""
    for key in ("stdout", "stderr"):
        fd = sk.get(key)
        if fd is not None and fd is not subprocess.DEVNULL:
            with contextlib.suppress(Exception):
                fd.close()
