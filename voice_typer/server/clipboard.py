"""Clipboard management and auto-paste.

- copy() ALWAYS puts text on the clipboard.
- paste() ALWAYS sends a paste keystroke (Ctrl+V or platform equivalent).
  Terminal emulators use Shift+Insert instead of Ctrl+V.
- On Windows, paste uses Win32 SendInput with all four events (Ctrl down,
  V down, V up, Ctrl up) submitted as a single atomic INPUT batch to
  avoid applications interpreting key-up as a duplicate paste event.
"""

import logging
import sys
import time

import pyperclip

log = logging.getLogger(__name__)


# Lazy-import pynput at instance creation time, not module import time.
# pynput.keyboard imports a platform backend (X11 on Linux, IOKit on mac,
# Win32 on Windows) that requires a running display / window manager.
# Importing at module level breaks `python -m voice_typer --version`
# in headless containers / SSH sessions without DISPLAY.
_Key = None  # type: ignore[assignment]
_Controller = None  # type: ignore[assignment]


def _ensure_pynput_imported():
    """Lazily import pynput.keyboard.Key and Controller on first use."""
    global _Key, _Controller
    if _Key is not None and _Controller is not None:
        return
    from pynput.keyboard import Controller as _C
    from pynput.keyboard import Key as _K
    _Key = _K
    _Controller = _C


# Terminal process names (lowercase, with extension) that require
# Shift+Insert instead of Ctrl+V for paste.
_TERMINAL_PROCESS_NAMES: set[str] = {
    "windowsterminal.exe",
    "warp.exe",
    "alacritty.exe",
    "wezterm-gui.exe",
    "conemu64.exe",
    "conemu.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "alacritty",
    "kitty",
    "xterm",
    "rxvt",
    "tilix",
    "terminator",
    "foot",
    "wezterm",
}


class ClipboardManager:
    """Handles copying text to clipboard and pasting into the focused app."""

    _PASTE_RATE_LIMIT = 1.0

    def __init__(self, paste_enabled: bool = True):
        self.paste_enabled = paste_enabled
        # Lazy-import pynput so the module can load headless. The actual
        # Controller() instantiation still requires a display (will raise
        # at instance construction time, NOT at module import time).
        _ensure_pynput_imported()
        self._keyboard = _Controller() if _Controller is not None else None
        self._last_paste_time: float = 0.0

    @staticmethod
    def _is_terminal_process(process_name: str | None) -> bool:
        if not process_name:
            return False
        return process_name.lower().strip() in _TERMINAL_PROCESS_NAMES

    @staticmethod
    def _detect_focused_process() -> str | None:
        """Detect the focused process name (Windows only)."""
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return None

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not h_process:
                return None

            try:
                size = wintypes.DWORD(512)
                buf = ctypes.create_unicode_buffer(512)
                if kernel32.QueryFullProcessImageNameW(h_process, 0, buf, ctypes.byref(size)):
                    return buf.value.rsplit("\\", 1)[-1].lower()
            finally:
                kernel32.CloseHandle(h_process)
        except Exception:
            pass
        return None

    def copy(self, text: str) -> bool:
        if not text:
            return False
        try:
            pyperclip.copy(text)
            log.info("[CLIPBOARD] Copied %d chars to clipboard", len(text))
            return True
        except Exception as e:
            log.error("[CLIPBOARD] Failed to copy to clipboard: %s", e)
            return False

    def paste(self) -> bool:
        """Send a paste keystroke into the focused window.

        Returns True if a keystroke was sent, False if paste is disabled
        or rate-limited.
        """
        now = time.monotonic()
        if now - self._last_paste_time < self._PASTE_RATE_LIMIT:
            log.info(
                "[CLIPBOARD] Paste rate-limited (%.0f ms since last paste)",
                (now - self._last_paste_time) * 1000,
            )
            return False

        if not self.paste_enabled:
            log.info("[CLIPBOARD] Paste disabled by config -- skipping keystroke")
            return False

        try:
            time.sleep(0.02)

            process_name = self._detect_focused_process()
            is_terminal = self._is_terminal_process(process_name)

            if is_terminal:
                if sys.platform == "darwin":
                    self._keyboard.press(_Key.cmd)
                    self._keyboard.press(_Key.shift)
                    self._keyboard.press("v")
                    self._keyboard.release("v")
                    self._keyboard.release(_Key.shift)
                    self._keyboard.release(_Key.cmd)
                else:
                    self._send_keystroke_sequence(_Key.shift, _Key.insert)
            elif sys.platform == "darwin":
                self._send_keystroke_sequence(_Key.cmd, "v")
            elif sys.platform == "win32":
                self._send_ctrl_v_win32()
            else:
                self._send_keystroke_sequence(_Key.ctrl, "v")

            self._last_paste_time = time.monotonic()
            log.info("[CLIPBOARD] Sent paste keystroke")
            return True
        except Exception as e:
            log.warning("[CLIPBOARD] Auto-paste failed (clipboard still has the text): %s", e)
            return False

    def _send_keystroke_sequence(self, modifier, char) -> None:
        self._keyboard.press(modifier)
        self._keyboard.press(char)
        self._keyboard.release(char)
        self._keyboard.release(modifier)

    def _send_ctrl_v_win32(self) -> None:
        """Send Ctrl+V via a single atomic SendInput batch."""
        import ctypes

        from pynput._util.win32 import (
            INPUT,
            KEYBDINPUT,
            INPUT_union,
            SendInput,
        )

        VK_CONTROL = 0x11
        VK_V = 0x56

        events = (INPUT * 4)(
            INPUT(
                INPUT.KEYBOARD,
                INPUT_union(ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
            ),
            INPUT(
                INPUT.KEYBOARD,
                INPUT_union(ki=KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
            ),
            INPUT(
                INPUT.KEYBOARD,
                INPUT_union(ki=KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
            ),
            INPUT(
                INPUT.KEYBOARD,
                INPUT_union(ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
            ),
        )

        result = SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
        if result != 4:
            log.warning(
                "[CLIPBOARD] SendInput returned %d (expected 4) — fallback to pynput",
                result,
            )
            self._send_keystroke_sequence(_Key.ctrl, "v")
