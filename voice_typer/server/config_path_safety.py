"""Backward-compat re-export shim for path-safety helpers.

Canonical location: :mod:`voice_typer.server.config_internals.paths`
(``voice_typer/server/config_internals/paths.py``) — the function
BODIES (the ``def`` statements) live there, not here.  This module
is the *named import path* for path-traversal / path-containment
validation used by :mod:`voice_typer.server.config`; it re-exports
the three path-safety helpers so existing callers and test-patch
sites keep resolving without pulling in the rest of the
config-loading machinery.

This shim will be removed after the monkeypatch
migration collapses the re-export indirection (callers updated to
import directly from ``config_internals.paths``); until then it is
preserved verbatim so the monkeypatch sites in
``tests/test_env_validation_*.py`` and ``tests/test_path_traversal.py``
keep working unchanged.

Finding  identified ``config.py`` (2,698 LOC) as mixing 5
module-level concerns; the path-safety helpers are one of those
concerns and belong in their own named module so future contributors
can grep for ``config_path_safety`` and find every path-traversal
guard in one place.

Rationale for the re-export shape
---------------------------------
The function *bodies* currently live in
:mod:`voice_typer.server.config_internals.paths` (they were moved
there during the  /  partial split that extracted
``config_internals.{paths,migrations}`` out of ``config.py``).
``config_internals.paths`` is a mixed module that bundles
path-safety + config-dir resolution + cross-process lock +
SystemRoot validation — the  finding's complaint is exactly
that these concerns are not yet separated into dedicated modules.

This module (``config_path_safety.py``) provides the *named* home
for path-safety concerns that the finding asks for.  It re-exports
the three path-safety functions so:

1. ``voice_typer.server.config`` can import them from
   ``config_path_safety`` (one less direct dependency on the
   ``config_internals.paths`` mixed module — a step toward
   ``config.py`` thinning to ≤600 LOC).
2. Future contributors can extend path-safety by editing
   ``config_path_safety.py`` directly (adding new helpers, migrating
   the existing function bodies here once ``config_internals.paths``
   is also touched).
3. Tests that want to import path-safety functions without pulling
   in the rest of the config-loading machinery can do
   ``from voice_typer.server.config_path_safety import
   _validate_path_safety`` without importing ``config.py``.

Functions re-exported
---------------------
- :func:`_validate_path_safety`     — path-traversal guard for
  user-supplied env vars (``VOICE_TYPER_CONFIG_DIR``,
  ``XDG_DATA_HOME``, etc.).
- :func:`_is_path_within`           — robust cross-platform
  path-containment check (uses :func:`os.path.commonpath`).
- :func:`_validate_import_path`     — bounds-check for the
  ``import_model`` IPC handler.

Public-API preservation
-----------------------
Every name re-exported here is also re-exported from
:mod:`voice_typer.server.config` (via ``from
voice_typer.server.config_path_safety import …``) so existing
``from voice_typer.server.config import _validate_path_safety``
callers (and the monkeypatch sites in
``tests/test_env_validation_*.py`` and
``tests/test_path_traversal.py``) keep working unchanged.
"""

from __future__ import annotations

# Re-export the path-safety helpers.  The ``noqa: F401`` suppresses
# the "imported but unused" warning — these names ARE used by callers
# who import them from this module.
# Canonical module: voice_typer/server/config_internals/paths.py
from voice_typer.server.config_internals.paths import (  # noqa: F401 — backward-compat re-export
    _is_path_within,
    _validate_import_path,
    _validate_path_safety,
)

__all__ = [
    "_is_path_within",
    "_validate_import_path",
    "_validate_path_safety",
]
