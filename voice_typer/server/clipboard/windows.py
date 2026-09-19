"""Win32 clipboard primitives ( split).

Extracted from the original ``clipboard.py`` monolith. Contains:

* :class:`Win32Clipboard`: context-manager abstraction over Win32
  OpenClipboard / EmptyClipboard / CloseClipboard /
GetClipboardSequenceNumber ().
* :func:`_win32_empty_clipboard`: convenience wrapper that clears the
clipboard via the :class:`Win32Clipboard` context manager ().
* :func:`_send_ctrl_v_win32`: standalone SendInput helper that
injects an atomic Ctrl+V keystroke batch ().

Platform guard: the Win32 code paths use ``ctypes.windll`` which only
exists on Windows. On non-Windows hosts, every function degrades to a
no-op or returns a sentinel (0 / False / raises ``RuntimeError`` in
the case of ``Win32Clipboard.__init__``).

Design contract: all patchable symbols (``is_windows``, ``log``) are
looked up via the PACKAGE (``_cb.X``) at call time so test patches
like ``patch.object(clip_mod, "is_windows", return_value=True)``
actually take effect on the code paths in this module.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes

from voice_typer.server import clipboard as _cb

# the per-submodule `log = logging.getLogger(...)` definition that


class Win32Clipboard:
    """Abstraction over Win32 clipboard API.

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
        if not _cb.is_windows():
            raise RuntimeError("Win32Clipboard is only available on Windows")
        self._owner = owner
        self._opened = False

    def __enter__(self):
        """Open the clipboard. Returns self."""
        try:
            user32 = ctypes.windll.user32
            if user32.OpenClipboard(self._owner):
                self._opened = True
            else:
                _cb.log.warning(
                    "[CLIPBOARD] OpenClipboard failed (err=%d)",
                    ctypes.windll.kernel32.GetLastError(),
                )
        except Exception as exc:
            _cb.log.warning("[CLIPBOARD] OpenClipboard raised: %s", exc)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the clipboard if it was opened."""
        if self._opened:
            try:
                ctypes.windll.user32.CloseClipboard()
            except (OSError, AttributeError):
                # narrowed from bare ``except Exception: pass``.
                _cb.log.debug("clipboard cleanup failed", exc_info=True)
            self._opened = False
        return False  # don't suppress exceptions

    def empty(self) -> bool:
        """Empty the clipboard. Must be called inside the context."""
        if not self._opened:
            return False
        try:
            return bool(ctypes.windll.user32.EmptyClipboard())
        except (OSError, AttributeError):
            # narrowed from bare ``except Exception: return False``.
            _cb.log.debug("[CLIPBOARD] EmptyClipboard failed", exc_info=True)
            return False

    @staticmethod
    def get_sequence_number() -> int:
        """PLAT-CLIPRACE: Get the clipboard sequence number.

        Returns 0 on non-Windows or on failure.
        """
        if not _cb.is_windows():
            return 0
        try:
            user32 = ctypes.windll.user32
            if hasattr(user32, "GetClipboardSequenceNumber"):
                return user32.GetClipboardSequenceNumber()
        except (OSError, AttributeError):
            # narrowed from bare ``except Exception: pass``.
            _cb.log.debug("clipboard sequence-number query failed", exc_info=True)
        return 0


def _win32_empty_clipboard() -> None:
    """Empty the clipboard via the Win32Clipboard abstraction.

    Called before pyperclip.copy() on Windows to clear stale clipboard
    formats (e.g. rich text artifacts from a previous copy).
    """
    if not _cb.is_windows():
        return
    try:
        # Look up Win32Clipboard via the package so test patches like
        with _cb.Win32Clipboard() as clip:
            clip.empty()
    except (OSError, AttributeError):
        # narrowed from bare ``except Exception: pass``. The
        _cb.log.debug("clipboard cleanup failed", exc_info=True)


# ``ExcludeClipboardContentFromMonitorProcessing`` is a registered

# Win32 constants used by the exclusion helper below. Kept private to
_GMEM_MOVEABLE = 0x0002


def _win32_exclude_clipboard_from_monitoring() -> bool:
    """Tag the clipboard with the monitor-exclusion format.

    (High, Privacy): after :meth:`ClipboardManager.copy` puts
    dictated text on the clipboard, this helper opens the clipboard
    and sets a 1-byte payload for the registered format
    ``ExcludeClipboardContentFromMonitorProcessing``. Windows' built-in
    clipboard history service (Win+V) and conforming third-party
    monitors skip the current clipboard content when this format is
    present, so dictated text (which can be passwords, financial data,
    medical notes, anything the user dictated) does not linger in the
    OS clipboard history after the paste completes.

    The helper is best-effort: it logs at DEBUG on failure and returns
    ``False``. The clipboard content itself is already set by the
    caller, the exclusion tag is a privacy enhancement, not a
    correctness gate. A failure to set the tag leaves the dictated
    text in the clipboard history (the pre-fix behavior), which is
    the safe degraded mode rather than a paste failure.

    Returns ``True`` if the exclusion format was set successfully,
    ``False`` on any failure (clipboard locked, format registration
    failed, ``SetClipboardData`` returned NULL, etc.).
    """
    if not _cb.is_windows():
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Register the named format. ``RegisterClipboardFormatW``
        fmt = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
        if not fmt:
            _cb.log.debug(
                "[CLIPBOARD] RegisterClipboardFormatW failed (err=%d), clipboard monitor exclusion disabled",
                kernel32.GetLastError(),
            )
            return False

        # Allocate a 1-byte global memory block. The payload content is
        h_global = kernel32.GlobalAlloc(_GMEM_MOVEABLE, 1)
        if not h_global:
            _cb.log.debug(
                "[CLIPBOARD] GlobalAlloc(1) failed for monitor-exclusion tag (err=%d)",
                kernel32.GetLastError(),
            )
            return False

        # ``SetClipboardData`` takes ownership of the HGLOBAL, do NOT
        with _cb.Win32Clipboard() as clip:
            if not clip._opened:
                _cb.log.debug("[CLIPBOARD] OpenClipboard failed, cannot set monitor-exclusion tag")
                # We still own the HGLOBAL; free it.
                kernel32.GlobalFree(h_global)
                return False
            result = user32.SetClipboardData(fmt, h_global)
            if not result:
                _cb.log.debug(
                    "[CLIPBOARD] SetClipboardData(monitor-exclusion) failed (err=%d), "
                    "dictated text will appear in clipboard history",
                    kernel32.GetLastError(),
                )
                # SetClipboardData failed → we still own the HGLOBAL;
                kernel32.GlobalFree(h_global)
                return False
        return True
    except (OSError, AttributeError):
        # narrowed from bare ``except Exception: pass``. The protected
        _cb.log.debug(
            "[CLIPBOARD] _win32_exclude_clipboard_from_monitoring failed",
            exc_info=True,
        )
        return False


# Win32 INPUT / KEYBDINPUT structures defined inline via

# ULONG_PTR is pointer-sized: 4 bytes on 32-bit Windows, 8 bytes on
_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class KEYBDINPUT(ctypes.Structure):
    """Win32 KEYBDINPUT: payload for a keyboard INPUT event."""

    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )
    # KEYBDINPUT.KEYUP constant from pynput._util.win32
    KEYUP = 0x0002


class InputUnion(ctypes.Union):
    """Win32 INPUT union, only the keyboard branch is populated."""

    _fields_ = (("ki", KEYBDINPUT),)


class INPUT(ctypes.Structure):
    """Win32 INPUT structure (keyboard variant)."""

    _fields_ = (
        ("type", wintypes.DWORD),
        # Named ``ki`` to match the production code's
        ("ki", InputUnion),
    )
    # INPUT.KEYBOARD constant from pynput._util.win32
    KEYBOARD = 1


def _resolve_send_input():
    """Look up ``user32.SendInput`` via ``ctypes.windll`` at call time.

    replaces the ``pynput._util.win32.SendInput`` import
        (a private API). Looking the function up at call time means test
        patches like ``patch("ctypes.windll", create=True)`` or
        ``patch.object(ctypes.windll.user32, "SendInput", ...)`` take
        effect. Configuring ``argtypes`` / ``restype`` here (rather than at
        module import time) keeps the module importable on non-Windows
        hosts where ``ctypes.windll`` doesn't exist.
    """
    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    send_input.restype = wintypes.UINT
    return send_input


def _send_ctrl_v_win32(
    fallback: Callable[[], None] | None = None,
) -> bool:
    """Send Ctrl+V via a single atomic SendInput batch.

    On Windows, we always prefer SendInput over
        pynput.keyboard.Controller because pynput's Controller is
        blocked by UIPI when targeting elevated processes from a
        non-elevated one.  Our direct SendInput call is subject to the
        same UIPI restriction, but we log the failure explicitly
        instead of silently dropping it.

    the Win32 ``INPUT`` / ``KEYBDINPUT`` structures
        (and the ``SendInput`` function pointer) are now resolved inline
        via ``ctypes`` + ``ctypes.windll.user32`` (previously imported
        from ``pynput._util.win32``, a private submodule). The previous
        implementation imported them from
        ``pynput._util.win32`` (a private submodule) which made the
        paste path fragile against pynput internal refactors.

        Returns ``True`` only when the full Ctrl+V sequence was
        delivered (SendInput returned 4). Returns ``False`` on partial
        success (SendInput returned 1..3) AND on total failure
        (SendInput returned 0), so the caller can surface a warning
        without risking a double-paste. Total failure still invokes
        ``fallback`` first (best effort, may help non-UIPI transient
        failures), but the delivery is unverifiable: the fallback uses
        the same SendInput API, so under UIPI (the dominant production
        cause) it is equally blocked yet reports nothing. The old
        contract returned True here ("best-effort success"), which made
        paste() log "Sent paste keystroke" while nothing was delivered;
        the restore daemon then wiped the clipboard and the dictation
        was silently lost.

        Parameters
        ----------
        fallback : callable, optional
            Invoked when SendInput returns 0 (complete failure, no
            events delivered, so no double-paste risk). Typically
            ``lambda: self._safe_key_press(_Key.ctrl, "v")`` for the
            pynput Controller fallback path.
    """
    # structs are defined inline at the top of this module
    send_input = _resolve_send_input()

    vk_control = 0x11
    vk_v = 0x56

    events = (INPUT * 4)(
        INPUT(
            INPUT.KEYBOARD,
            InputUnion(ki=KEYBDINPUT(wVk=vk_control, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        ),
        INPUT(
            INPUT.KEYBOARD,
            InputUnion(ki=KEYBDINPUT(wVk=vk_v, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        ),
        INPUT(
            INPUT.KEYBOARD,
            InputUnion(ki=KEYBDINPUT(wVk=vk_v, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
        ),
        INPUT(
            INPUT.KEYBOARD,
            InputUnion(ki=KEYBDINPUT(wVk=vk_control, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
        ),
    )

    result = send_input(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    if result != 4:
        # (revised): SendInput returns the number of events
        _cb.log.warning(
            "[CLIPBOARD] SendInput returned %d (expected 4), "
            "this may be caused by UIPI blocking if the target is elevated.",
            result,
        )
        if 1 <= result <= 3:
            # Partial success, synthesize KEYUP for any keys that may
            _cb.log.error(
                "[CLIPBOARD] SendInput partial success (%d/4 events), "
                "NOT falling back to pynput to avoid double-paste. "
                "Releasing any stuck modifiers.",
                result,
            )
            try:
                # Best-effort: send both KEYUP events; harmless if the
                release_events = (INPUT * 2)(
                    INPUT(
                        INPUT.KEYBOARD,
                        InputUnion(ki=KEYBDINPUT(wVk=vk_v, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
                    ),
                    INPUT(
                        INPUT.KEYBOARD,
                        InputUnion(
                            ki=KEYBDINPUT(wVk=vk_control, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)
                        ),
                    ),
                )
                send_input(2, ctypes.byref(release_events), ctypes.sizeof(INPUT))
            except Exception:
                _cb.log.debug("[CLIPBOARD] failed to synthesize KEYUP cleanup", exc_info=True)
            return False  # paste did not complete cleanly; do not proceed

        # result == 0: TOTAL failure, zero events delivered. Still
        _cb.log.warning(
            "[CLIPBOARD] SendInput returned 0 (no events delivered, UIPI may be blocking); "
            "attempting pynput fallback, delivery unverified",
        )
        if fallback is not None:
            fallback()
        return False

    # SendInput returned 4, full Ctrl+V sequence delivered.
    return True


# (Low): mirror of ``_send_ctrl_v_win32`` for the Windows terminal


def _send_shift_insert_win32(
    fallback: Callable[[], None] | None = None,
) -> bool:
    """Send Shift+Insert via a single atomic SendInput batch.

    Mirrors :func:`_send_ctrl_v_win32`. The keystroke sequence is
    Shift↓ → Insert↓ → Insert↑ → Shift↑ (4 events submitted as one
    ``SendInput`` call). Used for terminal-emulator paste targets on
    Windows when pynput (``self._keyboard``) is unavailable.

    Returns ``True`` only on full success (4 events). Returns ``False``
    on partial success (1..3 events) AND on total failure (0 events,
    fallback attempted but delivery unverified — same contract as
    :func:`_send_ctrl_v_win32`).

    Parameters
    ----------
    fallback : callable, optional
        Invoked when ``SendInput`` returns 0 (complete failure, no
        events delivered). Typically
        ``lambda: self._safe_key_press(_Key.shift, _Key.insert)``.
    """
    send_input = _resolve_send_input()

    # VK_SHIFT = 0xA0 (the left shift virtual key, matching pynput's
    vk_shift = 0xA0
    vk_insert = 0x2D

    events = (INPUT * 4)(
        INPUT(
            INPUT.KEYBOARD,
            InputUnion(ki=KEYBDINPUT(wVk=vk_shift, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        ),
        INPUT(
            INPUT.KEYBOARD,
            InputUnion(ki=KEYBDINPUT(wVk=vk_insert, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        ),
        INPUT(
            INPUT.KEYBOARD,
            InputUnion(ki=KEYBDINPUT(wVk=vk_insert, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
        ),
        INPUT(
            INPUT.KEYBOARD,
            InputUnion(ki=KEYBDINPUT(wVk=vk_shift, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
        ),
    )

    result = send_input(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    if result != 4:
        _cb.log.warning(
            "[CLIPBOARD] SendInput(Shift+Insert) returned %d (expected 4), "
            "this may be caused by UIPI blocking if the target is elevated.",
            result,
        )
        if 1 <= result <= 3:
            # Partial success, synthesize KEYUP for any keys that may
            _cb.log.error(
                "[CLIPBOARD] SendInput(Shift+Insert) partial success (%d/4 events), "
                "NOT falling back to pynput to avoid double-paste. "
                "Releasing any stuck modifiers.",
                result,
            )
            try:
                release_events = (INPUT * 2)(
                    INPUT(
                        INPUT.KEYBOARD,
                        InputUnion(
                            ki=KEYBDINPUT(
                                wVk=vk_insert,
                                wScan=0,
                                dwFlags=KEYBDINPUT.KEYUP,
                                time=0,
                                dwExtraInfo=0,
                            )
                        ),
                    ),
                    INPUT(
                        INPUT.KEYBOARD,
                        InputUnion(
                            ki=KEYBDINPUT(wVk=vk_shift, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)
                        ),
                    ),
                )
                send_input(2, ctypes.byref(release_events), ctypes.sizeof(INPUT))
            except Exception:
                _cb.log.debug("[CLIPBOARD] failed to synthesize Shift+Insert KEYUP cleanup", exc_info=True)
            return False

        # result == 0: TOTAL failure (same contract as the Ctrl+V
        _cb.log.warning(
            "[CLIPBOARD] SendInput(Shift+Insert) returned 0 (no events delivered, UIPI may be blocking); "
            "attempting pynput fallback, delivery unverified",
        )
        if fallback is not None:
            fallback()
        return False

    return True


__all__ = [
    "INPUT",
    "InputUnion",
    "KEYBDINPUT",
    "Win32Clipboard",
    "_send_ctrl_v_win32",
    "_send_shift_insert_win32",
    "_win32_empty_clipboard",
    "_win32_exclude_clipboard_from_monitoring",
]
