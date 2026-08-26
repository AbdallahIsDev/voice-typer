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
interface; implements the  runtime fallback chain (native →
legacy) and the  macOS Accessibility permission onboarding.

The factory function :func:`create_hotkey_backend` picks the best
available backend for the current platform.

All backends share a common interface:

    - ``start(callback) -> None``
    - ``stop() -> None``
    - ``is_alive() -> bool``
    - ``diagnose() -> str``

Phase 4.5 /  — this file was previously a 2,939-line god-module
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

import sys

from voice_typer.server.hotkeys.base import HotkeyBackend
from voice_typer.server.hotkeys.factory import create_hotkey_backend
from voice_typer.server.hotkeys.native_adapter import _NativeBackendAdapter

# =====================================================================
# CRITICAL — DO NOT REMOVE (2026-07-20)
# =====================================================================
# These three platform predicates (is_windows, is_linux, is_macos) MUST
# remain importable at package level here. They are NOT dead code.
#
# Submodules (factory.py, native_adapter.py, capture.py, pynput_backend.py)
# delegate to them via wrapper lambdas like:
#     is_windows = lambda: _hotkeys_pkg.is_windows()
# And tests patch them via:
#     monkeypatch.setattr("voice_typer.server.hotkeys.is_windows", ...)
#
# If removed, every hotkey registration fails at runtime with:
#     AttributeError: module 'voice_typer.server.hotkeys' has no attribute
#     'is_windows'
# which silently disables ALL hotkeys (caps_lock, ESC cancel, repaste, etc.)
# without crashing the app — the user just gets a non-functional hotkey layer.
#
# They are zero-arg callables resolved through THIS module's namespace on
# every call, so both patch styles keep working:
#   * ``monkeypatch.setattr("...hotkeys.is_windows", ...)`` replaces the
#     package attribute that the submodule wrappers read at call time;
#   * ``monkeypatch.setattr("...hotkeys.sys.platform", ...)`` propagates
#     because the platform_utils bodies read ``sys.platform`` at call time.
#
# The bodies live in platform_utils.py (the centralized platform
# detection helpers); this re-export keeps the historical import surface
# ``from voice_typer.server.hotkeys import is_windows`` intact.
# =====================================================================
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
