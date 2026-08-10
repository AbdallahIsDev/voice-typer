"""Backward-compat shim — code moved to ``voice_typer.server.security`` (EO-23).

The secure atomic file-I/O helpers + ``PersistedJSON`` moved to
:mod:`voice_typer.server.security.file_io`. This module re-exports every
name the codebase and tests reference so existing import sites (including
``voice_typer.server.config``'s re-export and the lazy
``PersistedJSON.save`` import of ``config._secure_atomic_write``) keep
working unchanged.

New code should import from ``voice_typer.server.security.file_io``
directly.
"""

import time  # noqa: F401 — re-exported for tests that monkeypatch secure_file_io.time

from voice_typer.server.security.file_io import (  # noqa: F401
    _DEFAULT_MAX_READ_BYTES,
    _QUARANTINE_SUFFIX_SEQ,
    PersistedJSON,
    _chmod_owner_only,
    _read_with_byte_limit,
    _secure_atomic_write,
    _secure_read_text,
    _windows_fsync_directory,
)

__all__ = ["PersistedJSON"]
