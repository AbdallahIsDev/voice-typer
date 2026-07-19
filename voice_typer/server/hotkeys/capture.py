"""Custom-hotkey capture helper (Windows GetAsyncKeyState polling).

Split out from the original ``hotkeys.py`` god-file in Phase 4.5
(ARCH-045).
"""

import ctypes
import time

from voice_typer.server import hotkeys as _hotkeys_pkg

from .base import log
from .win32_vk import (
    _MOD_ALT,
    _MOD_ALTGR,
    _MOD_CONTROL,
    _MOD_SHIFT,
    _VK_MAP,
    _init_vk_map,
)

# See pynput_backend.py for the rationale.
is_windows = lambda: _hotkeys_pkg.is_windows()


# ─── PLAT-VKMAP: Custom hotkey capture ──────────────────────────────────────


def capture_custom_hotkey(timeout: float = 10.0) -> tuple[int, int, str] | None:
    """PLAT-VKMAP: Capture a keystroke via GetAsyncKeyState polling.

    On Windows, polls all VK codes at ~50Hz to detect which key is
    pressed along with modifier state. This is useful for non-US
    keyboards where the static VK map in parse_hotkey_to_win32() may
    not produce the correct VK code.

    Returns ``(vk_code, modifiers, description)`` on success, or
    ``None`` on timeout or non-Windows platforms.

    The *modifiers* value is a bitmask of _MOD_ALT, _MOD_CONTROL,
    _MOD_SHIFT, _MOD_WIN, _MOD_ALTGR flags suitable for
    RegisterHotKey().

    The *description* is a human-readable string like "AltGr+1".

    Parameters
    ----------
    timeout : float
        Maximum seconds to wait for a key press. Default 10s.

    Usage
    -----
    >>> vk, mods, desc = capture_custom_hotkey()
    >>> if vk is not None:
    ...     print(f"Captured: VK=0x{vk:X}, mods=0x{mods:X}, desc={desc}")
    """
    if not is_windows():
        log.warning("[HOTKEY] Custom hotkey capture is only available on Windows")
        return None

    from ctypes.wintypes import DWORD, INT

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    user32.GetAsyncKeyState.argtypes = [INT]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    kernel32.Sleep.argtypes = [DWORD]
    kernel32.Sleep.restype = None

    # VK codes to poll (skip modifier keys 0x10-0x12, 0xA5, 0x5B/5C)
    _modifier_vks = {0x10, 0x11, 0x12, 0xA5, 0x5B, 0x5C}

    log.info("[HOTKEY-CAPTURE] Waiting for keystroke (timeout=%.0fs)...", timeout)
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        # Check all VK codes 0x01..0xFF for a key press
        for vk in range(1, 256):
            if vk in _modifier_vks:
                continue
            state = user32.GetAsyncKeyState(vk)
            if state & 0x8000:
                # Key is pressed — capture modifiers
                mods = 0
                mod_names = []
                if user32.GetAsyncKeyState(0x11) & 0x8000:  # Ctrl
                    mods |= _MOD_CONTROL
                    mod_names.append("Ctrl")
                if user32.GetAsyncKeyState(0x10) & 0x8000:  # Shift
                    mods |= _MOD_SHIFT
                    mod_names.append("Shift")
                if user32.GetAsyncKeyState(0x12) & 0x8000:  # Alt
                    mods |= _MOD_ALT
                    mod_names.append("Alt")
                if user32.GetAsyncKeyState(0xA5) & 0x8000:  # AltGr/Right Alt
                    mods |= _MOD_ALTGR
                    mod_names.append("AltGr")

                # Build description
                _init_vk_map()
                vk_name = None
                for name, code in _VK_MAP.items():
                    if code == vk:
                        vk_name = name
                        break
                if vk_name is None:
                    vk_name = f"0x{vk:02X}"

                mod_str = "+".join(mod_names + [vk_name]) if mod_names else vk_name
                log.info(
                    "[HOTKEY-CAPTURE] Captured: VK=0x%X, mods=0x%X, desc=%s",
                    vk,
                    mods,
                    mod_str,
                )

                # Wait for key release to avoid re-triggering
                while user32.GetAsyncKeyState(vk) & 0x8000:
                    kernel32.Sleep(20)

                return (vk, mods, mod_str)

        kernel32.Sleep(20)  # ~50Hz polling

    log.info("[HOTKEY-CAPTURE] Timed out after %.0fs", timeout)
    return None
