"""Platform-flag helpers (backwards-compat shim).

Phase 4.5 /  — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  These three
helpers (``is_windows`` / ``is_macos`` / ``is_linux``) are duplicates
of the canonical implementations in
:mod:`voice_typer.server.platform_utils`.

The comprehensive review notes that callers should be migrated to
import from :mod:`platform_utils` directly (which several modules
already do — see :mod:`voice_typer.server.clipboard`).  This module is
kept as a soft-deprecation shim: it re-exports the canonical
implementations so any external code (or tests) that does
``from voice_typer.server.server_platform import is_windows`` keeps
working without modification.

the canonical ``platform_utils.is_linux`` uses
``sys.platform.startswith("linux")`` (matches ``linux2`` on Python 2
and ``linux`` on Python 3).  The legacy ``server_platform.is_linux``
used the exact-match ``sys.platform == "linux"`` form.  The
soft-deprecation shim re-exports the canonical (``startswith``) form
because:

  1. Python 2 is no longer supported by the project (pyproject.toml
     ``requires-python`` is ``>=3.9``), so the ``linux2`` case never
     fires in practice.
  2. Using the canonical form here means there is exactly one
     definition of ``is_linux`` in the codebase, eliminating the
     drift hazard between two parallel implementations.

If a caller depends on the legacy exact-match behaviour, it should
import ``sys`` and do the comparison itself.
"""

from __future__ import annotations

import sys

from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

# Canonical platform-dispatch snapshot ("win32" / "darwin" / "linux").
#
# Owns the single ``SYSTEM`` definition for the ``server_platform``
# package. Submodules that dispatch on the raw platform read this
# attribute through THIS module at call time
# (``platform_flags.SYSTEM``), so tests fake the platform by patching
# ``voice_typer.server.server_platform.platform_flags.SYSTEM`` — one
# stable target for every consumer.
SYSTEM = sys.platform

__all__ = ["is_windows", "is_macos", "is_linux", "SYSTEM"]
