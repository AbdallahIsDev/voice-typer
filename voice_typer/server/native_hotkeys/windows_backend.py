"""Windows native hotkey backend.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
(ARCH-045).

Spawns ``windows-key-listener.exe`` (C) which uses
``WH_KEYBOARD_LL`` (event-driven) instead of ``GetAsyncKeyState``
polling. Lower CPU, supports key suppression and modifier-only
detection.
"""

import sys

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

from .base import SubprocessHotkeyBackend

# See base.py for the rationale.
is_windows = lambda: _native_hotkeys_pkg.is_windows()


class WindowsHookHotkey(SubprocessHotkeyBackend):
    """Windows native hotkey backend.

    Spawns ``windows-key-listener.exe`` (C) which uses
    ``WH_KEYBOARD_LL`` (event-driven) instead of ``GetAsyncKeyState``
    polling. Lower CPU, supports key suppression and modifier-only
    detection.
    """

    platform_name = "Windows"
    supports_fn = False  # FN is firmware-only on Windows

    def _validate_platform(self) -> str | None:
        if not is_windows():
            return f"WindowsHookHotkey requires Windows (current: {sys.platform})"
        if self._parsed and "fn" in self._parsed["modifiers"]:
            return (
                "FN key is not supported on Windows — it is firmware-only "
                "and never reaches the OS. Use Caps Lock, Alt, or a function "
                "key instead."
            )
        return None
