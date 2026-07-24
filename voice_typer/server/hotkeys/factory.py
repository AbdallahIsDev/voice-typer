"""Factory function :func:`create_hotkey_backend` — picks the best
backend for the current platform.

Split out from the original ``hotkeys.py`` god-file in Phase 4.5
(ARCH-045).
"""

import os
import sys

from voice_typer.server import hotkeys as _hotkeys_pkg

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


def create_hotkey_backend(hotkey_str: str) -> HotkeyBackend:
    """Create the best hotkey backend for the current platform.

    Selection order (per platform):
    - macOS: ``MacNativeHotkey`` (Swift binary, supports FN key via
      ``NSEvent.modifierFlags.contains(.function)`` + CGEventTap). Falls
      back to ``PynputHotkey`` if the native binary is missing.
    - Windows: ``WindowsHookHotkey`` (C binary using ``WH_KEYBOARD_LL``).
      Falls back to ``WindowsNativeHotkey`` (GetAsyncKeyState polling)
      if the native binary is missing, and to ``PynputHotkey`` if Win32
      is unavailable.
    - Linux/Wayland: Linux evdev native binary (the C binary reading
      ``/dev/input/event*``, subprocess-wrapped via
      ``native_hotkeys.SubprocessHotkeyBackend``). Falls back to
      ``WaylandHotkey`` (Unix socket) if the native binary is missing.
    - Linux/X11: Linux evdev native binary preferred (works on both X11
      and Wayland); falls back to ``PynputHotkey`` if missing.

    The native backends are preferred because they support:
    - The FN key on macOS (firmware-level on Windows/Linux)
    - Modifier-only hotkeys (e.g. ``<alt>``, ``<caps_lock>``) on all
      platforms — pynput's GlobalHotKeys does not support these
    - Key suppression (so the trigger key doesn't reach the foreground
      app) on macOS and Windows
    - Lower CPU usage and lower latency than polling

    FIX-HOTKEY-ARCHITECTURE: when the native binary is NOT built (e.g.
    running from a source checkout without invoking
    ``scripts/build/compile_native.{sh,ps1}``, or on a platform where
    the binary isn't bundled), the factory falls back to the legacy
    backends. On Windows this means ``WindowsNativeHotkey`` uses
    ``GetAsyncKeyState`` polling at 1kHz. This is expected behavior —
    NOT a bug. The polling backend now also supports modifier-only
    hotkeys (``<alt>``, ``<ctrl>``, ``<shift>``, ``<win>``) via
    ``_run_modifier_only_polling_loop``, and suppresses the Caps Lock
    toggle for ``<caps_lock>`` via ``_suppress_caps_lock_toggle``.
    Users who want the full feature set (lower CPU, sub-ms latency,
    native key suppression) should build the native binary.
    """
    # NATIVE-001: try the native subprocess backend first. It supports
    # FN on macOS, modifier-only hotkeys everywhere, and key suppression
    # on macOS/Windows. The legacy backends remain as fallbacks.
    try:
        from voice_typer.server.native_hotkeys import create_native_backend

        native = create_native_backend(hotkey_str)
        if native is not None:
            # Wrap the native backend so it satisfies the HotkeyBackend
            # interface expected by HotkeyDispatcher.
            log.info(
                "[HOTKEY] Using native %s backend for %r",
                type(native).__name__,
                hotkey_str,
            )
            return _NativeBackendAdapter(native)
    except Exception as exc:
        # PVT-G5-081: native-backend unavailability is an EXPECTED fallback
        # (e.g. running from a source checkout without the prebuilt binary).
        # ``log.exception`` emits ERROR + full traceback, which makes this
        # routine path look like a crash in the unified log. Downgrade to
        # INFO and preserve just the exception summary.
        log.info(
            "[HOTKEY] Native backend unavailable (%s); falling back to legacy",
            exc,
        )

    if is_windows():
        # FIX-HOTKEY-ARCHITECTURE: this is the polling fallback. It's
        # expected when the native windows-key-listener.exe binary isn't
        # built. See the class docstring for the feature differences.
        log.info(
            "[HOTKEY] Platform is win32 -> using WindowsNativeHotkey (legacy "
            "polling, native binary not built or unavailable)"
        )
        return WindowsNativeHotkey(hotkey_str)

    # #4 PLAT-WAYLAND: detect Wayland and use Unix socket fallback
    if is_linux():
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        xdg_session = os.environ.get("XDG_SESSION_TYPE", "")
        if wayland_display or xdg_session == "wayland":
            log.info("[HOTKEY] Wayland detected -> using WaylandHotkey (Unix socket, legacy)")
            return WaylandHotkey(hotkey_str)

    log.info("[HOTKEY] Platform is %s -> using PynputHotkey (legacy)", sys.platform)
    return PynputHotkey(hotkey_str)
