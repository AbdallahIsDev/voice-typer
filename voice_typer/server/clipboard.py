"""Clipboard management and auto-paste.

- copy() ALWAYS puts text on the clipboard.
- paste() ALWAYS sends a paste keystroke (Ctrl+V or platform equivalent).
  Terminal emulators use Shift+Insert instead of Ctrl+V.
- On Windows, paste uses Win32 SendInput with all four events (Ctrl down,
  V down, V up, Ctrl up) submitted as a single atomic INPUT batch to
  avoid applications interpreting key-up as a duplicate paste event.

PLAT-001: On Windows, we always prefer SendInput over pynput.keyboard.Controller
for sending keystrokes.  pynput uses SendInput internally on Windows, but when
UIPI (User Interface Privilege Isolation) blocks it (e.g. targeting an elevated
process from a non-elevated one), pynput silently fails.  Our _send_ctrl_v_win32()
uses the same Win32 SendInput API directly and logs the failure, then falls back
to the pynput path as a last resort.

PLAT-027: All direct ctypes.windll.user32 clipboard calls are wrapped in
the Win32Clipboard context manager, which handles OpenClipboard/CloseClipboard
lifecycle, EmptyClipboard, and GetClipboardSequenceNumber.

PLAT-CONTENT: We do not detect contentEditable elements. Pasted text is always
plain text. In a future version, consider detecting contentEditable elements
(via UI Automation on Windows) and pasting rich text. For now, we log when
the paste target appears to be a rich editor (e.g. Word, LibreOffice).
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

# PLAT-CONTENT: process names that are known rich-text editors.
# Pasting plain text into these is a known limitation.
_RICH_EDITOR_PROCESS_NAMES: set[str] = {
    "winword.exe",
    "wordpad.exe",
    "soffice.bin",
    "soffice.exe",
    "notion.exe",
    "obsidian.exe",
}


# ─── PLAT-027: Win32Clipboard abstraction ─────────────────────────────


class Win32Clipboard:
    """PLAT-027: Abstraction over Win32 clipboard API.

    Wraps OpenClipboard, EmptyClipboard, CloseClipboard, and
    GetClipboardSequenceNumber so callers don't use ctypes.windll.user32
    directly for clipboard operations.  Used as a context manager to
    guarantee CloseClipboard is always called.
    """

    def __init__(self, owner: int = 0):
        """Initialize with an optional owner window handle.

        Parameters
        ----------
        owner : int
            Window handle to pass to OpenClipboard. 0 = current task.
        """
        if sys.platform != "win32":
            raise RuntimeError("Win32Clipboard is only available on Windows")
        self._owner = owner
        self._opened = False

    def __enter__(self):
        """Open the clipboard. Returns self."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            if user32.OpenClipboard(self._owner):
                self._opened = True
            else:
                log.warning("[CLIPBOARD] OpenClipboard failed (err=%d)",
                            ctypes.windll.kernel32.GetLastError())
        except Exception as exc:
            log.warning("[CLIPBOARD] OpenClipboard raised: %s", exc)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the clipboard if it was opened."""
        if self._opened:
            try:
                import ctypes
                ctypes.windll.user32.CloseClipboard()
            except Exception:
                pass
            self._opened = False
        return False  # don't suppress exceptions

    def empty(self) -> bool:
        """Empty the clipboard. Must be called inside the context."""
        if not self._opened:
            return False
        try:
            import ctypes
            return bool(ctypes.windll.user32.EmptyClipboard())
        except Exception:
            return False

    @staticmethod
    def get_sequence_number() -> int:
        """PLAT-CLIPRACE: Get the clipboard sequence number.

        Returns 0 on non-Windows or on failure.
        """
        if sys.platform != "win32":
            return 0
        try:
            import ctypes
            user32 = ctypes.windll.user32
            if hasattr(user32, 'GetClipboardSequenceNumber'):
                return user32.GetClipboardSequenceNumber()
        except Exception:
            pass
        return 0


def _win32_empty_clipboard() -> None:
    """PLAT-006: Empty the clipboard via the Win32Clipboard abstraction.

    Called before pyperclip.copy() on Windows to clear stale clipboard
    formats (e.g. rich text artifacts from a previous copy).
    """
    if sys.platform != "win32":
        return
    try:
        with Win32Clipboard() as clip:
            clip.empty()
    except Exception:
        pass


# ─── PLAT-013: Elevated target detection ──────────────────────────────


def _is_elevated_target() -> bool:
    """PLAT-013: Check if the foreground window belongs to an elevated process.

    Uses GetWindowThreadProcessId + OpenProcess + GetTokenInformation to
    determine if the target process is running elevated.  If we are not
    elevated but the target is, UIPI will block our SendInput calls.

    Returns True if the foreground window is elevated and we are not.
    Returns False if we can't determine (fail open) or if elevation
    matches.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h_process:
            return False

        try:
            # Check if the target process is elevated
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(h_process, 0x0008, ctypes.byref(token)):
                return False
            try:
                # TokenElevation = 20
                ret_len = wintypes.DWORD()
                advapi32.GetTokenInformation(token, 20, None, 0, ctypes.byref(ret_len))
                buf = ctypes.create_string_buffer(ret_len.value or 4)
                if not advapi32.GetTokenInformation(
                    token, 20, buf, ctypes.sizeof(buf), ctypes.byref(ret_len)
                ):
                    return False
                # TOKEN_ELEVATION struct: DWORD TokenIsElevated
                target_elevated = bool(ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0])
            finally:
                kernel32.CloseHandle(token)

            # Now check if WE are elevated
            our_token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(our_token)):
                return False
            try:
                advapi32.GetTokenInformation(our_token, 20, None, 0, ctypes.byref(ret_len))
                our_buf = ctypes.create_string_buffer(ret_len.value or 4)
                if not advapi32.GetTokenInformation(
                    our_token, 20, our_buf, ctypes.sizeof(our_buf), ctypes.byref(ret_len)
                ):
                    return False
                we_elevated = bool(ctypes.cast(our_buf, ctypes.POINTER(wintypes.DWORD))[0])
            finally:
                kernel32.CloseHandle(our_token)

            # If target is elevated and we're not, warn
            if target_elevated and not we_elevated:
                log.warning(
                    "[CLIPBOARD] Target window (pid=%d) is elevated but we are not — "
                    "paste may fail due to UIPI", pid.value,
                )
                return True
            return False
        finally:
            kernel32.CloseHandle(h_process)
    except Exception:
        return False


# ─── PLAT-014: Password field detection ───────────────────────────────


def _is_password_field() -> bool:
    """PLAT-014: Check if the focused element is a password field.

    On Windows, uses UI Automation to check IsPasswordPropertyId.
    If the focused element has IsPassword=True, skip paste and warn
    the user that dictation into password fields is disabled for security.

    Returns True if a password field is detected, False otherwise.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        # Try using comtypes for UI Automation
        try:
            import comtypes.client
            UIA = comtypes.client.GetModule("UIAutomationCore.dll")
            uia = comtypes.CoCreateInstance(
                UIA.CUIAutomation._reg_clsid_,
                interface=UIA.IUIAutomation,
            )
            focused = uia.GetFocusedElement()
            if focused is not None:
                # UIA_IsPasswordPropertyId = 30022
                is_password = focused.GetCurrentPropertyValue(30022)
                if is_password:
                    log.warning(
                        "[CLIPBOARD] Password field detected — "
                        "dictation into password fields is disabled for security"
                    )
                    return True
        except Exception:
            pass

        # Fallback: try raw ctypes approach with UIAutomationCore.dll
        try:
            uia_core = ctypes.windll.UIAutomationCore
            # This is complex — if comtypes failed, just skip
            pass
        except Exception:
            pass

        return False
    except Exception:
        return False


class ClipboardManager:
    """Handles copying text to clipboard and pasting into the focused app."""

    # NEW-CQ-025: rate limit for paste operations. The PROBLEMS
    # invariant says "500ms"; the code previously said 1.0s. We
    # align to 500ms (0.5s) which is the documented invariant —
    # fast enough for rapid dictation but slow enough to prevent
    # accidental double-paste from a stuck hotkey.
    _PASTE_RATE_LIMIT = 0.5

    def __init__(self, paste_enabled: bool = True):
        self.paste_enabled = paste_enabled
        # Lazy-import pynput so the module can load headless. The actual
        # Controller() instantiation still requires a display (will raise
        # at instance construction time, NOT at module import time).
        _ensure_pynput_imported()
        self._keyboard = _Controller() if _Controller is not None else None
        self._last_paste_time: float = 0.0
        # PLAT-CLIPRACE: clipboard sequence number for race detection on Windows
        self._clipboard_seq: int = 0
        # PLAT-SECURE: last copied text for clipboard-clear comparison
        self._last_copied_text: str = ""
        self._clear_thread = None
        # PLAT-SECURE: saved clipboard content for restore after paste
        self._saved_clipboard: str | None = None

    @staticmethod
    def _get_clipboard_sequence_number() -> int:
        """Get the Windows clipboard sequence number.

        PLAT-CLIPRACE: used to detect if another app modified the
        clipboard between our copy and paste.
        PLAT-027: delegates to Win32Clipboard.get_sequence_number().
        """
        return Win32Clipboard.get_sequence_number()

    @staticmethod
    def _is_safe_paste_target() -> bool:
        """Check that the foreground window is safe for pasting.

        Blocks paste into UAC dialogs, credential prompts, and
        Winlogon windows to prevent credential theft.
        """
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return True

            # Check window class name for security-sensitive windows
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            class_name = buf.value

            # Block UAC/consent dialogs and credential prompts
            blocked_classes = {"#32770", "Credential Dialog Xaml Host", "CredDialog"}
            if class_name in blocked_classes:
                log.warning("[CLIPBOARD] Blocked paste into security-sensitive window (class=%s)", class_name)
                return False

            # PLAT-013: warn if the target is elevated and we are not
            _is_elevated_target()

            # PLAT-014: check if the focused element is a password field
            if _is_password_field():
                return False

            return True
        except Exception:
            return True  # Fail open — don't block paste on error

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
            # PLAT-006: On Windows, empty the clipboard before copying
            # to clear rich text artifacts from the previous clipboard content.
            # PLAT-027: uses Win32Clipboard abstraction instead of direct
            # ctypes.windll.user32 calls.
            _win32_empty_clipboard()

            # PLAT-SECURE / SEC-012: save existing clipboard content before
            # overwriting, if clipboard_save_restore is enabled (default True).
            self._saved_clipboard = None
            try:
                self._saved_clipboard = pyperclip.paste()
            except Exception:
                pass
            log.debug("[CLIPBOARD-AUDIT] Saved previous clipboard content for restore (len=%d)",
                      len(self._saved_clipboard) if self._saved_clipboard else 0)

            # PLAT-007: Retry clipboard access on ERROR_ACCESS_DENIED
            for attempt in range(3):
                try:
                    pyperclip.copy(text)
                    break
                except Exception as copy_err:
                    if attempt < 2:
                        time.sleep(0.05 * (attempt + 1))
                    else:
                        raise copy_err

            # PLAT-PASTEVR: Verify the clipboard content matches what we copied.
            # If another app modified the clipboard between our copy and
            # verification, retry up to 3 times.
            for verify_attempt in range(3):
                try:
                    actual = pyperclip.paste()
                    if actual == text:
                        break
                    log.warning(
                        "[CLIPBOARD] Clipboard verification failed (attempt %d/3) — "
                        "expected %d chars, got %d.",
                        verify_attempt + 1, len(text), len(actual) if actual else 0,
                    )
                    pyperclip.copy(text)
                except Exception:
                    pass  # pyperclip.paste() may not be supported on all platforms
            else:
                log.error("[CLIPBOARD] Clipboard verification still failed after 3 retries")

            # PLAT-SECURE: store the text for later comparison when clearing
            self._last_copied_text = text
            # PLAT-CLIPRACE: capture the clipboard sequence number after copy
            self._clipboard_seq = self._get_clipboard_sequence_number()
            log.info("[CLIPBOARD-AUDIT] Copied %d chars to clipboard (seq=%d)", len(text), self._clipboard_seq)
            return True
        except Exception as e:
            log.error("[CLIPBOARD] Failed to copy to clipboard: %s", e)
            return False

    def _release_stuck_modifiers(self) -> None:
        """Release any stuck modifier keys before paste.

        PLAT-STUCK: if a previous paste was interrupted (e.g. exception
        during _send_keystroke_sequence), Ctrl/Shift/Alt/Cmd may be
        left in a pressed state. Releasing them before the next paste
        prevents stuck-modifier behavior.
        """
        if _Key is None:
            return
        try:
            for key in (_Key.ctrl, _Key.shift, _Key.alt, _Key.cmd):
                try:
                    self._keyboard.release(key)
                except Exception:
                    pass
        except Exception:
            pass

    def _safe_key_press(self, modifier, char) -> None:
        """PLAT-STUCK: Press modifier + char with guaranteed modifier release.

        Wraps the modifier press/release in try/finally to ensure the
        modifier key is always released even if the character press or
        release raises an exception.
        """
        try:
            self._keyboard.press(modifier)
            self._keyboard.press(char)
            self._keyboard.release(char)
        finally:
            self._keyboard.release(modifier)

    def schedule_clipboard_clear(self, delay: float = 0) -> None:
        """Schedule clearing the clipboard after a delay.

        PLAT-SECURE / SEC-012: Sensitive transcript text (passwords, PII)
        should not remain on the clipboard indefinitely. After pasting, we
        schedule a clear to remove the text and optionally restore the
        previous clipboard content.

        SEC-012: The default delay is read from config
        ``clipboard_clear_delay_seconds`` (default 5 seconds). Pass 0 to
        use the config default; pass a positive value to override.
        ``clipboard_save_restore`` controls whether the previous clipboard
        content is restored after clearing.
        """
        import threading

        # SEC-012: Use config default if no explicit delay given
        if delay <= 0:
            try:
                from voice_typer.server.config import Config
                delay = float(Config.load().clipboard_clear_delay_seconds)
            except Exception:
                delay = 5.0

        # SEC-012: Check whether save/restore is enabled
        save_restore = True  # default
        try:
            from voice_typer.server.config import Config
            save_restore = Config.load().clipboard_save_restore
        except Exception:
            pass

        saved_text = self._last_copied_text
        saved_clipboard = self._saved_clipboard if save_restore else None

        def _clear():
            try:
                time.sleep(delay)
                # Only clear if clipboard still contains our text
                # (don't clobber if user copied something else)
                try:
                    current = pyperclip.paste()
                except Exception:
                    current = None
                if current == saved_text:
                    # PLAT-SECURE / SEC-012: restore previous clipboard
                    # content if we saved it and save_restore is enabled,
                    # otherwise clear to empty
                    if saved_clipboard is not None:
                        try:
                            pyperclip.copy(saved_clipboard)
                            log.info("[CLIPBOARD-AUDIT] Clipboard restored to previous content after %ds delay", int(delay))
                        except Exception:
                            pyperclip.copy("")
                            log.info("[CLIPBOARD-AUDIT] Clipboard cleared (sensitive data removed, restore failed)")
                    else:
                        pyperclip.copy("")
                        log.info("[CLIPBOARD-AUDIT] Clipboard cleared (sensitive data removed) after %ds delay", int(delay))
                else:
                    log.debug("[CLIPBOARD-AUDIT] Clipboard not cleared — content changed since copy")
            except Exception:
                pass
        self._clear_thread = threading.Thread(target=_clear, daemon=True, name="clipboard-clear")
        self._clear_thread.start()

    def paste(self) -> bool:
        """Send a paste keystroke into the focused window.

        Returns True if a keystroke was sent, False if paste is disabled
        or rate-limited.
        """
        # PLAT-STUCK: release any stuck modifier keys before pasting
        self._release_stuck_modifiers()

        # PLAT-CLIPRACE: verify clipboard wasn't modified between copy and paste
        if sys.platform == "win32" and hasattr(self, '_clipboard_seq') and self._clipboard_seq:
            current_seq = self._get_clipboard_sequence_number()
            if current_seq != self._clipboard_seq:
                log.warning("[CLIPBOARD] Clipboard modified between copy and paste (seq %d -> %d)", self._clipboard_seq, current_seq)

        # PLAT-RDP: increase paste delay in RDP sessions where clipboard
        # sync is slower
        paste_delay = 0.02
        if sys.platform == "win32":
            try:
                from voice_typer.server.platform import is_remote_session
                if is_remote_session():
                    paste_delay = 0.10
                    log.info("[CLIPBOARD] RDP session detected — increasing paste delay to %dms", int(paste_delay * 1000))
            except Exception:
                pass

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

        if not self._is_safe_paste_target():
            log.info("[CLIPBOARD] Paste blocked — security-sensitive window in foreground")
            return False

        try:
            time.sleep(paste_delay)

            process_name = self._detect_focused_process()
            is_terminal = self._is_terminal_process(process_name)

            # PLAT-CONTENT: log when the paste target appears to be a rich editor
            if process_name and process_name.lower() in _RICH_EDITOR_PROCESS_NAMES:
                log.info(
                    "[CLIPBOARD] Paste target appears to be a rich editor (%s) — "
                    "pasting plain text (contentEditable detection not implemented)",
                    process_name,
                )

            if is_terminal:
                if sys.platform == "darwin":
                    self._safe_key_press(_Key.cmd, "v")
                else:
                    self._safe_key_press(_Key.shift, _Key.insert)
            elif sys.platform == "darwin":
                self._safe_key_press(_Key.cmd, "v")
            elif sys.platform == "win32":
                self._send_ctrl_v_win32()
            else:
                self._safe_key_press(_Key.ctrl, "v")

            self._last_paste_time = time.monotonic()
            log.info("[CLIPBOARD-AUDIT] Sent paste keystroke (terminal=%s, target=%s)", is_terminal, process_name or "unknown")
            return True
        except Exception as e:
            log.warning("[CLIPBOARD] Auto-paste failed (clipboard still has the text): %s", e)
            return False

    def _send_keystroke_sequence(self, modifier, char) -> None:
        # PLAT-STUCK: ensure modifier is always released even on exception.
        # Uses a robust try/finally pattern that presses modifier + char,
        # releases in reverse order, and guarantees ALL keys are released
        # in the finally block even if an intermediate release raises.
        try:
            self._keyboard.press(modifier)
            self._keyboard.press(char)
            self._keyboard.release(char)
            self._keyboard.release(modifier)
        finally:
            # Double-release: guarantee modifier is freed even if the
            # normal release path above was skipped by an exception.
            for key in (modifier, char):
                try:
                    self._keyboard.release(key)
                except Exception:
                    pass

    def _send_ctrl_v_win32(self) -> None:
        """Send Ctrl+V via a single atomic SendInput batch.

        PLAT-001: On Windows, we always prefer SendInput over
        pynput.keyboard.Controller because pynput's Controller is
        blocked by UIPI when targeting elevated processes from a
        non-elevated one.  Our direct SendInput call is subject to the
        same UIPI restriction, but we log the failure explicitly
        instead of silently dropping it.
        """
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
                "[CLIPBOARD] SendInput returned %d (expected 4) — "
                "this may be caused by UIPI blocking if the target is elevated. "
                "Falling back to pynput Controller.",
                result,
            )
            # PLAT-001: fallback to pynput Controller as last resort.
            # Note: pynput.keyboard.Controller is also subject to UIPI,
            # so this may also fail silently.
            self._safe_key_press(_Key.ctrl, "v")
