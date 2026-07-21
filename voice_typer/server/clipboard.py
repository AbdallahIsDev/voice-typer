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

import atexit
import contextlib
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any

import pyperclip

from voice_typer.server.clipboard_snapshot import ClipboardSnapshot
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger(__name__)


# ─── CLIP-8: module-level registry of pending delayed-restores ────────
#
# When paste() schedules a daemon-thread restore, it also appends an
# entry to this list. The daemon thread removes its entry on normal
# completion. If the app exits while a delayed restore is still
# pending (e.g. user quits during the 150ms restore-delay window),
# the atexit handler walks this list and force-restores each snapshot
# synchronously — preventing the user's original clipboard content
# from being lost forever.
#
# Each entry is a tuple of (ClipboardManager, ClipboardSnapshot,
# pasted_text, delay). The lock guards the list because the daemon
# thread and atexit handler may run concurrently.
_pending_restores: list[tuple[Any, Any, str, float]] = []
_pending_restores_lock = threading.Lock()


def _force_restore_pending_at_exit() -> None:
    """CLIP-8: atexit handler — force-restore any pending snapshots.

    Walks the module-level ``_pending_restores`` list and synchronously
    restores each snapshot. This prevents data loss when the app exits
    while a delayed restore is still pending (e.g. user quits during
    the 150ms restore delay window).

    The handler is best-effort: per-snapshot failures are logged but
    do not abort the loop (we restore as many as we can).
    """
    with _pending_restores_lock:
        items = list(_pending_restores)
        _pending_restores.clear()
    for _cm, snapshot, pasted_text, delay in items:
        try:
            # Try to read the clipboard to decide whether to restore.
            # If we can't read it, restore anyway (data-loss prevention
            # beats false-positive restore — see CLIP-9).
            try:
                current = _paste_from_clipboard()
            except Exception:
                current = None
            if current is None or current == pasted_text:
                snapshot.restore()
                log.info(
                    "[CLIPBOARD-AUDIT] Atexit: restored snapshot (delay was %.3fs)",
                    delay,
                )
            else:
                log.debug(
                    "[CLIPBOARD-AUDIT] Atexit: skip restore (clipboard changed, current=%d chars, expected=%d chars)",
                    len(current) if current else 0,
                    len(pasted_text),
                )
        except Exception:
            log.exception("[CLIPBOARD] Atexit restore failed")


# Register the atexit handler once at module import. Idempotent guard
# prevents double-registration if the module is re-imported.
_ATEXIT_REGISTERED = False
if not _ATEXIT_REGISTERED:
    try:
        atexit.register(_force_restore_pending_at_exit)
        _ATEXIT_REGISTERED = True
    except Exception:  # pragma: no cover — atexit.register only fails if interpreter is shutting down
        pass


class ClipboardCopyError(RuntimeError):
    """Raised when ``ClipboardManager.copy()`` fails to write text to the
    clipboard after retries.

    ADR-0010 §5.2: distinguishes "copy failed" (caller should write to
    crash recovery) from "save/restore disabled" (caller skips the
    clipboard). The snapshot, if captured, is restored before raising
    so the clipboard is never left torn.
    """


# Lazy-import pynput at instance creation time, not module import time.
# pynput.keyboard imports a platform backend (X11 on Linux, IOKit on mac,
# Win32 on Windows) that requires a running display / window manager.
# Importing at module level breaks `python -m voice_typer --version`
# in headless containers / SSH sessions without DISPLAY.
# TASK-10: _Key / _Controller are lazily populated by
# _ensure_pynput_imported() on first use. They are typed as ``Any`` so
# pyrefly can follow the .cmd / .ctrl / .press() / .release() accesses
# without flagging every call site (the actual pynput import is
# deferred to runtime so headless installs don't break at import time).
_Key: Any = None  # type: ignore[assignment]
_Controller: Any = None  # type: ignore[assignment]


def _ensure_pynput_imported():
    """Lazily import pynput.keyboard.Key and Controller on first use."""
    global _Key, _Controller
    if _Key is not None and _Controller is not None:
        return
    from pynput.keyboard import Controller as _c  # noqa: N813
    from pynput.keyboard import Key as _k  # noqa: N813

    _Key = _k
    _Controller = _c


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


# ─── ADR-0020 §6.6: Wayland clipboard fallback (wl-copy / wl-paste) ────
#
# On Wayland, `pyperclip.copy()` does NOT work reliably — pyperclip
# auto-detects xclip / xsel which are X11-only and silently no-op under
# native Wayland apps. ADR-0020 §6.6 mandates the clipboard + Ctrl+V
# fallback path via `wl-copy` / `wl-paste` (provided by the `wl-clipboard`
# package) when `WAYLAND_DISPLAY` is set and we're on Linux.
#
# These helpers are best-effort: if `wl-clipboard` is not installed, the
# caller falls back to `pyperclip` (which still works under XWayland
# sessions where both X11 and Wayland clients are talking to the same
# compositor). The runbook (linux-validation-runbook.md §5/§6) lists
# `wl-clipboard` as a required system dep on both X11 and Wayland hosts
# because the same binary runs on both session types.


def _is_wayland_session() -> bool:
    """Return True if running on a Linux Wayland session.

    Detection: `WAYLAND_DISPLAY` is set AND we're on Linux. This is the
    same heuristic `tauri-plugin-clipboard-manager` uses per ADR-0020 §6.6.

    Note: a Wayland session typically also has `DISPLAY` set (for
    XWayland), so checking only `DISPLAY` is insufficient. We check
    `WAYLAND_DISPLAY` first.
    """
    if not is_linux():
        return False
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _have_wl_clipboard() -> bool:
    """Return True if both `wl-copy` and `wl-paste` are on PATH."""
    return bool(shutil.which("wl-copy") and shutil.which("wl-paste"))


def _linux_wayland_copy(text: str) -> None:
    """Copy text to the Wayland clipboard via `wl-copy`.

    Raises ``RuntimeError`` if `wl-copy` is missing or exits non-zero.
    The text is piped to wl-copy's stdin so it works for arbitrary
    Unicode (no shell escaping concerns).

    XPLAT-7: ``timeout=5`` bounds the call so a hung Wayland compositor
    (or a wedged wl-copy fork) can't block the transcription thread
    indefinitely. ``subprocess.TimeoutExpired`` is converted to a
    ``RuntimeError`` so the caller's ``except Exception`` fallback to
    pyperclip kicks in.
    """
    if not text:
        # `wl-copy` with no args clears the clipboard; that matches our
        # "empty text → no-op" semantics in ClipboardManager.copy().
        return
    try:
        proc = subprocess.run(
            ["wl-copy", "--", text],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"wl-copy timed out after 5s: {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RuntimeError(f"wl-copy exited with {proc.returncode}: {stderr.strip()}")


def _linux_wayland_paste() -> str:
    """Read text from the Wayland clipboard via `wl-paste`.

    Returns the clipboard text (may be empty). Raises ``RuntimeError``
    if `wl-paste` is missing or exits non-zero.

    XPLAT-7: ``timeout=5`` bounds the call (see :func:`_linux_wayland_copy`).
    """
    try:
        proc = subprocess.run(
            ["wl-paste", "--no-newline"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"wl-paste timed out after 5s: {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RuntimeError(f"wl-paste exited with {proc.returncode}: {stderr.strip()}")
    return proc.stdout.decode("utf-8", errors="replace")


# ─── XPLAT-15: Wayland paste fallback (wtype) ──────────────────────────
#
# pynput.keyboard.Controller is X11-only — on a native Wayland session it
# either silently no-ops or raises (depending on whether XWayland is
# reachable). ADR-0020 §6.6 / XPLAT-15 mandate a `wtype` shell-out as the
# canonical Wayland text-injection path. `ydotool` is a fallback for
# compositors that don't ship wtype (rare; wtype is in most distros).
#
# Detection uses BOTH `WAYLAND_DISPLAY` and `XDG_SESSION_TYPE=wayland`
# because some compositors (e.g. sway launched from a TTY) set the latter
# but not the former in the spawned process's env. The existing
# :func:`_is_wayland_session` helper checks only `WAYLAND_DISPLAY` (its
# tests pin that contract), so we use a separate helper here for the
# broader detection.

_WTYPE_SHORT_TEXT_THRESHOLD = 300  # chars; matches XPLAT-2 recommendation


def _is_wayland_paste_session() -> bool:
    """Return True if running on a Linux Wayland session (paste routing).

    XPLAT-15: broader than :func:`_is_wayland_session` — also accepts
    ``XDG_SESSION_TYPE=wayland`` for compositors that don't set
    ``WAYLAND_DISPLAY`` in the spawned process's env.
    """
    if not is_linux():
        return False
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _have_wtype() -> bool:
    """Return True if `wtype` (Wayland text-injection tool) is on PATH."""
    return bool(shutil.which("wtype"))


def _linux_paste_via_wtype(text: str | None) -> None:
    """Paste on Wayland via `wtype`.

    XPLAT-15: pynput is X11-only and silently no-ops on Wayland. `wtype`
    is the canonical Wayland text-injection tool.

    CLIP-10 (High, Wayland perf): we ALWAYS use the clipboard path
    (``wtype -k ctrl+v``) instead of typing short text directly with
    ``wtype -d 50``. The previous short-text path used a 50ms/keystroke
    delay, which made pasting 300 chars take ~15 seconds — a noticeable
    UX regression for short dictations. Since :meth:`ClipboardManager.copy`
    already populated the Wayland clipboard via ``wl-copy``, the
    ``Ctrl+V`` path is always available and is O(1) regardless of text
    length.

    Raises ``RuntimeError`` if `wtype` is missing or exits non-zero, or
    ``subprocess.TimeoutExpired``-derived ``RuntimeError`` on hang (5s
    cap — matches the wl-clipboard timeout per XPLAT-7).
    """
    # CLIP-10: always paste from clipboard via Ctrl+V. The previous
    # short-text path (`wtype -d 50 -- <text>`) took ~15s for 300 chars.
    cmd = ["wtype", "-k", "ctrl+v"]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"wtype timed out after 5s: {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RuntimeError(f"wtype exited with {proc.returncode}: {stderr.strip()}")


def _linux_copy(text: str) -> None:
    """Copy text to the clipboard on Linux, choosing the right backend.

    On Wayland with wl-clipboard installed: use `wl-copy` (native
    Wayland clipboard).

    Otherwise: fall back to `pyperclip.copy()` (uses xclip/xsel on X11,
    or the XWayland bridge under a Wayland session if X11 tools are
    present).
    """
    if _is_wayland_session() and _have_wl_clipboard():
        try:
            _linux_wayland_copy(text)
            return
        except Exception as exc:
            log.warning("[CLIPBOARD] wl-copy failed (%s) — falling back to pyperclip", exc)
    pyperclip.copy(text)


def _linux_paste() -> str:
    """Read clipboard text on Linux, choosing the right backend.

    Mirrors :func:`_linux_copy` — uses `wl-paste` on Wayland when
    available, otherwise `pyperclip.paste()`.
    """
    if _is_wayland_session() and _have_wl_clipboard():
        try:
            return _linux_wayland_paste()
        except Exception as exc:
            log.warning("[CLIPBOARD] wl-paste failed (%s) — falling back to pyperclip", exc)
    return pyperclip.paste()


def _copy_to_clipboard(text: str) -> None:
    """Platform-aware clipboard copy dispatcher.

    On Linux: routes through :func:`_linux_copy` (Wayland-aware).
    On Windows / macOS: calls ``pyperclip.copy(text)`` directly (the
    Win32 / AppKit backend handles both, no wl-clipboard equivalent).

    Tests that monkeypatch ``clipboard.pyperclip`` continue to work
    because the Linux branch only short-circuits to ``wl-copy`` when
    ``WAYLAND_DISPLAY`` is set — headless test environments fall through
    to ``pyperclip.copy`` unchanged.
    """
    if is_linux():
        _linux_copy(text)
    else:
        pyperclip.copy(text)


def _paste_from_clipboard() -> str:
    """Platform-aware clipboard read dispatcher (mirrors _copy_to_clipboard)."""
    if is_linux():
        return _linux_paste()
    return pyperclip.paste()


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
        if not is_windows():
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
                log.warning("[CLIPBOARD] OpenClipboard failed (err=%d)", ctypes.windll.kernel32.GetLastError())
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
        if not is_windows():
            return 0
        try:
            import ctypes

            user32 = ctypes.windll.user32
            if hasattr(user32, "GetClipboardSequenceNumber"):
                return user32.GetClipboardSequenceNumber()
        except Exception:
            pass
        return 0


def _win32_empty_clipboard() -> None:
    """PLAT-006: Empty the clipboard via the Win32Clipboard abstraction.

    Called before pyperclip.copy() on Windows to clear stale clipboard
    formats (e.g. rich text artifacts from a previous copy).
    """
    if not is_windows():
        return
    try:
        with Win32Clipboard() as clip:
            clip.empty()
    except Exception:
        pass


# ─── ARCH-11: clipboard target-safety extraction ───────────────────
# The Win32 UI Automation focus / password-field / elevated-target
# detection (PLAT-013/014, PERF-FIX-001) was extracted to
# ``clipboard_target_safety.py`` to untangle it from the clipboard I/O
# helpers. We re-export the names here so internal callers
# (``_is_safe_paste_target``) and external tests that patch
# ``voice_typer.server.clipboard.<name>`` keep working unchanged.
from voice_typer.server.clipboard_target_safety import (  # noqa: E402,F401
    _CRED_DIALOG_CLASSES,
    _UIA_MODULE,
    _UIA_SINGLETON,
    _UIA_SINGLETON_INIT_ATTEMPTED,
    _WE_ELEVATED,
    _focused_window_is_credential_dialog,
    _get_uia_focused_element,
    _get_uia_singleton,
    _get_we_elevated,
    _is_content_editable,
    _is_elevated_target,
    _is_password_field,
)


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
        # PLAT-SECURE: last copied text for seq-mismatch recovery in paste().
        # ADR-0010 §5.1: kept (not removed) because paste() still re-copies
        # on clipboard sequence number mismatch.
        self._last_copied_text: str = ""
        # ADR-0010 §5.6 / DP8: removed ``_clear_thread`` and
        # ``_saved_clipboard`` — dead code replaced by the snapshot
        # return value of copy() and the daemon-thread restore in paste().
        # PLAT-SECURE (revised): cached config flag for clipboard_save_restore.
        # Initialized to True (default) and refreshed via refresh_config()
        # when the user changes the setting. Used to gate snapshot capture
        # in copy() (DP7 — the flag is now actually consulted).
        self._clipboard_save_restore_enabled: bool = True
        # ADR-0010 §5.6 / §5.3: cached restore delay in milliseconds. Read
        # by paste() when scheduling the daemon-thread restore. Refreshed
        # at runtime via refresh_config().
        self._restore_delay_ms: int = 150

    def refresh_config(self, config) -> None:
        """Refresh cached config flags from a Config object.

        ADR-0010 §5.5: called from ``service.apply_config()`` after
        ``config.save()`` (inside the config-mutation lock). Keeps the
        live ClipboardManager in sync with runtime config changes —
        including the ``paste_enabled`` ↔ ``paste_on_stop`` mirror
        (§2.12), which is otherwise stale until restart.

        Without the ``paste_on_stop`` mirror, toggling auto-paste in the
        UI leaves ``paste_enabled`` stale and auto-paste / repaste
        silently no-op until restart (§2.12).
        """
        try:
            self._clipboard_save_restore_enabled = bool(getattr(config, "clipboard_save_restore", True))
        except Exception:
            self._clipboard_save_restore_enabled = True  # safe default

        try:
            self._restore_delay_ms = int(getattr(config, "clipboard_restore_delay_ms", 150))
        except Exception:
            self._restore_delay_ms = 150

        # §2.12: mirror paste_on_stop → paste_enabled so a runtime toggle
        # of auto-paste actually takes effect. Without this, paste()'s
        # internal gate stays stale and auto-paste (and repaste) silently
        # no-op until restart.
        try:
            self.paste_enabled = bool(getattr(config, "paste_on_stop", True))
        except Exception:
            self.paste_enabled = True

        log.debug(
            "[CLIPBOARD] refresh_config: save_restore=%s, restore_delay=%dms, paste_enabled=%s",
            self._clipboard_save_restore_enabled,
            self._restore_delay_ms,
            self.paste_enabled,
        )

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

        CLIP-3 (Medium, Security): the outer ``except Exception`` now
        distinguishes "safety-check infrastructure is broken" from
        "target is safe." Previously any exception (including from
        ``_is_password_field`` or ``_is_elevated_target``) returned
        ``True`` (fail-open) — meaning a broken UIA install would
        silently disable credential-prompt blocking. Now:

          * Exceptions from ``_is_password_field`` or
            ``_is_elevated_target`` → log WARNING, return ``False``
            (fail-closed). We'd rather skip paste than risk pasting
            into a credential prompt with broken detection.
          * Exceptions from ``_is_content_editable`` → log DEBUG,
            continue (fail-open). contentEditable is informational
            only — not a security gate.
          * Truly outer exceptions (e.g. ``ctypes`` itself broken) →
            still fail-open. This indicates a broken Python install,
            not a security infra issue.

        CLIP-4 (Perf): the focused UIA element is fetched ONCE at the
        top via :func:`_get_uia_focused_element` and passed to both
        :func:`_is_password_field` and :func:`_is_content_editable`.
        Previously each helper fetched the focused element separately
        (2x ``GetFocusedElement`` RPCs per paste).

        CLIP-5 (Perf): ``CoInitialize`` / ``CoUninitialize`` are
        hoisted here to wrap both ``_is_password_field`` and
        ``_is_content_editable`` calls. Previously each helper did
        its own init/teardown (2x ``CoInitialize`` + 2x
        ``CoUninitialize`` per paste).

        CLIP-12 (Perf): ``GetForegroundWindow`` is fetched ONCE at
        the top and passed to ``_is_elevated_target`` and
        ``_is_password_field`` (for the cred-dialog fallback).
        Previously each helper fetched it separately.
        """
        if not is_windows():
            return True
        try:
            import ctypes

            user32 = ctypes.windll.user32
            # CLIP-12: fetch hwnd ONCE and pass to all helpers.
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

            # PLAT-013 (revised): Block paste if the target is elevated
            # and we are not. The previous code called _is_elevated_target()
            # but discarded the return value — only the side-effect (a log
            # line inside the function) was observed, and the paste
            # proceeded anyway. This was unsafe: if the target is elevated
            # (e.g. an Administrator cmd window), our SendInput will be
            # silently blocked by UIPI, but we'd still try to paste —
            # potentially into the wrong window if the user switched focus
            # at the last moment.
            #
            # Now we actually USE the return value: if the target is
            # elevated and we are not, return False to abort the paste.
            # The user will see no paste and can investigate (the function
            # logs a warning explaining why).
            #
            # CLIP-3: fail-closed on exception — if the elevation check
            # itself raises, we block paste rather than risk UIPI failure.
            try:
                if _is_elevated_target(hwnd):
                    log.warning(
                        "[CLIPBOARD] Target window is elevated but we are not — blocking paste to avoid UIPI failure"
                    )
                    return False
            except Exception:
                log.warning(
                    "[CLIPBOARD] _is_elevated_target check raised — blocking paste (fail-closed, CLIP-3)",
                    exc_info=True,
                )
                return False

            # CLIP-4/5: fetch the focused UIA element ONCE and hoist
            # CoInitialize/CoUninitialize to wrap both password-field
            # and content-editable checks. Falls back to the
            # credential-dialog heuristic when comtypes is unavailable.
            focused = None
            com_initialized = False
            try:
                import comtypes

                comtypes.CoInitialize()
                com_initialized = True
                focused = _get_uia_focused_element()
            except ImportError:
                # comtypes unavailable — _is_password_field will fall
                # through to its ImportError branch and use the
                # window-class heuristic. Pass focused=None so the
                # helper knows to fetch it itself (which will also
                # fail safely).
                pass
            except Exception:
                # COM init failed — log and proceed. _is_password_field
                # will retry CoInitialize (idempotent on same thread).
                log.debug("[CLIPBOARD] CoInitialize failed in _is_safe_paste_target", exc_info=True)

            try:
                # PLAT-014: check if the focused element is a password field.
                # CLIP-3: fail-closed on exception — if password-field
                # detection itself raises, block paste rather than risk
                # pasting into a credential prompt.
                try:
                    if _is_password_field(focused, hwnd):
                        return False
                except Exception:
                    log.warning(
                        "[CLIPBOARD] _is_password_field check raised — blocking paste (fail-closed, CLIP-3)",
                        exc_info=True,
                    )
                    return False

                # PLAT-CONTENT: check if the focused element is a
                # contentEditable element (rich editor like Word, Gmail
                # compose, etc.). We don't block paste — just log it so
                # the user knows the paste target supports rich text and
                # our plain-text paste may lose formatting.
                #
                # CLIP-3: keep fail-OPEN here — contentEditable is
                # informational, not a security gate.
                try:
                    if _is_content_editable(focused):
                        log.info(
                            "[CLIPBOARD] Paste target is a contentEditable element — "
                            "pasting plain text (rich text formatting may be lost)"
                        )
                except Exception:
                    log.debug("[CLIPBOARD] contentEditable check failed", exc_info=True)
            finally:
                if com_initialized:
                    with contextlib.suppress(Exception):
                        import comtypes as _ct  # local re-import; safe inside finally

                        _ct.CoUninitialize()

            return True
        except Exception:
            # CLIP-3: outer exception — fail-open ONLY when truly
            # broken infra (e.g. ctypes itself unavailable). This is
            # rare and indicates a broken Python install rather than
            # a security infra issue. Security-check exceptions are
            # caught earlier (per-helper) and fail-closed.
            log.warning("[CLIPBOARD] _is_safe_paste_target outer exception — failing open", exc_info=True)
            return True  # Fail open — don't block paste on outer infra error

    @staticmethod
    def _is_terminal_process(process_name: str | None) -> bool:
        if not process_name:
            return False
        return process_name.lower().strip() in _TERMINAL_PROCESS_NAMES

    @staticmethod
    def _detect_focused_process() -> str | None:
        """Detect the focused process name (Windows only)."""
        if not is_windows():
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

            process_query_limited_information = 0x1000
            h_process = kernel32.OpenProcess(process_query_limited_information, False, pid.value)
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

    def copy(self, text: str) -> ClipboardSnapshot | None:
        """Copy text to the clipboard. Returns a snapshot of the prior content.

        ADR-0010 §5.2 / DP4 / DP7.

        Returns ``None`` if:
          * ``text`` is empty (no-op).
          * ``clipboard_save_restore`` is disabled (no snapshot captured).

        Returns a :class:`ClipboardSnapshot` (which may have an empty
        ``items`` list if the clipboard was empty) when snapshot capture
        succeeds. The caller is responsible for restoring the snapshot
        after the text has been consumed — see :meth:`paste` and
        :meth:`restore_now`.

        Raises :class:`ClipboardCopyError` if the text copy/verify fails
        after retries. The snapshot, if captured, is restored before
        raising so the clipboard is never left torn. The caller should
        write the transcription to crash recovery on this exception.
        """
        if not text:
            return None

        # ① SNAPSHOT (gated by config flag — DP7). The snapshot is a
        # value returned to the caller; it is NOT stored on self. This
        # makes overlapping cycles safe (DP4).
        snapshot: ClipboardSnapshot | None = None
        if self._clipboard_save_restore_enabled:
            snapshot = ClipboardSnapshot.capture()
            # snapshot may be None if capture failed (clipboard locked
            # or empty). That's OK — we just won't restore. Log for
            # debugging.
            if snapshot is None:
                log.debug("[CLIPBOARD] Snapshot capture returned None (clipboard locked or empty)")

        try:
            # ② WIN32 EMPTY (existing PLAT-006). On Windows, empty the
            # clipboard before copying to clear rich text artifacts from
            # the previous clipboard content. PLAT-027: uses
            # Win32Clipboard abstraction instead of direct ctypes calls.
            _win32_empty_clipboard()

            # ③ COPY TEXT (existing PLAT-007 retry on ERROR_ACCESS_DENIED).
            # ADR-0020 §6.6: on Linux Wayland, route through _copy_to_clipboard
            # which uses `wl-copy` instead of pyperclip's xclip/xsel (which
            # are X11-only and silently no-op under native Wayland apps).
            for attempt in range(3):
                try:
                    _copy_to_clipboard(text)
                    break
                except OSError as copy_err:
                    # ERROR_ACCESS_DENIED = 5 on Windows. pyperclip wraps
                    # the underlying win32 clipboard error as OSError or
                    # pywintypes.error (which is a subclass of OSError).
                    winerror = getattr(copy_err, "winerror", None)
                    if winerror == 5 and attempt < 2:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    raise copy_err

            # ④ VERIFY (existing PLAT-PASTEVR).
            for verify_attempt in range(3):
                try:
                    actual = _paste_from_clipboard()
                    if actual == text:
                        break
                    log.warning(
                        "[CLIPBOARD] Clipboard verification failed (attempt %d/3) — expected %d chars, got %d.",
                        verify_attempt + 1,
                        len(text),
                        len(actual) if actual else 0,
                    )
                    _copy_to_clipboard(text)
                except Exception:
                    pass  # pyperclip.paste() may not be supported on all platforms
            else:
                log.error("[CLIPBOARD] Clipboard verification still failed after 3 retries")

            # ⑤ STORE METADATA (existing PLAT-CLIPRACE / PLAT-SECURE).
            self._last_copied_text = text
            self._clipboard_seq = self._get_clipboard_sequence_number()
            log.info(
                "[CLIPBOARD-AUDIT] Copied %d chars to clipboard (seq=%d, snapshot=%s)",
                len(text),
                self._clipboard_seq,
                "captured" if snapshot is not None else "none",
            )
            return snapshot

        except Exception as e:
            log.error("[CLIPBOARD] Failed to copy to clipboard: %s", e)
            # If copy failed, restore the snapshot immediately so we don't
            # leave the clipboard in a torn state, then signal failure.
            if snapshot is not None:
                with contextlib.suppress(Exception):
                    snapshot.restore()
            raise ClipboardCopyError(str(e)) from e

    def _release_stuck_modifiers(self) -> None:
        """Release any stuck modifier keys before paste.

        PLAT-STUCK: if a previous paste was interrupted (e.g. exception
        during _send_keystroke_sequence), Ctrl/Shift/Alt/Cmd may be
        left in a pressed state. Releasing them before the next paste
        prevents stuck-modifier behavior.
        """
        # TASK-10: pynput is optional — _Key / _Controller stay None in
        # headless environments, and self._keyboard is None whenever
        # _Controller is unavailable. Guard both before touching the
        # keyboard controller.
        if _Key is None or self._keyboard is None:
            return
        try:
            for key in (_Key.ctrl, _Key.shift, _Key.alt, _Key.cmd):
                with contextlib.suppress(Exception):
                    self._keyboard.release(key)
        except Exception:
            pass

    def _safe_key_press(self, modifier, char) -> None:
        """PLAT-STUCK: Press modifier + char with guaranteed modifier release.

        Wraps the modifier press/release in try/finally to ensure the
        modifier key is always released even if the character press or
        release raises an exception.
        """
        # TASK-10: pynput may be unavailable (headless / sandboxed).
        # Without this guard, .press() / .release() would raise
        # AttributeError on the None controller, defeating the
        # try/finally cleanup below.
        if self._keyboard is None:
            log.debug("[CLIPBOARD] _safe_key_press skipped — no keyboard controller")
            return
        try:
            self._keyboard.press(modifier)
            self._keyboard.press(char)
            self._keyboard.release(char)
        finally:
            self._keyboard.release(modifier)

    def paste(
        self,
        snapshot: ClipboardSnapshot | None = None,
        restore_delay: float | None = None,
        pasted_text: str | None = None,
        force: bool = False,
        pasted_seq: int | None = None,
    ) -> bool:
        """Send a paste keystroke into the focused window.

        ADR-0010 §5.3 / DP1 / DP2 / DP3 / DP4.

        If a snapshot is provided, a delayed restore is ALWAYS scheduled
        on a daemon thread (DP3) — even when the keystroke is later
        skipped (pynput missing, rate-limited, paste disabled, unsafe
        target). This guarantees the clipboard borrow is always paired
        with a restore (DP1/DP2), so the user's original clipboard is
        never orphaned.

        ``force=True`` bypasses the ``paste_enabled`` gate. Used by
        ``repaste_last()`` — a manual user action that must never be
        coupled to the auto-paste (``paste_on_stop``) setting. See §2.12.

        The snapshot and the expected pasted text are passed as value
        parameters — no instance state is read or written for the
        snapshot or the restore guard (DP4). This makes overlapping
        cycles safe: cycle B's copy() cannot corrupt cycle A's restore,
        because every cycle carries its own expected text. The
        transcription thread is never blocked by the restore (it runs on
        a daemon thread).

        Returns ``True`` if a keystroke was sent, ``False`` if paste is
        disabled, rate-limited, or blocked by the safety check.
        """
        # ── schedule restore FIRST, before any early return (DP1/DP2) ──
        # The borrow happened in copy(); failure to send the keystroke
        # must not prevent the paired restore. ``_delayed_restore``
        # re-checks the clipboard before restoring, so this is safe even
        # if the paste never lands.
        #
        # CLIP-8: register the pending restore in the module-level
        # _pending_restores list so the atexit handler can force-restore
        # it if the app exits before the daemon thread fires. The daemon
        # thread removes its entry on normal completion.
        _pending_entry: tuple[Any, Any, str, float] | None = None
        if snapshot is not None:
            delay = restore_delay if restore_delay is not None else (self._restore_delay_ms / 1000.0)
            expected = pasted_text if pasted_text is not None else self._last_copied_text
            _pending_entry = (self, snapshot, expected, delay)
            with _pending_restores_lock:
                _pending_restores.append(_pending_entry)
            threading.Thread(
                target=self._delayed_restore,
                args=(snapshot, expected, delay, _pending_entry),
                daemon=True,
                name="clipboard-restore",
            ).start()

        # PLAT-STUCK: release any stuck modifier keys before pasting
        self._release_stuck_modifiers()

        # TASK-10: pynput is optional. If both _Key and _Controller are
        # unavailable AND we're not on Windows (where _send_ctrl_v_win32
        # uses SendInput directly without pynput), there's no way to
        # synthesize a paste keystroke. Bail out early rather than
        # AttributeError on _Key.cmd / _Key.ctrl below.
        #
        # XPLAT-15: on Linux Wayland, we route through `wtype` (no pynput
        # needed), so don't bail out even if pynput is missing — let the
        # Wayland branch below handle it.
        if _Controller is None and not is_windows():
            if is_linux() and _is_wayland_paste_session() and _have_wtype():
                log.debug("[CLIPBOARD] pynput unavailable — will use wtype on Wayland")
            else:
                log.warning("[CLIPBOARD] pynput unavailable — cannot paste")
                return False

        # CLIP-13: rate-limit check moved BEFORE seq-mismatch re-copy.
        # Previously, a rate-limited paste would still trigger a
        # re-copy of the clipboard (via pyperclip.copy) even though no
        # keystroke would be sent — wasting the re-copy work and
        # potentially racing with a concurrent paste cycle. Now we
        # short-circuit on rate-limit before doing any clipboard work.
        now = time.monotonic()
        if now - self._last_paste_time < self._PASTE_RATE_LIMIT:
            log.info(
                "[CLIPBOARD] Paste rate-limited (%.0f ms since last paste)",
                (now - self._last_paste_time) * 1000,
            )
            return False

        # §2.12: ``force`` bypasses the paste_enabled gate so repaste
        # (a manual user action) works regardless of the auto-paste
        # (paste_on_stop) setting.
        if not self.paste_enabled and not force:
            log.info("[CLIPBOARD] Paste disabled by config -- skipping keystroke")
            return False

        # PLAT-CLIPRACE (revised): verify clipboard wasn't modified between
        # copy and paste. The previous code only LOGGED a warning when the
        # clipboard sequence number changed between copy() and paste() —
        # paste proceeded anyway with potentially stale clipboard content.
        #
        # Now we actually RECOVER: if the seq changed, re-copy the
        # text before pasting. This handles the case where another app
        # (clipboard manager, password manager, screenshot tool) overwrote
        # the clipboard between our copy() and the paste keystroke.
        if is_windows() and hasattr(self, "_clipboard_seq"):
            # CRIT-3: use the request-scoped seq when provided (set by
            # copy() for THIS request), falling back to the instance
            # attribute for repaste/callers that don't thread it. This
            # prevents a concurrent copy() in another request from
            # clobbering the seq we validate against.
            expected_seq = pasted_seq if pasted_seq is not None else self._clipboard_seq
            if expected_seq:
                current_seq = self._get_clipboard_sequence_number()
                if current_seq != expected_seq:
                    log.warning(
                        "[CLIPBOARD] Clipboard modified between copy and paste (seq %d -> %d) — re-copying before paste",
                        expected_seq,
                        current_seq,
                    )
                    # Re-copy the text we want to paste. Use the stored
                    # _last_copied_text to avoid passing the wrong content.
                    #
                    # CLIP-7 (Medium, Wayland): use _copy_to_clipboard()
                    # instead of pyperclip.copy() so the Wayland dispatcher
                    # routes through `wl-copy` on Linux Wayland sessions.
                    # Previously, paste()'s seq-mismatch re-copy bypassed
                    # the dispatcher and called pyperclip.copy directly —
                    # which uses xclip/xsel (X11-only) and silently no-ops
                    # under native Wayland apps, leaving the re-copy stale.
                    try:
                        if self._last_copied_text:
                            _copy_to_clipboard(self._last_copied_text)
                            # Brief delay to let the clipboard settle
                            time.sleep(0.02)
                            # Update seq so a subsequent mismatch check is accurate
                            self._clipboard_seq = self._get_clipboard_sequence_number()
                    except Exception as exc:
                        log.error(
                            "[CLIPBOARD] Failed to re-copy after seq mismatch: %s — paste may deliver stale content",
                            exc,
                        )

        # PLAT-RDP: increase paste delay in RDP sessions where clipboard
        # sync is slower.
        #
        # CLIP-11 (Low, Perf): drop the unconditional 20ms ``paste_delay``
        # sleep. The 20ms was applied to every paste on every platform,
        # adding latency without benefit (the seq-mismatch re-copy path
        # already has its own 20ms settle delay when it fires). The 100ms
        # RDP sleep is preserved because RDP clipboard sync genuinely
        # needs the extra delay.
        paste_delay = 0.0
        if is_windows():
            try:
                from voice_typer.server.server_platform import is_remote_session

                if is_remote_session():
                    paste_delay = 0.10
                    log.info(
                        "[CLIPBOARD] RDP session detected — increasing paste delay to %dms", int(paste_delay * 1000)
                    )
            except Exception:
                pass

        if not self._is_safe_paste_target():
            log.info("[CLIPBOARD] Paste blocked — security-sensitive window in foreground")
            return False

        try:
            if paste_delay > 0:
                time.sleep(paste_delay)

            # CRIT-2: re-validate the target RIGHT BEFORE sending the
            # keystroke. The safety check above (line ~1563) ran before the
            # paste_delay sleep; the foreground window could have changed
            # during that window (TOCTOU). If the target is now unsafe
            # (e.g. focus moved to a credential prompt), abort — do NOT
            # send the paste into the wrong/unsafe window.
            if not self._is_safe_paste_target():
                log.info("[CLIPBOARD] Paste blocked — foreground target became unsafe during paste delay")
                return False

            process_name = self._detect_focused_process()
            is_terminal = self._is_terminal_process(process_name)

            # PLAT-CONTENT: log when the paste target appears to be a rich editor
            if process_name and process_name.lower() in _RICH_EDITOR_PROCESS_NAMES:
                log.info(
                    "[CLIPBOARD] Paste target appears to be a rich editor (%s) — "
                    "pasting plain text (contentEditable detection not implemented)",
                    process_name,
                )

            # XPLAT-15: on Linux Wayland, pynput silently no-ops (it's
            # X11-only). Route through `wtype` instead — uses Ctrl+V
            # (the clipboard was already populated by copy()). Falls
            # through to the pynput path on X11, when wtype isn't
            # installed, or on non-Linux platforms.
            #
            # CLIP-10: ``_linux_paste_via_wtype`` now always uses the
            # Ctrl+V clipboard path (no more ``-d 50`` keystroke delay
            # for short text).
            use_wayland_wtype = is_linux() and _is_wayland_paste_session() and _have_wtype()
            paste_succeeded = True
            if is_terminal:
                if is_macos():
                    self._safe_key_press(_Key.cmd, "v")
                elif use_wayland_wtype:
                    _linux_paste_via_wtype(self._last_copied_text)
                else:
                    self._safe_key_press(_Key.shift, _Key.insert)
            elif is_macos():
                self._safe_key_press(_Key.cmd, "v")
            elif is_windows():
                # CLIP-14: check the return value of _send_ctrl_v_win32.
                # A False return means SendInput reported partial success
                # (1..3 of 4 events delivered) — the paste did NOT
                # complete cleanly. Previously this was silently dropped
                # (the user saw nothing happen and no warning). Now we
                # log a warning and return False so the user knows the
                # paste failed.
                paste_succeeded = self._send_ctrl_v_win32()
            elif use_wayland_wtype:
                _linux_paste_via_wtype(self._last_copied_text)
            else:
                self._safe_key_press(_Key.ctrl, "v")

            if not paste_succeeded:
                log.warning(
                    "[CLIPBOARD] Auto-paste failed (SendInput partial success — UIPI may have blocked) (CLIP-14)"
                )
                return False

            self._last_paste_time = time.monotonic()
            log.info(
                "[CLIPBOARD-AUDIT] Sent paste keystroke (terminal=%s, target=%s, restore_scheduled=%s)",
                is_terminal,
                process_name or "unknown",
                snapshot is not None,
            )
            return True
        except Exception as e:
            log.warning("[CLIPBOARD] Auto-paste failed (clipboard still has the text): %s", e)
            return False

    def _delayed_restore(
        self,
        snapshot: "ClipboardSnapshot",
        pasted_text: str,
        delay: float,
        pending_entry: Any = None,
    ) -> None:
        """Restore a snapshot after a delay. Runs on a daemon thread.

        ADR-0010 §5.3 / DP3.

        Defensive check: if the clipboard no longer contains
        ``pasted_text`` (user copied something else, or target app
        rewrote it), skip restore to avoid clobbering the new content.

        CR-3 fix: the ``pending_entry`` parameter is the tuple that
        ``paste()`` appended to ``_pending_restores`` before spawning
        this daemon thread. The entry is removed from the list in the
        ``finally`` block below so the list does not grow unboundedly
        across many paste invocations (memory leak) and so the atexit
        handler does not double-restore an already-restored snapshot.
        The default ``None`` preserves backward compatibility with
        legacy 3-arg direct calls (e.g. existing tests at
        ``tests/test_clipboard_borrow_restore.py:301/318``).
        """
        try:
            time.sleep(delay)
            try:
                # ADR-0020 §6.6: on Linux Wayland, _paste_from_clipboard
                # uses `wl-paste` so the defensive check actually reads
                # the Wayland clipboard (pyperclip.paste() would no-op).
                current = _paste_from_clipboard()
            except Exception:
                current = None
            if current == pasted_text:
                snapshot.restore()
                log.info(
                    "[CLIPBOARD-AUDIT] Restored snapshot after %.3fs delay",
                    delay,
                )
            else:
                log.debug(
                    "[CLIPBOARD-AUDIT] Restore skipped — clipboard changed (current=%d chars, expected=%d chars)",
                    len(current) if current else 0,
                    len(pasted_text),
                )
        except Exception:
            log.exception("[CLIPBOARD] Delayed restore failed")
        finally:
            # CR-3: remove this entry from _pending_restores under the
            # lock. ValueError is benign — the atexit handler may have
            # already cleared the list while the daemon thread slept.
            if pending_entry is not None:
                try:
                    with _pending_restores_lock:
                        try:
                            _pending_restores.remove(pending_entry)
                        except ValueError:
                            pass  # already removed by atexit or another path
                except Exception:  # pragma: no cover — catastrophic lock failure
                    log.exception("[CLIPBOARD] Failed to remove pending restore entry")

    def restore_now(self, snapshot: ClipboardSnapshot | None) -> None:
        """Restore a snapshot immediately (no paste keystroke, no delay).

        ADR-0010 §5.4 / DP2.

        Used when copy() borrowed the clipboard but no paste follows
        (``paste_on_stop = False``). This is the critical fix for the
        data-loss bug where ``paste_on_stop=False`` permanently
        destroyed the user's clipboard.
        """
        if snapshot is None:
            return
        try:
            snapshot.restore()
            log.info("[CLIPBOARD-AUDIT] Restored snapshot immediately (no paste)")
        except Exception:
            log.exception("[CLIPBOARD] Immediate restore failed")

    def _send_keystroke_sequence(self, modifier, char) -> None:
        # PLAT-STUCK: ensure modifier is always released even on exception.
        # Uses a robust try/finally pattern that presses modifier + char,
        # releases in reverse order, and guarantees ALL keys are released
        # in the finally block even if an intermediate release raises.
        # TASK-10: guard None keyboard — pynput is optional.
        if self._keyboard is None:
            log.debug("[CLIPBOARD] _send_keystroke_sequence skipped — no keyboard controller")
            return
        try:
            self._keyboard.press(modifier)
            self._keyboard.press(char)
            self._keyboard.release(char)
            self._keyboard.release(modifier)
        finally:
            # Double-release: guarantee modifier is freed even if the
            # normal release path above was skipped by an exception.
            for key in (modifier, char):
                with contextlib.suppress(Exception):
                    self._keyboard.release(key)

    def _send_ctrl_v_win32(self) -> bool:
        """Send Ctrl+V via a single atomic SendInput batch.

        PLAT-001: On Windows, we always prefer SendInput over
        pynput.keyboard.Controller because pynput's Controller is
        blocked by UIPI when targeting elevated processes from a
        non-elevated one.  Our direct SendInput call is subject to the
        same UIPI restriction, but we log the failure explicitly
        instead of silently dropping it.

        Returns ``True`` if the full Ctrl+V sequence was delivered
        (SendInput returned 4) OR the pynput fallback was invoked
        (best-effort — assumed success since pynput raises on failure).
        Returns ``False`` on partial success (SendInput returned 1..3)
        so the caller can surface a warning without risking a
        double-paste.
        """
        import ctypes

        from pynput._util.win32 import (
            INPUT,
            KEYBDINPUT,
            INPUT_union,
            SendInput,
        )

        vk_control = 0x11
        vk_v = 0x56

        events = (INPUT * 4)(
            INPUT(
                INPUT.KEYBOARD,
                INPUT_union(ki=KEYBDINPUT(wVk=vk_control, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
            ),
            INPUT(
                INPUT.KEYBOARD,
                INPUT_union(ki=KEYBDINPUT(wVk=vk_v, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
            ),
            INPUT(
                INPUT.KEYBOARD,
                INPUT_union(ki=KEYBDINPUT(wVk=vk_v, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
            ),
            INPUT(
                INPUT.KEYBOARD,
                INPUT_union(ki=KEYBDINPUT(wVk=vk_control, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)),
            ),
        )

        result = SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
        if result != 4:
            # PLAT-001 (revised): SendInput returns the number of events
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
            # so falling back to pynput is safe (no double-paste risk).
            log.warning(
                "[CLIPBOARD] SendInput returned %d (expected 4) — "
                "this may be caused by UIPI blocking if the target is elevated.",
                result,
            )
            if 1 <= result <= 3:
                # Partial success — synthesize KEYUP for any keys that may
                # be stuck down (Ctrl and/or V) to avoid leaving the
                # keyboard in a wedged state.
                log.error(
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
                            INPUT_union(
                                ki=KEYBDINPUT(wVk=vk_v, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)
                            ),
                        ),
                        INPUT(
                            INPUT.KEYBOARD,
                            INPUT_union(
                                ki=KEYBDINPUT(wVk=vk_control, wScan=0, dwFlags=KEYBDINPUT.KEYUP, time=0, dwExtraInfo=0)
                            ),
                        ),
                    )
                    SendInput(2, ctypes.byref(release_events), ctypes.sizeof(INPUT))
                except Exception:
                    log.debug("[CLIPBOARD] failed to synthesize KEYUP cleanup", exc_info=True)
                return False  # paste did not complete cleanly; do not proceed

            # result == 0: complete failure — safe to fall back to pynput
            # (no events were delivered, so no double-paste risk).
            log.info("[CLIPBOARD] SendInput returned 0 — falling back to pynput Controller")
            # PLAT-001: fallback to pynput Controller as last resort.
            # Note: pynput.keyboard.Controller is also subject to UIPI,
            # so this may also fail silently.
            self._safe_key_press(_Key.ctrl, "v")
            return True  # pynput fallback invoked — best-effort success

        # SendInput returned 4 — full Ctrl+V sequence delivered.
        return True
