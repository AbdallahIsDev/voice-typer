"""Windows security attribute helpers."""

from voice_typer.server.platform_utils import is_windows  # noqa: F401
from voice_typer.server.security.win32_dacl import (  # noqa: F401
    __local_free_safe,
    _create_restrictive_security_attributes,
)

__all__ = ["_create_restrictive_security_attributes"]
