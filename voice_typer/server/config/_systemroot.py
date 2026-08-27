"""System-root validation re-export shim.

Extracted from ``config/__init__.py``. The
canonical implementation already lives in
``voice_typer.server.config_internals.paths._validate_systemroot``
(that module owns the actual probe — see the lazy-import shim
there for why it cannot be eagerly imported at module-load time).

This module is the canonical import path going forward:

.. code-block:: python

    from voice_typer.server.config._systemroot import _validate_systemroot

``config/__init__.py`` re-exports ``_validate_systemroot`` so the
legacy import ``from voice_typer.server.config import
_validate_systemroot`` (used by ``env_validation.py`` and the
``tests/regressions/test_security.py`` regression guards) keeps
working unchanged.
"""

from voice_typer.server.config_internals.paths import (  # noqa: F401 — re-export
    _validate_systemroot,
)

__all__ = ["_validate_systemroot"]
