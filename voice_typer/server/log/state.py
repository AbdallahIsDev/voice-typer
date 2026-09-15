"""Process-wide mutable logging state shared across the log package.

Extracted from the original monolithic ``log/__init__.py`` so
implementations can live in focused modules while still sharing the
same mutable registries:

- :data:`_module_level_overrides` — runtime per-logger level overrides
  (env-var path + ``set_module_level`` API).  Re-exported by identity
  from the package so ``from voice_typer.server.log import
  _module_level_overrides`` mutates the same dict.
- :data:`_devnull_files` — file descriptors opened for pythonw.exe
  stdio redirection (same identity-re-export pattern).
- :data:`_session_id` is intentionally NOT stored here.  The canonical
  store is the package attribute ``voice_typer.server.log._session_id``
  because test fixtures snapshot/restore it via
  ``_log_module._session_id`` and ``_SessionFilter`` / ``setup_logging``
  must observe those writes.  :func:`reset` keeps the package attribute
  in sync (late lookup, no circular import at module load time).
"""

from __future__ import annotations

import contextlib
import logging
import sys

# runtime log-level override registry.  Populated by
# ``_apply_per_module_log_levels`` (env-var path at startup) and by
# ``set_module_level`` (runtime API for IPC / CLI).  Queried by
# ``get_module_levels`` so operators can verify the active per-module
# config without restarting.  Values are stored as level *names*
# (``"DEBUG"``, ``"INFO"`` ...) so the dict is JSON-serialisable for IPC.
_module_level_overrides: dict[str, str] = {}

_devnull_files: list = []
"""File descriptors opened for pythonw.exe stdio redirection."""


def _sync_package_session_id(value: str) -> None:
    """Write ``voice_typer.server.log._session_id`` if the package is loaded.

    Late lookup via ``sys.modules`` so this module never imports the
    package at load time (circular).  Safe when the package is still
    mid-import: the attribute is only needed after setup/reset runs.
    """
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
    # clear the per-module override registry so tests don't leak
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
    """Register a devnull file descriptor for cleanup on shutdown.

    Called from :mod:`voice_typer.server.signal_handlers` when the
    Win32 Ctrl-Close handler reopens stdout/stderr to ``os.devnull``
    after ``FreeConsole`` (Windows). The registered FD is later closed
    by :func:`close_devnull_files` during shutdown.
    """
    _devnull_files.append(fd)
