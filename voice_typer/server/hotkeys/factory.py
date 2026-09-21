"""Factory function :func:`create_hotkey_backend`, picks the best
Split out from the original ``hotkeys.py`` module
"""

import sys

from voice_typer.server import hotkeys as _hotkeys_pkg
from voice_typer.server.platform_utils import is_wayland_session
from voice_typer.server.tray_hotkey import format_hotkey_label

from .base import HotkeyBackend, log
from .native_adapter import _NativeBackendAdapter
from .pynput_backend import PynputHotkey
from .wayland import WaylandHotkey
from .windows_native import WindowsNativeHotkey


# See pynput_backend.py for the rationale.
def is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


def is_linux() -> bool:
    return _hotkeys_pkg.is_linux()


def create_hotkey_backend(hotkey_str: str, role: str | None = None) -> HotkeyBackend:
    """``GetAsyncKeyState`` polling at ~125 Hz (8 ms cadence via
    ``kernel32.Sleep(8)`` with ``timeBeginPeriod(8)``, see
    """
    # try the native subprocess backend first. It supports
    try:
        from voice_typer.server.native_hotkeys import create_native_backend

        native = create_native_backend(hotkey_str)
        if native is not None:
            # Wrap the native backend so it satisfies the HotkeyBackend
            log.debug(
                "[HOTKEY] Using native %s backend for %s (role=%s)",
                type(native).__name__,
                format_hotkey_label(hotkey_str),
                role,
            )
            # pass ``role`` through so the adapter can propagate
            return _NativeBackendAdapter(native, role=role)
    except Exception as exc:
        # native-backend unavailability is an EXPECTED fallback
        log.info(
            "[HOTKEY] Native backend unavailable (%s); falling back to legacy",
            exc,
        )

    if is_windows():
        # this is the polling fallback. It's
        log.info(
            "[HOTKEY] Platform is win32 -> using WindowsNativeHotkey (legacy "
            "polling, native binary not built or unavailable)"
        )
        return WindowsNativeHotkey(hotkey_str)

    # #4 PLAT-WAYLAND: detect Wayland and use Unix socket fallback.
    if is_linux() and is_wayland_session():
        # Wayland compositors do NOT expose a standard global-hotkey
        log.warning(
            "[HOTKEY] Wayland detected, global hotkeys may not work. "
            "Consider installing wlr-which-key or using the evdev backend "
            "(requires input group)."
        )
        # Caps Lock on Wayland CANNOT be suppressed, ``WaylandHotkey``
        if hotkey_str and "caps_lock" in hotkey_str.lower():
            log.warning(
                "[HOTKEY] On Wayland, Caps Lock cannot be suppressed, "
                "your text will be capitalized. Bind Alt or a function "
                "key instead, or remap Caps Lock via your compositor's "
                "settings."
            )
        log.info(
            "[HOTKEY] Wayland detected -> using WaylandHotkey (Unix socket, legacy, role=%r)",
            role,
        )
        # pass ``role`` so the socket filename is per-backend.
        return WaylandHotkey(hotkey_str, role=role)

    log.info("[HOTKEY] Platform is %s -> using PynputHotkey (legacy)", sys.platform)
    return PynputHotkey(hotkey_str)
