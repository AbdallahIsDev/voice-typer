"""IME composition guard for ``WindowsNativeHotkey``.

Extracted from the original ``windows_native.py`` god-class
split). Detects when the IME is in composition mode (e.g. typing
CJK characters) so the polling loop can suppress hotkey triggers
during composition — otherwise GetAsyncKeyState may fire hotkey
triggers for keys that are part of the composition string.
"""

from __future__ import annotations

import time

from voice_typer.server import hotkeys as _hotkeys_pkg

from ..base import log  # noqa: F401  # re-exported for tests


# patch-target: tests patch
# ``voice_typer.server.hotkeys.is_windows`` and expect the patch to
# take effect on ``WindowsNativeHotkey._is_ime_composing()``. The
# wrapper delegates to the package's binding at call time so the
# patch propagates.
def _is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


def is_ime_composing() -> bool:
    """Detect if the IME is currently composing.

    When the IME is in composition mode (e.g. typing CJK characters),
    GetAsyncKeyState may fire hotkey triggers for keys that are part
    of the composition string. We suppress hotkey triggers during
    IME composition to avoid false-fires.

    Uses ImmGetContext + ImmGetCompositionStringW or ImmGetOpenStatus
    on Windows. Returns False on non-Windows or on failure.
    """
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
    """Throttled wrapper around ``_is_ime_composing()``.

    The underlying staticmethod makes 5 syscalls per call
    (GetForegroundWindow, ImmGetContext, ImmGetOpenStatus,
    ImmGetCompositionStringW, ImmReleaseContext). The polling loop
    runs at 8ms cadence (~125 Hz), so calling it every iteration
    would be ~625 syscalls/sec. This wrapper re-queries at most
    every 50ms (20 Hz) and returns the cached result between queries.

    50ms latency is invisible to the user because IME state changes
    at human typing speed (each key press is ~50-150ms apart).

    NOTE: calls ``self._is_ime_composing()`` (NOT the module-level
    function) so test patches that override the instance attribute
    take effect.
    """
    now = time.monotonic()
    if now - self._last_ime_check_time < 0.05:
        return self._last_ime_composing
    self._last_ime_composing = self._is_ime_composing()
    self._last_ime_check_time = now
    return self._last_ime_composing
