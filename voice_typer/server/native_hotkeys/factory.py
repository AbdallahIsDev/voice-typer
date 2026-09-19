"""Factory functions :func:`create_native_backend` and"""

import logging

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

from .base import SubprocessHotkeyBackend
from .binary_path import (
    get_native_binary_path,
    load_binary_manifest,
    verify_native_binary_or_skip,
)
from .linux_backend import LinuxEvdevHotkey
from .mac_backend import MacNativeHotkey
from .windows_backend import WindowsHookHotkey

log = logging.getLogger(__name__)


def is_windows() -> bool:
    return _native_hotkeys_pkg.is_windows()


def is_macos() -> bool:
    return _native_hotkeys_pkg.is_macos()


def is_linux() -> bool:
    return _native_hotkeys_pkg.is_linux()


def _is_dev_mode() -> bool:
    """Detect dev mode (source-tree layout) for warning."""
    from pathlib import Path

    native_src_dir = Path(__file__).resolve().parent.parent / "native"
    return native_src_dir.is_dir()


def _manifest_version_for_binary(binary_path) -> str | None:
    """Look up the manifest's ``version`` field for the given binary.

    Returns ``None`` if the manifest or entry is missing.
    """
    manifest = load_binary_manifest()
    if manifest is None:
        return None
    binaries = manifest.get("binaries", {})
    if not isinstance(binaries, dict):
        return None
    entry = binaries.get(binary_path.name)
    if not isinstance(entry, dict):
        return None
    version = entry.get("version")
    if isinstance(version, str) and version:
        return version.strip()
    return None


def create_native_backend(hotkey_str: str) -> SubprocessHotkeyBackend | None:
    """Create a native subprocess backend. : verifies SHA-256 first."""
    binary = get_native_binary_path()
    if binary is None:
        # warn specifically on macOS dev mode where the stub
        if is_macos() and _is_dev_mode():
            log.warning(
                "[NATIVE-HOTKEY] macOS native binary not found in dev mode "
                "(voice_typer/server/native/macos-key-listener missing or is a "
                "placeholder stub). Native hotkey backend will NOT be used; "
                "falling back to the legacy pynput backend. Build the real "
                "binary on a macOS host via scripts/build/compile_native.sh.",
            )
        return None
    if not verify_native_binary_or_skip(binary):
        return None
    backend: SubprocessHotkeyBackend | None = None
    if is_macos():
        backend = MacNativeHotkey(hotkey_str, binary_path=binary)
    elif is_windows():
        backend = WindowsHookHotkey(hotkey_str, binary_path=binary)
    elif is_linux():
        backend = LinuxEvdevHotkey(hotkey_str, binary_path=binary)
    if backend is not None:
        # stash the manifest's expected version so the backend
        expected = _manifest_version_for_binary(binary)
        backend._expected_version = expected  # type: ignore[attr-defined]
    return backend


def is_native_backend_available() -> bool:
    """available = discoverable AND checksum-verified."""
    binary = get_native_binary_path()
    if binary is None:
        return False
    return verify_native_binary_or_skip(binary)
