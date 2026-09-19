"""macOS native hotkey backend."""

import sys

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

from .base import SubprocessHotkeyBackend


# See base.py for the rationale.
def is_macos() -> bool:
    return _native_hotkeys_pkg.is_macos()


class MacNativeHotkey(SubprocessHotkeyBackend):
    """macOS native hotkey backend."""

    platform_name = "macOS"
    supports_fn = True

    def _validate_platform(self) -> str | None:
        if not is_macos():
            return f"MacNativeHotkey requires macOS (current: {sys.platform})"
        if self._parsed and "fn" in self._parsed["modifiers"]:
            # FN is supported on macOS
            pass
        return None
