"""Voice Typer — background voice-to-text utility for Windows.

SEC-004: This module must not have side effects at import time.
Only ``__version__`` is exported; no heavy imports, no global state,
no file I/O, and no logging configuration happen at the module level.

NEW-DOC-019: ``__version__`` is now read from package metadata
(installed via ``pyproject.toml``'s ``[project] version`` field)
using ``importlib.metadata``, with a hardcoded fallback for
development environments where the package isn't installed.
This makes ``pyproject.toml`` the single source of truth for the
version string; ``package.json`` and ``installer.iss`` should be
kept in sync via the build script (see ``scripts/sync_versions.py``).
"""

try:
    # Python 3.8+: importlib.metadata is in the stdlib.
    from importlib.metadata import version as _pkg_version, PackageNotFoundError

    try:
        __version__ = _pkg_version("voice-typer")
    except PackageNotFoundError:
        # Package not installed (e.g. running from source checkout).
        # Fall back to the hardcoded value; the build process overrides
        # this via pyproject.toml.
        __version__ = "1.0.0"
except ImportError:
    # Python <3.8 (not supported, but defensive).
    __version__ = "1.0.0"
