"""Clipboard management and safe auto-paste.

Key behavior:
- copy() ALWAYS puts text on the clipboard.
- paste() only sends a paste keystroke when it is safe to do so:
    * If focus detection is available (Windows): only paste when a text
      input is confirmed focused.  If not, silently skip.
    * If focus detection is not available: paste only when the caller
      explicitly opted in (paste_on_stop config).
    * Terminal emulators use Shift+Insert instead of Ctrl+V.
- On any failure the clipboard text is preserved.
"""

import logging
import sys
import time

import pyperclip
from pynput.keyboard import Key, Controller

from voice_typer.server.focus import is_text_input_focused

log = logging.getLogger(__name__)


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
    """Handles copying text to clipboard and safely pasting into the focused app."""

    def __init__(self, paste_enabled: bool = True, unsafe_paste_on_unknown_focus: bool = False):
        self.paste_enabled = paste_enabled
        self.unsafe_paste_on_unknown_focus = unsafe_paste_on_unknown_focus
        self._keyboard = Controller()

    @staticmethod
    def _is_terminal_process(process_name: str | None) -> bool:
        """Return True if the process name looks like a terminal emulator."""
        if not process_name:
            return False
        return process_name.lower().strip() in _TERMINAL_PROCESS_NAMES

    def _detect_focused_process(self) -> str | None:
        """Best-effort detection of the focused process name (Windows only)."""
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
            try:
                from voice_typer.server.focus import _get_process_name
                return _get_process_name(user32, kernel32, hwnd)
            except Exception:
                return None
        except Exception:
            return None

    def copy(self, text: str) -> bool:
        """Copy text to clipboard.  Returns True on success."""
        if not text:
            return False
        try:
            pyperclip.copy(text)
            log.info("Copied %d chars to clipboard", len(text))
            return True
        except Exception as e:
            log.error("Failed to copy to clipboard: %s", e)
            return False

    def paste(self) -> bool:
        """Attempt to paste into the focused field.

        Returns True if a keystroke was sent, False if paste was skipped
        (no text input focused, detection unavailable and paste disabled,
        or paste_enabled is False).

        On Windows, includes a short retry: if focus detection returns False
        on the first attempt (which can happen if focus is briefly disrupted
        by the recording-stop or tray-update event), we wait and retry
        multiple times. This handles the common case where the original text
        field regains focus almost immediately.
        """
        if not self.paste_enabled:
            log.info("Paste disabled by config -- skipping keystroke")
            return False

        focused = is_text_input_focused()

        # Retry on Windows if focus was just disrupted
        if focused is False and sys.platform == "win32":
            delays = [0.15, 0.3, 0.5]  # 150ms, 300ms, 500ms
            for i, delay in enumerate(delays, 1):
                log.info(
                    "No text input focused on attempt %d -- "
                    "retrying after %.0f ms (focus may be disrupted)",
                    i + 1, delay * 1000
                )
                time.sleep(delay)
                focused = is_text_input_focused()
                if focused is not False:
                    log.info("Focus recovered on retry %d", i + 1)
                    break

        if focused is False:
            log.info("No text input focused -- skipping paste (clipboard has the text)")
            return False
        if focused is None:
            if self.unsafe_paste_on_unknown_focus:
                log.info(
                    "Focus detection unavailable -- attempting paste "
                    "(unsafe_paste_on_unknown_focus=True)"
                )
            else:
                log.info(
                    "Focus detection unavailable -- skipping paste "
                    "(set unsafe_paste_on_unknown_focus=True to override)"
                )
                return False

        # focused is True, or None with opt-in -> attempt paste

        try:
            time.sleep(0.02)  # minimal settle before keystroke
            if not is_text_input_focused() and not self.unsafe_paste_on_unknown_focus:
                log.info("Focus lost during paste delay -- skipping")
                return False
            # Detect if focused process is a terminal
            process_name = self._detect_focused_process()
            is_terminal = self._is_terminal_process(process_name)

            if is_terminal:
                # Terminals use Shift+Insert for paste
                if sys.platform == "darwin":
                    # macOS terminals: Cmd+Shift+V
                    self._keyboard.press(Key.cmd)
                    self._keyboard.press(Key.shift)
                    self._keyboard.press("v")
                    self._keyboard.release("v")
                    self._keyboard.release(Key.shift)
                    self._keyboard.release(Key.cmd)
                else:
                    # Linux/Windows terminals: Shift+Insert
                    self._keyboard.press(Key.shift)
                    self._keyboard.press(Key.insert)
                    self._keyboard.release(Key.insert)
                    self._keyboard.release(Key.shift)
            elif sys.platform == "darwin":
                self._keyboard.press(Key.cmd)
                self._keyboard.press("v")
                self._keyboard.release("v")
                self._keyboard.release(Key.cmd)
            else:
                self._keyboard.press(Key.ctrl)
                self._keyboard.press("v")
                self._keyboard.release("v")
                self._keyboard.release(Key.ctrl)

            log.info("Sent paste keystroke")
            return True
        except Exception as e:
            log.warning("Auto-paste failed (clipboard still has the text): %s", e)
            return False
