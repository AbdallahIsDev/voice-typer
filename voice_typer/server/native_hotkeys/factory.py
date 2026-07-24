"""Factory functions :func:`create_native_backend` and
:func:`is_native_backend_available`. CR-46: adds checksum verification.
"""

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

from .base import SubprocessHotkeyBackend
from .binary_path import get_native_binary_path, verify_native_binary_or_skip
from .linux_backend import LinuxEvdevHotkey
from .mac_backend import MacNativeHotkey
from .windows_backend import WindowsHookHotkey


def is_windows() -> bool:
    return _native_hotkeys_pkg.is_windows()


def is_macos() -> bool:
    return _native_hotkeys_pkg.is_macos()


def is_linux() -> bool:
    return _native_hotkeys_pkg.is_linux()


def create_native_backend(hotkey_str: str) -> SubprocessHotkeyBackend | None:
    """Create a native subprocess backend. CR-46: verifies SHA-256 first."""
    binary = get_native_binary_path()
    if binary is None:
        return None
    if not verify_native_binary_or_skip(binary):
        return None
    if is_macos():
        return MacNativeHotkey(hotkey_str)
    if is_windows():
        return WindowsHookHotkey(hotkey_str)
    if is_linux():
        return LinuxEvdevHotkey(hotkey_str)
    return None


def is_native_backend_available() -> bool:
    """CR-46: available = discoverable AND checksum-verified."""
    binary = get_native_binary_path()
    if binary is None:
        return False
    return verify_native_binary_or_skip(binary)
