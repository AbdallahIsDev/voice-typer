"""Internal helpers extracted from ``voice_typer.server.config``.

This package contains implementation details that ``config.py``
delegates to.  The split exists purely to keep ``config.py`` under a
manageable size — every symbol defined here is re-exported from
``config.py`` (via ``from voice_typer.server.config_internals.paths
import ...`` etc.) so existing callers that do
``from voice_typer.server.config import _config_dir`` keep working
unchanged.

Modules:
- :mod:`voice_typer.server.config_internals.paths`      — path-safety +
  config-dir resolution + cross-process config-lock.
- :mod:`voice_typer.server.config_internals.migrations` — schema
  migration runner + per-version migrators.
"""
