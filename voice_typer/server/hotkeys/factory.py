"""Factory function :func:`create_hotkey_backend` — picks the best
backend for the current platform.

Split out from the original ``hotkeys.py`` god-file in Phase 4.5
().
"""

import sys

from voice_typer.server import hotkeys as _hotkeys_pkg
from voice_typer.server.platform_utils import is_wayland_session

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
    ``GetAsyncKeyState`` polling at ~125 Hz (8 ms cadence via
    ``kernel32.Sleep(8)`` with ``timeBeginPeriod(8)`` — see
    ``WindowsNativeHotkey._run_polling_loop``). This is expected
    behavior — NOT a bug. The polling backend now also supports
    modifier-only hotkeys (``<alt>``, ``<ctrl>``, ``<shift>``,
    ``<win>``) via ``_run_modifier_only_polling_loop``, and suppresses
    the Caps Lock toggle for ``<caps_lock>`` via
    ``_suppress_caps_lock_toggle``. Users who want the full feature
    set (lower CPU, sub-ms latency, native key suppression) should
    build the native binary.
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
                "[HOTKEY] Using native %s backend for %r (role=%r)",
                type(native).__name__,
                hotkey_str,
                role,
            )
            # pass ``role`` through so the adapter can propagate
            # it to a legacy WaylandHotkey if the native backend
            # permanently fails and the adapter swaps to legacy.
            return _NativeBackendAdapter(native, role=role)
    except Exception as exc:
        # native-backend unavailability is an EXPECTED fallback
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

    # #4 PLAT-WAYLAND: detect Wayland and use Unix socket fallback.
    # Delegate env-var detection to platform_utils.is_wayland_session
    # (single source of truth — handles XDG_SESSION_TYPE + WAYLAND_DISPLAY,
    # case-insensitive). The is_linux() gate is retained so tests that
    # mock is_linux can still control the platform branch.
    if is_linux() and is_wayland_session():
        # Wayland compositors do NOT expose a standard global-hotkey
        # portal (the xdg-desktop-portal GlobalShortcuts interface is
        # opt-in and not all compositors implement it). The
        # ``WaylandHotkey`` backend listens on a Unix socket — it only
        # fires when an external tool (wlr-which-key, a shell script,
        # etc.) connects and sends commands. Without that external tool,
        # the hotkey is effectively dead. Surface this at register time
        # so the user knows to either install the external tool or
        # switch to the evdev backend (which requires the ``input``
        # group).
        log.warning(
            "[HOTKEY] Wayland detected — global hotkeys may not work. "
            "Consider installing wlr-which-key or using the evdev backend "
            "(requires input group)."
        )
        # Caps Lock on Wayland CANNOT be suppressed — ``WaylandHotkey``
        # has no key-suppression mechanism (the Unix socket backend just
        # receives commands, it doesn't intercept keystrokes). The OS
        # will toggle caps state on every press, so the user's dictated
        # text will be capitalized. Warn the user at register time and
        # suggest alternatives (Alt, a function key, or remapping Caps
        # Lock via the compositor's settings).
        if hotkey_str and "caps_lock" in hotkey_str.lower():
            log.warning(
                "[HOTKEY] On Wayland, Caps Lock cannot be suppressed — "
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
