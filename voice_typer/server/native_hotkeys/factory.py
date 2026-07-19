"""Factory functions :func:`create_native_backend` and
:func:`is_native_backend_available`.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
(ARCH-045).
"""

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

from .base import SubprocessHotkeyBackend
from .binary_path import get_native_binary_path
from .linux_backend import LinuxEvdevHotkey
from .mac_backend import MacNativeHotkey
from .windows_backend import WindowsHookHotkey

# See base.py for the rationale.
is_windows = lambda: _native_hotkeys_pkg.is_windows()
is_macos = lambda: _native_hotkeys_pkg.is_macos()
is_linux = lambda: _native_hotkeys_pkg.is_linux()


# ─── Factory ───────────────────────────────────────────────────────────────


def create_native_backend(hotkey_str: str) -> SubprocessHotkeyBackend | None:
    """Create a native subprocess backend for the current platform.

    Returns ``None`` if no native binary is available (caller should fall
    back to a legacy backend).
    """
    binary = get_native_binary_path()
    if binary is None:
        return None

    if is_macos():
        return MacNativeHotkey(hotkey_str)
    if is_windows():
        return WindowsHookHotkey(hotkey_str)
    if is_linux():
        return LinuxEvdevHotkey(hotkey_str)
    return None


def is_native_backend_available() -> bool:
    """Return True if a native binary is available for the current platform."""
    return get_native_binary_path() is not None
