"""Linux native hotkey backend.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
(ARCH-045).

Spawns ``linux-key-listener`` (C) which reads from
``/dev/input/event*`` (evdev). Works on both X11 and Wayland
(unlike pynput which is X11-only). Requires the user to be in the
``input`` group.
"""

import sys

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

from .base import SubprocessHotkeyBackend

# See base.py for the rationale.
is_linux = lambda: _native_hotkeys_pkg.is_linux()


class LinuxEvdevHotkey(SubprocessHotkeyBackend):
    """Linux native hotkey backend.

    Spawns ``linux-key-listener`` (C) which reads from
    ``/dev/input/event*`` (evdev). Works on both X11 and Wayland
    (unlike pynput which is X11-only). Requires the user to be in the
    ``input`` group.
    """

    platform_name = "Linux"
    supports_fn = False  # FN is firmware-only on most Linux laptops

    def _validate_platform(self) -> str | None:
        if not is_linux():
            return f"LinuxEvdevHotkey requires Linux (current: {sys.platform})"
        if self._parsed and "fn" in self._parsed["modifiers"]:
            return (
                "FN key is not supported on Linux — it is firmware-only on "
                "most laptops. Use Caps Lock, Alt, or a function key instead."
            )
        return None
