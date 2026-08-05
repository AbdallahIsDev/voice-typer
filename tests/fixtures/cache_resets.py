"""Centralised ``functools.lru_cache`` reset helper for tests.

Previously the ``clear_binary_path_cache`` autouse fixture in
:mod:`tests.conftest` carried four near-identical ``try/except ImportError``
blocks, one per cached production helper:

  - ``voice_typer.server.native_hotkeys.binary_path.get_native_binary_path``
  - ``voice_typer.server.clipboard.linux._shutil_which_cached``
  - ``voice_typer.server.prewarm.cache_probe._resolve_hf_cache_dir``
  - ``voice_typer.server.prewarm.cache_probe._cached_active_config``

Each block did the same three things: import the module, ``getattr`` the
cached callable, look up ``cache_clear``, and call it if present. The
copy-paste meant that adding a fifth cached callable required touching
``conftest.py`` again (and risked the new block being silently skipped
by a typo in one of the four ``except ImportError`` clauses — the exact
latent bug documented in the original fixture's docstring).

This module replaces the four blocks with a single
:data:`CACHES_TO_CLEAR` table + a :func:`clear_caches` loop. Adding a
new cache is now a one-line table edit; the loop is too small to
harbour per-block drift.

The function is intentionally side-effect-only (returns ``None``) so
callers can use it as the body of an autouse fixture without juggling a
return value. ``ImportError`` is swallowed per-entry so a missing
optional dependency (e.g. ``clipboard.linux`` on Windows-only test
runs) doesn't break the rest of the clears — same semantics as the
original copy-pasted blocks.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable

# Each entry is ``(module_path, attr_name)``. ``module_path`` is the
# dotted import path; ``attr_name`` is the attribute on that module
# whose ``functools.lru_cache`` should be cleared between tests.
#
# Order matters only for readability — each clear is independent and
# runs in its own ``try/except`` so a failure on one entry does not
# short-circuit the rest (mirrors the original copy-pasted semantics).
CACHES_TO_CLEAR: tuple[tuple[str, str], ...] = (
    (
        "voice_typer.server.native_hotkeys.binary_path",
        "get_native_binary_path",
    ),
    (
        "voice_typer.server.clipboard.linux",
        "_shutil_which_cached",
    ),
    (
        "voice_typer.server.prewarm.cache_probe",
        "_resolve_hf_cache_dir",
    ),
    (
        "voice_typer.server.prewarm.cache_probe",
        "_cached_active_config",
    ),
)


def clear_caches(
    caches: Iterable[tuple[str, str]] | None = None,
) -> None:
    """Clear ``functools.lru_cache`` decorations on a fixed set of helpers.

    Iterates over :data:`CACHES_TO_CLEAR` (or the override ``caches``
    argument, useful for tests that want to assert the loop itself
    works). For each ``(module_path, attr_name)`` entry:

      1. ``importlib.import_module(module_path)`` — if the module is
         not importable in this environment (e.g. a stripped-down test
         subset, or a platform-gated module), the ``ImportError`` is
         swallowed and the loop moves on. This mirrors the original
         per-block ``except ImportError: pass`` semantics.
      2. ``getattr(module, attr_name)`` — fetches the cached callable.
         ``AttributeError`` here would indicate production drift (the
         callable was renamed or removed); it is allowed to propagate
         so the drift is visible in CI rather than silently swallowed.
      3. ``getattr(callable, "cache_clear", None)`` — looks up the
         ``functools.lru_cache`` teardown hook. If the callable is no
         longer decorated (a future refactor removed
         ``@lru_cache``), this is ``None`` and the call is skipped —
         same guard as the original blocks. The affected tests would
         then start failing on the caching contract, which is the
         desired signal.
      4. ``cache_clear()`` — clears the cache.

    Returns ``None``. Callers should use it for its side effect; the
    autouse fixture in :mod:`tests.conftest` calls it once per test.
    """
    entries = tuple(caches) if caches is not None else CACHES_TO_CLEAR
    for module_path, attr_name in entries:
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            # Module not importable in this test environment (e.g. a
            # stripped-down test subset or a platform-gated module).
            # Nothing to clear for THIS entry — subsequent entries
            # still run, mirroring the original per-block semantics.
            continue
        cached = getattr(module, attr_name)
        cache_clear = getattr(cached, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()
