"""Process-wide mutable logging state shared across the log package."""

from __future__ import annotations

import contextlib
import logging
import sys

# runtime log-level override registry.  Populated by
_module_level_overrides: dict[str, str] = {}

_devnull_files: list = []
"""File descriptors opened for pythonw.exe stdio redirection."""


def _sync_package_session_id(value: str) -> None:
    """Write ``voice_typer.server.log._session_id`` if the package is loaded."""
    pkg = sys.modules.get("voice_typer.server.log")
    if pkg is not None:
        pkg.__dict__["_session_id"] = value


def reset() -> None:
    """Reset all logging state, called by tests to avoid cross-test contamination."""
    _sync_package_session_id("")
    for f in _devnull_files:
        with contextlib.suppress(Exception):
            f.close()
    _devnull_files.clear()
    # overrides between runs.
    _module_level_overrides.clear()
    root = logging.getLogger("voice_typer")
    root.handlers.clear()
    root.filters.clear()
    root.setLevel(logging.DEBUG)


def close_devnull_files() -> None:
    """Close all devnull file descriptors opened during :func:`setup_logging`.

    Called during application shutdown so the FDs don't leak.
    """
    for f in _devnull_files:
        with contextlib.suppress(Exception):
            f.close()
    _devnull_files.clear()


def register_devnull_file(fd) -> None:
    """Register a devnull file descriptor for cleanup on shutdown."""
    _devnull_files.append(fd)
