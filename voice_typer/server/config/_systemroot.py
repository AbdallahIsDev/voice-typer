"""System-root validation re-export shim."""

from voice_typer.server.config_internals.paths import (  # noqa: F401, re-export
    _validate_systemroot,
)

__all__ = ["_validate_systemroot"]
