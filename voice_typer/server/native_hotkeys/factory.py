"""Factory functions :func:`create_native_backend` and
func:`is_native_backend_available`. : adds checksum verification.
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
    """Create a native subprocess backend. : verifies SHA-256 first.

    The SHA-256-verified ``binary`` Path discovered above is forwarded
    to each platform backend's constructor as ``binary_path=`` so the
    backend uses the exact verified file when it spawns the subprocess.
    Pre-fix the constructor ignored the factory's discovery and
    re-ran ``get_native_binary_path()`` from ``base.__init__`` — that
    re-discovery was a TOCTOU window between the factory's verification
    and the backend's spawn, and also discarded the factory's work
    (the verifier had already been paid for). ``base.SubprocessHotkeyBackend``
    still accepts ``binary_path=None`` for tests that construct backends
    directly without going through the factory; when the factory is the
    caller it always supplies the verified Path.
    """
    binary = get_native_binary_path()
    if binary is None:
        return None
    if not verify_native_binary_or_skip(binary):
        return None
    if is_macos():
        return MacNativeHotkey(hotkey_str, binary_path=binary)
    if is_windows():
        return WindowsHookHotkey(hotkey_str, binary_path=binary)
    if is_linux():
        return LinuxEvdevHotkey(hotkey_str, binary_path=binary)
    return None


def is_native_backend_available() -> bool:
    """available = discoverable AND checksum-verified."""
    binary = get_native_binary_path()
    if binary is None:
        return False
    return verify_native_binary_or_skip(binary)
