"""Hotkey backend abstraction.

Provides platform-aware hotkey listening with these implementations:

- :class:`PynputHotkey` — uses ``pynput.keyboard.GlobalHotKeys`` (cross-platform).
- :class:`WindowsNativeHotkey` — uses Win32 ``RegisterHotKey`` via ctypes
  (Windows only).  Also implements the ``GetAsyncKeyState`` polling
  fallback, the low-level ``WH_KEYBOARD_LL`` hook path, the
  modifier-only polling loop, and the Caps Lock toggle suppression.
- :class:`WaylandHotkey` — listens on a Unix domain socket (Wayland
  fallback).
- :class:`_NativeBackendAdapter` — wraps a native
  ``SubprocessHotkeyBackend`` to satisfy the ``HotkeyBackend``
  interface; implements the GAP-4 runtime fallback chain (native →
  legacy) and the GAP-2 macOS Accessibility permission onboarding.

The factory function :func:`create_hotkey_backend` picks the best
available backend for the current platform.

All backends share a common interface:

    - ``start(callback) -> None``
    - ``stop() -> None``
    - ``is_alive() -> bool``
    - ``diagnose() -> str``

Phase 4.5 / ARCH-045 — this file was previously a 2,939-line god-module
(``voice_typer/server/hotkeys.py``); it has been split into a package
with one module per backend.  This ``__init__.py`` re-exports every
public name that the original module exposed so existing imports of the
form ``from voice_typer.server.hotkeys import X`` keep working without
modification.

Patch-path compatibility: tests use ``patch("voice_typer.server.hotkeys.is_windows")``
and ``monkeypatch.setattr("voice_typer.server.hotkeys.sys.platform", "linux")``.
For the patches to affect production code defined in submodules, the
submodules look up ``is_windows`` / ``is_linux`` / ``is_macos`` via
thin wrapper lambdas that delegate to *this* package's binding at call
time (rather than capturing the function object at import time).  The
``sys`` module is imported here so ``voice_typer.server.hotkeys.sys``
resolves to the real ``sys`` module and ``monkeypatch.setattr`` on
``.sys.platform`` propagates to all callers.

VK code map: ``_VK_MAP`` (defined in :mod:`.win32_vk`) maps pynput-style
lowercase key names to Win32 virtual-key codes.  ``_VK_MAP.get(key_name)``
is the O(1) lookup used by :func:`parse_hotkey_to_win32`.
PLAT-VKMAP: VK codes are mapped from the US keyboard layout; the
``MapVirtualKey`` fallback in :func:`parse_hotkey_to_win32` resolves
printable-character VK codes via the current keyboard layout for non-US
keyboards (German ^/°, French #, etc.).
"""

# `sys` is imported (and re-exported) so that tests using
# ``monkeypatch.setattr("voice_typer.server.hotkeys.sys.platform", "linux")``
# resolve the dotted path to the real ``sys`` module.
import logging
import sys  # noqa: F401 — re-exported for monkeypatch.setattr targets

from voice_typer.server.platform_utils import (
    is_linux,
    is_macos,
    is_windows,
)

# ─── Public API re-exports ──────────────────────────────────────────────────
# Order matters: each submodule imports from earlier ones (base → win32_vk →
# pynput_backend → windows_native → wayland → native_adapter → factory →
# capture).  All submodules also do ``from voice_typer.server import hotkeys
# as _hotkeys_pkg`` to defer ``is_windows`` / ``is_linux`` / ``is_macos``
# lookups to call time, so the package's bindings for those names must be
# in place before the submodules' lambdas ever fire — which they are,
# because the lambdas only execute when the wrapped functions are called
# at runtime, long after this ``__init__.py`` has finished loading.
from .base import HotkeyBackend, log
from .capture import capture_custom_hotkey
from .factory import create_hotkey_backend
from .native_adapter import _NativeBackendAdapter
from .pynput_backend import PynputHotkey, _parse_hotkey_to_pynput
from .wayland import WaylandHotkey
from .win32_vk import (
    _GWLP_USERDATA,
    _KEYEVENTF_KEYUP,
    _MOD_ALT,
    _MOD_ALTGR,
    _MOD_CONTROL,
    _MOD_NOREPEAT,
    _MOD_SHIFT,
    _MOD_WIN,
    _VK_CAPITAL,
    _VK_CONTROL,
    _VK_LWIN,
    _VK_MAP,
    _VK_MAP_LOCK,
    _VK_MENU,
    _VK_RMENU,
    _VK_RWIN,
    _VK_SHIFT,
    _WHC_KEYBOARD_LL,
    _WM_HOTKEY,
    _WM_KEYDOWN,
    _WM_KEYUP,
    _WM_QUIT,
    _WM_SYSKEYDOWN,
    _WM_SYSKEYUP,
    _init_vk_map,
    _win32_vk,
    parse_hotkey_to_vk,
    parse_hotkey_to_win32,
)
from .windows_native import WindowsNativeHotkey

__all__ = [
    # base
    "HotkeyBackend",
    "log",
    # pynput_backend
    "PynputHotkey",
    "_parse_hotkey_to_pynput",
    # win32_vk
    "_GWLP_USERDATA",
    "_KEYEVENTF_KEYUP",
    "_MOD_ALT",
    "_MOD_ALTGR",
    "_MOD_CONTROL",
    "_MOD_NOREPEAT",
    "_MOD_SHIFT",
    "_MOD_WIN",
    "_VK_CAPITAL",
    "_VK_CONTROL",
    "_VK_LWIN",
    "_VK_MAP",
    "_VK_MAP_LOCK",
    "_VK_MENU",
    "_VK_RMENU",
    "_VK_RWIN",
    "_VK_SHIFT",
    "_WHC_KEYBOARD_LL",
    "_WM_HOTKEY",
    "_WM_KEYDOWN",
    "_WM_KEYUP",
    "_WM_QUIT",
    "_WM_SYSKEYDOWN",
    "_WM_SYSKEYUP",
    "_init_vk_map",
    "_win32_vk",
    "parse_hotkey_to_vk",
    "parse_hotkey_to_win32",
    # windows_native
    "WindowsNativeHotkey",
    # native_adapter
    "_NativeBackendAdapter",
    # wayland
    "WaylandHotkey",
    # factory
    "create_hotkey_backend",
    # capture
    "capture_custom_hotkey",
    # platform_utils (re-exported for patch compatibility)
    "is_linux",
    "is_macos",
    "is_windows",
    # sys (re-exported for monkeypatch.setattr compatibility)
    "sys",
]
