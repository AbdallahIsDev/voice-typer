"""Platform-flag helpers (backwards-compat shim)."""

from __future__ import annotations

import sys

from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

# Canonical platform-dispatch snapshot ("win32" / "darwin" / "linux").
SYSTEM = sys.platform

# How many ancestor processes to walk looking for the host runtime
_MAX_CHAIN_DEPTH = 8

__all__ = ["is_windows", "is_macos", "is_linux", "SYSTEM"]
