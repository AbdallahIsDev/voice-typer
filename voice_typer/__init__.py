"""voice_typer package marker."""

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

            v: str = version("lausu")
        except Exception:
            # Package not installed (e.g. running from source checkout)
            # or importlib.metadata unavailable on this Python build.
            # Fall back to the hardcoded value; the build process
            # overrides this via pyproject.toml.
            v = "1.0.0"
        globals()["__version__"] = v  # cache for subsequent access
        return v
    raise AttributeError(name)
