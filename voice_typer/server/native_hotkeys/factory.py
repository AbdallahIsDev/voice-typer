"""Factory functions :func:`create_native_backend` and
:func:`is_native_backend_available`. Adds checksum verification
(SHA-256 manifest gate), version comparison, and macOS dev-mode stub warning.
"""

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
    """Detect dev mode (source-tree layout) for warning.

    Dev mode is heuristically detected by the presence of the
    ``voice_typer/server/native/`` source directory alongside this
    package. In a PyInstaller bundle the source tree is absent (only
    the compiled binaries are extracted to ``_MEIPASS``), so this
    returns False in production builds.
    """
    from pathlib import Path

    native_src_dir = Path(__file__).resolve().parent.parent / "native"
    return native_src_dir.is_dir()


def _manifest_version_for_binary(binary_path) -> str | None:
    """Look up the manifest's ``version`` field for the given binary.

    Mirrors :func:`binary_path.get_expected_sha256`'s name-resolution
    logic but returns the ``version`` field instead of the sha256.
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

    (macOS stub warning): if no binary is found AND we're on
    macOS AND we're in dev mode, log a WARNING explaining that the
    macOS native binary is a placeholder and the legacy backend will
    be used. This mirrors the Linux/Windows pattern where a missing
    binary in production is a hard error but a missing binary in dev
    mode (where cross-compilation may not be available) is a soft
    warning. Returns ``None`` so the caller falls back to the legacy
    backend (pynput / WindowsNativeHotkey / WaylandHotkey).

    (version check): the manifest's ``version`` field for the
    binary's filename is forwarded to the backend via the
    ``_expected_version`` attribute. The backend's
    ``_on_version_event`` handler compares the binary's runtime-reported
    VERSION against this expected value AFTER the binary has started
    and emits a warning log on mismatch. The comparison is deferred to
    ``_on_version_event`` (rather than done here) because the binary
    only emits VERSION after READY, which happens after ``start()`` —
    the factory creates the backend but doesn't start it.
    """
    binary = get_native_binary_path()
    if binary is None:
        # warn specifically on macOS dev mode where the stub
        # script (or absent binary) is the known dev-mode state.
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
        # can compare it against the binary's runtime-reported VERSION
        # line in ``_on_version_event``. ``None`` here means "manifest
        # didn't have a version for this binary" — the backend treats
        # that as "skip the comparison" rather than "mismatch".
        expected = _manifest_version_for_binary(binary)
        backend._expected_version = expected  # type: ignore[attr-defined]
    return backend


def is_native_backend_available() -> bool:
    """available = discoverable AND checksum-verified."""
    binary = get_native_binary_path()
    if binary is None:
        return False
    return verify_native_binary_or_skip(binary)
