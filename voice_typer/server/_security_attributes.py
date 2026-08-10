"""Backward-compat shim — code moved to ``voice_typer.server.security`` (EO-23).

The Win32 restrictive-DACL builder moved to
:mod:`voice_typer.server.security.win32_dacl`. This module re-exports the
names so existing import sites (``app.py``, ``single_instance.py``,
``tests/test__security_attributes.py``) keep working unchanged.

New code should import from ``voice_typer.server.security.win32_dacl``
directly.
"""

from voice_typer.server.platform_utils import is_windows  # noqa: F401
from voice_typer.server.security.win32_dacl import (  # noqa: F401
    __local_free_safe,
    _create_restrictive_security_attributes,
)

__all__ = ["_create_restrictive_security_attributes"]
