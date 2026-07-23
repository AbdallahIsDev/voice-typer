"""Voice Typer — background voice-to-text utility for Windows.

SEC-004: This module must not have side effects at import time.
Only ``__version__`` is exported; no heavy imports, no global state,
no file I/O, and no logging configuration happen at the module level.

NEW-DOC-019: ``__version__`` is now read from package metadata
(installed via ``pyproject.toml``'s ``[project] version`` field)
using ``importlib.metadata``, with a hardcoded fallback for
development environments where the package isn't installed.
This makes ``pyproject.toml`` the single source of truth for the
version string; ``package.json`` should be
kept in sync via the build script (see ``scripts/build/sync_versions.py``).

PR-1-FIX-3: ``__version__`` is resolved lazily via PEP 562's
``__getattr__``. The first access pays the ~53ms
``importlib.metadata.version("voice-typer")`` cost; subsequent
accesses read the cached value from ``globals()``. This keeps the
package import itself free of metadata I/O, which is 57% of the
post-optimization tray-import cumulative time
(see ``bench/COLDSTART_REPORT.md``).
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    """Resolve module attributes lazily (PEP 562).

    Only ``__version__`` is handled here. On first access it queries
    ``importlib.metadata`` and caches the result in the module
    ``globals()`` so later reads are free. Any other attribute name
    raises :class:`AttributeError` as usual.
    """
    if name == "__version__":
        try:
            from importlib.metadata import version

            v: str = version("voice-typer")
        except Exception:
            # Package not installed (e.g. running from source checkout)
            # or importlib.metadata unavailable on this Python build.
            # Fall back to the hardcoded value; the build process
            # overrides this via pyproject.toml.
            v = "1.0.0"
        globals()["__version__"] = v  # cache for subsequent access
        return v
    raise AttributeError(name)
