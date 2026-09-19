"""IME composition guard for ``WindowsNativeHotkey``."""

from __future__ import annotations

import time

from voice_typer.server import hotkeys as _hotkeys_pkg

from ..base import log  # noqa: F401  # re-exported for tests


# patch-target: tests patch
def _is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


def is_ime_composing() -> bool:
    """Detect if the IME is currently composing."""
    if not _is_windows():
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        himc = imm32.ImmGetContext(hwnd)
        if not himc:
            return False

        try:
            # Check if IME is open
            open_status = imm32.ImmGetOpenStatus(himc)
            if not open_status:
                return False

            # Check if there's a composition string (GCS_COMPSTR = 0x0400)
            comp_len = imm32.ImmGetCompositionStringW(himc, 0x0400, None, 0)
            return comp_len > 0
        finally:
            imm32.ImmReleaseContext(hwnd, himc)
    except Exception:
        return False


def is_ime_composing_throttled(self) -> bool:
    """runs at 8ms cadence (~125 Hz), so calling it every iteration"""
    now = time.monotonic()
    if now - self._last_ime_check_time < 0.05:
        return self._last_ime_composing
    self._last_ime_composing = self._is_ime_composing()
    self._last_ime_check_time = now
    return self._last_ime_composing
