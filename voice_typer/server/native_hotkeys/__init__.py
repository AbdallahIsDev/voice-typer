"""Native subprocess hotkey backends."""

# `sys` is imported (and re-exported) so that tests using
import subprocess  # noqa: F401, re-exported for monkeypatch.setattr targets
import sys  # noqa: F401, re-exported for monkeypatch.setattr targets

from voice_typer.server.platform_utils import (
    is_linux,
    is_macos,
    is_windows,
)

# Order matters: each submodule imports from earlier ones
from .base import (
    MAX_RESTART_ATTEMPTS,
    READY_TIMEOUT_SECONDS,
    RESTART_DELAY_BASE_SECONDS,
    SubprocessHotkeyBackend,
)
from .binary_path import (
    _BINARY_NAMES,
    _MANIFEST_PATH,
    _is_trusted_path_override,
    get_expected_sha256,
    get_native_binary_path,
    load_binary_manifest,
    verify_native_binary,
    verify_native_binary_or_skip,
)
from .factory import create_native_backend, is_native_backend_available
from .linux_backend import LinuxEvdevHotkey
from .mac_backend import MacNativeHotkey
from .modifiers import (
    _MOD_CANONICAL_MAP,
    _canonical_modifier,
    _canonical_modifier_name_for_token,
    _key_name_to_token,
    _modifier_to_token,
)
from .recorder import NativeHotkeyRecorder
from .spec_parser import _normalize_key_name, log, parse_hotkey_spec
from .windows_backend import WindowsHookHotkey

__all__ = [
    # spec_parser
    "parse_hotkey_spec",
    "_normalize_key_name",
    "log",
    # binary_path
    "_BINARY_NAMES",
    "_MANIFEST_PATH",
    "_is_trusted_path_override",
    "get_expected_sha256",
    "get_native_binary_path",
    "load_binary_manifest",
    "verify_native_binary",
    "verify_native_binary_or_skip",
    # modifiers
    "_MOD_CANONICAL_MAP",
    "_canonical_modifier",
    "_canonical_modifier_name_for_token",
    "_modifier_to_token",
    "_key_name_to_token",
    # base
    "MAX_RESTART_ATTEMPTS",
    "RESTART_DELAY_BASE_SECONDS",
    "READY_TIMEOUT_SECONDS",
    "SubprocessHotkeyBackend",
    # mac / windows / linux backends
    "MacNativeHotkey",
    "WindowsHookHotkey",
    "LinuxEvdevHotkey",
    # factory
    "create_native_backend",
    "is_native_backend_available",
    # recorder
    "NativeHotkeyRecorder",
    # platform_utils (re-exported for patch compatibility)
    "is_linux",
    "is_macos",
    "is_windows",
    # sys (re-exported for monkeypatch.setattr compatibility)
    "sys",
    # subprocess (re-exported for monkeypatch.setattr compatibility)
    "subprocess",
]
