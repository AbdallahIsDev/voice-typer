"""Win32 clipboard primitives ( split).

Extracted from the original ``clipboard.py`` monolith. Contains:

* :class:`Win32Clipboard` — context-manager abstraction over Win32
  OpenClipboard / EmptyClipboard / CloseClipboard /
GetClipboardSequenceNumber ().
* :func:`_win32_empty_clipboard` — convenience wrapper that clears the
clipboard via the :class:`Win32Clipboard` context manager ().
* :func:`_send_ctrl_v_win32` — standalone SendInput helper that
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
# used to live here was removed because it was unused — every log call in
# this module routes through `_cb.log` (the package-level logger imported
# above as `_cb`). Defining a separate `log` here would shadow the
# package logger and risk future contributors adding `log.info(...)`
# calls that bypass the `_cb.log` patch surface used by tests
# (`tests/test_clipboard.py` patches `voice_typer.server.clipboard.log`).
# The `import logging` was also removed (no remaining references).


# Win32Clipboard abstraction ─────────────────────────────


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
                # CloseClipboard is a Win32 ctypes call; ``OSError`` covers
                # Win32 API failures and ``AttributeError`` covers a
                # missing ctypes function pointer on stripped builds.
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
            # EmptyClipboard is a Win32 ctypes call; ``OSError`` covers
            # Win32 API failures and ``AttributeError`` covers a missing
            # ctypes function pointer on stripped builds.
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
            # GetClipboardSequenceNumber is a Win32 ctypes call;
            # ``OSError`` covers Win32 API failures and
            # ``AttributeError`` covers a missing function pointer.
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
        # ``patch.object(clip_mod, "Win32Clipboard", side_effect=...)``
        # take effect.
        with _cb.Win32Clipboard() as clip:
            clip.empty()
    except (OSError, AttributeError):
        # narrowed from bare ``except Exception: pass``. The
        # protected block opens, empties, and closes the clipboard via
        # Win32 ctypes (OpenClipboard / EmptyClipboard / CloseClipboard);
        # ``OSError`` covers Win32 API failures and ``AttributeError``
        # covers a missing ctypes function pointer.
        _cb.log.debug("clipboard cleanup failed", exc_info=True)


# Win32 SendInput Ctrl+V helper ──────────────────────────

# Win32 INPUT / KEYBDINPUT structures defined inline via
# ``ctypes.Structure`` (previously imported from ``pynput._util.win32``,
# a private submodule that may break across pynput releases without
# notice). Only the keyboard branch is needed — mouse/hardware input
# structs from the Win32 INPUT union are omitted to keep the surface
# small. The definitions mirror the Win32 SDK:
#   https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input
#
# Public names (``INPUT``, ``KEYBDINPUT``, ``InputUnion``) match the
# previous pynput._util.win32 import surface so downstream code and
# tests with their own ctypes Structure stubs can swap them in
# unchanged.

# ULONG_PTR is pointer-sized: 4 bytes on 32-bit Windows, 8 bytes on
# 64-bit. ``wintypes`` does not export it directly; select the right
# width by ``sizeof(c_void_p)``.
_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class KEYBDINPUT(ctypes.Structure):
    """Win32 KEYBDINPUT — payload for a keyboard INPUT event."""

    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )
    # KEYBDINPUT.KEYUP constant from pynput._util.win32
    # (Win32 KEYEVENTF_KEYUP = 0x0002).
    KEYUP = 0x0002


class InputUnion(ctypes.Union):
    """Win32 INPUT union — only the keyboard branch is populated."""

    _fields_ = (("ki", KEYBDINPUT),)


class INPUT(ctypes.Structure):
    """Win32 INPUT structure (keyboard variant)."""

    _fields_ = (
        ("type", wintypes.DWORD),
        # Named ``ki`` to match the production code's
        # ``InputUnion(ki=KEYBDINPUT(...))`` construction. The C
        # struct has an anonymous union here; ctypes requires a
        # name to address the union member.
        ("ki", InputUnion),
    )
    # INPUT.KEYBOARD constant from pynput._util.win32
    # (Win32 INPUT_KEYBOARD = 1).
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

        Returns ``True`` if the full Ctrl+V sequence was delivered
        (SendInput returned 4) OR the ``fallback`` was invoked
        (best-effort — assumed success since pynput raises on failure).
        Returns ``False`` on partial success (SendInput returned 1..3)
        so the caller can surface a warning without risking a
        double-paste.

        Parameters
        ----------
        fallback : callable, optional
            Invoked when SendInput returns 0 (complete failure — no
            events delivered, so no double-paste risk). Typically
            ``lambda: self._safe_key_press(_Key.ctrl, "v")`` for the
            pynput Controller fallback path.
    """
    # structs are defined inline at the top of this module
    # (no longer imported from pynput._util.win32 — a private submodule).
    # ``SendInput`` is resolved via ``ctypes.windll.user32`` at call time
    # so test patches like ``patch("ctypes.windll", create=True)`` take
    # effect.
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
        # successfully inserted. Values 1..3 mean SOME but not all of
        # the Ctrl+V keystroke events were delivered — e.g. result=2
        # means Ctrl-down + V-down happened but V-up + Ctrl-up did not.
        #
        # The previous code fell back to pynput._safe_key_press() in
        # this case, which would deliver ANOTHER full Ctrl+V sequence
        # — causing a DOUBLE-PASTE if the partial SendInput already
        # pasted the clipboard content (e.g. result=2 with Ctrl-down +
        # V-down is enough to trigger paste in most apps).
        #
        # Fix: when result is in [1, 3], we DO NOT fall back to pynput.
        # Instead we log the partial failure and synthesize the missing
        # KEYUP events explicitly to release any stuck modifiers, then
        # return without paste. The caller can retry the full paste
        # sequence on the next hotkey press.
        #
        # When result == 0 (complete failure), no events were delivered,
        # so falling back to the pynput path (via ``fallback``) is safe
        # (no double-paste risk).
        _cb.log.warning(
            "[CLIPBOARD] SendInput returned %d (expected 4) — "
            "this may be caused by UIPI blocking if the target is elevated.",
            result,
        )
        if 1 <= result <= 3:
            # Partial success — synthesize KEYUP for any keys that may
            # be stuck down (Ctrl and/or V) to avoid leaving the
            # keyboard in a wedged state.
            _cb.log.error(
                "[CLIPBOARD] SendInput partial success (%d/4 events) — "
                "NOT falling back to pynput to avoid double-paste. "
                "Releasing any stuck modifiers.",
                result,
            )
            try:
                # Best-effort: send both KEYUP events; harmless if the
                # key wasn't actually down.
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

        # result == 0: complete failure — safe to fall back to pynput
        # (no events were delivered, so no double-paste risk).
        _cb.log.info("[CLIPBOARD] SendInput returned 0 — falling back to pynput Controller")
        # fallback to pynput Controller as last resort.
        # Note: pynput.keyboard.Controller is also subject to UIPI,
        # so this may also fail silently.
        if fallback is not None:
            fallback()
        return True  # pynput fallback invoked — best-effort success

    # SendInput returned 4 — full Ctrl+V sequence delivered.
    return True


__all__ = [
    "INPUT",
    "InputUnion",
    "KEYBDINPUT",
    "Win32Clipboard",
    "_send_ctrl_v_win32",
    "_win32_empty_clipboard",
]
