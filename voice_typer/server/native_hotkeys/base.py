"""Native hotkey backend — facade module.

Re-exports SubprocessHotkeyBackend + constants + get_native_binary_path
from the leaf modules, keeping the import path
``voice_typer.server.native_hotkeys.base.X`` resolving for all names
that tests and callers expect (C-ARCH-2).

The actual class definition lives in :mod:`._core`;
platform-specific logic is in :mod:`._spawn`, :mod:`._reader`,
:mod:`._watchdog`, and :mod:`._matching`.
"""

from __future__ import annotations

from voice_typer.server.native_hotkeys._constants import (
    MAX_RESTART_ATTEMPTS,
    READY_TIMEOUT_SECONDS,
    RESTART_DELAY_BASE_SECONDS,
)
from voice_typer.server.native_hotkeys._core import SubprocessHotkeyBackend

# Re-export get_native_binary_path so tests that patch
# ``voice_typer.server.native_hotkeys.base.get_native_binary_path``
# keep resolving (the binary_path module is the canonical owner).
from voice_typer.server.native_hotkeys.binary_path import get_native_binary_path  # noqa: F401

# Re-export log so ``base.log`` resolves (used by test_retry_regressions).
from voice_typer.server.native_hotkeys.spec_parser import log  # noqa: F401

__all__ = [
    "MAX_RESTART_ATTEMPTS",
    "READY_TIMEOUT_SECONDS",
    "RESTART_DELAY_BASE_SECONDS",
    "SubprocessHotkeyBackend",
    "get_native_binary_path",
    "log",
]
