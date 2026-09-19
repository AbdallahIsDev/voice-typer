"""Hotkey backend abstraction."""

# `sys` is imported (and re-exported) so that tests using

import sys

from voice_typer.server.hotkeys.base import HotkeyBackend
from voice_typer.server.hotkeys.factory import create_hotkey_backend
from voice_typer.server.hotkeys.native_adapter import _NativeBackendAdapter

# CRITICAL, DO NOT REMOVE (2026-07-20)
from voice_typer.server.hotkeys.pynput_backend import (  # noqa: E402
    PynputHotkey,
    _parse_hotkey_to_pynput,
)
from voice_typer.server.hotkeys.wayland import WaylandHotkey  # noqa: E402
from voice_typer.server.hotkeys.win32_vk import (  # noqa: E402
    _MOD_ALT,
    _MOD_ALTGR,
    _MOD_CONTROL,
    _MOD_SHIFT,
    _MOD_WIN,
    _VK_MAP,
    _VK_MAP_LOCK,
    _init_vk_map,
    parse_hotkey_to_vk,
    parse_hotkey_to_win32,
)
from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey  # noqa: E402
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

__all__ = [
    "sys",
    "HotkeyBackend",
    "create_hotkey_backend",
    "PynputHotkey",
    "_parse_hotkey_to_pynput",
    "WindowsNativeHotkey",
    "WaylandHotkey",
    "_NativeBackendAdapter",
    "_VK_MAP",
    "_VK_MAP_LOCK",
    "_init_vk_map",
    "parse_hotkey_to_vk",
    "parse_hotkey_to_win32",
    "_MOD_ALT",
    "_MOD_ALTGR",
    "_MOD_CONTROL",
    "_MOD_SHIFT",
    "_MOD_WIN",
    "is_windows",
    "is_linux",
    "is_macos",
]
