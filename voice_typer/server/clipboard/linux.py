"""Linux / Wayland clipboard primitives ( split)."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

# Use a local alias to avoid circular import at module load time. The
from voice_typer.server import clipboard as _cb

if TYPE_CHECKING:  # pragma: no cover
    pass

# Logs go through `_cb.log` (package logger); do not define a local log.


# Memoize shutil.which for invariant Wayland binaries (wl-copy/wl-paste/wtype).
@functools.cache
def _shutil_which_cached(binary: str) -> str | None:
    """Return ``shutil.which(binary)`` with the result memoised."""
    import shutil  # noqa: PLC0415, stdlib, kept lazy to mirror existing style

    return shutil.which(binary)


# Lazy pynput import (needs a display). State lives on the package (_cb._Key/_Controller)
def _ensure_pynput_imported() -> None:
    """Lazily import pynput.keyboard Key and Controller on first use."""
    if _cb._Key is not None and _cb._Controller is not None:
        return
    from pynput.keyboard import (
        Controller as _c,  # noqa: N813
        Key as _k,  # noqa: N813
    )

    _cb._Key = _k
    _cb._Controller = _c


# Used by ClipboardManager._is_terminal_process / paste() for routing.

# Terminal process names (lowercase, with extension) that require
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
    # ``gnome-terminal-server`` is the D-Bus-activated backend process
    "gnome-terminal-server",
    # Ptyxis is the GNOME-adjacent terminal shipped by Fedora Workstation
    "ptyxis",
    "ptyxis-agent",
    # BlackBox is a GTK4/libadwaita terminal for GNOME, its binary
    "blackbox",
    # Tabby (formerly "Terminus") is an predecessor-based terminal whose
    "tabby",
    # cosmic-term is the System76 COSMIC desktop terminal.
    "cosmic-term",
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
_RICH_EDITOR_PROCESS_NAMES: set[str] = {
    "winword.exe",
    "wordpad.exe",
    "soffice.bin",
    "soffice.exe",
    "notion.exe",
    "obsidian.exe",
}


# ADR-0020 §6.6: Wayland uses wl-copy/wl-paste (pyperclip is X11-only).


def _is_wayland_session(*, broad: bool = False) -> bool:
    """Return True if running on a Linux Wayland session."""
    if not _cb.is_linux():
        return False
    import os

    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    if broad:
        # Delegate the env-var check to the platform-wide Wayland
        from voice_typer.server.platform_utils import is_wayland_session

        return is_wayland_session()
    return False


def _have_wl_clipboard() -> bool:
    """Return True if both `wl-copy` and `wl-paste` are on PATH."""
    return bool(_shutil_which_cached("wl-copy") and _shutil_which_cached("wl-paste"))


def _linux_wayland_copy(text: str) -> None:
    """Copy text to the Wayland clipboard via `wl-copy`."""
    if not text:
        # `wl-copy` with no args clears the clipboard; that matches our
        return
    import subprocess

    try:
        proc = subprocess.run(
            ["wl-copy"],
            input=text.encode("utf-8"),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        # re-raise the original ``TimeoutExpired`` directly (preserves
        raise
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RuntimeError(f"wl-copy exited with {proc.returncode}: {stderr.strip()}")


def _linux_wayland_paste() -> str:
    """Read text from the Wayland clipboard via `wl-paste`.

    Returns the clipboard text (may be empty). Raises ``RuntimeError``
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["wl-paste", "--no-newline"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        # re-raise the original ``TimeoutExpired`` directly (preserves
        raise
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RuntimeError(f"wl-paste exited with {proc.returncode}: {stderr.strip()}")
    return proc.stdout.decode("utf-8", errors="replace")


# pynput.keyboard.Controller is X11-only, on a native Wayland session it

_WTYPE_SHORT_TEXT_THRESHOLD = 300  # chars; matches  recommendation


def _is_wayland_paste_session() -> bool:
    """Return True if running on a Linux Wayland session (paste routing)."""
    return _is_wayland_session(broad=True)


def _have_wtype() -> bool:
    """Return True if `wtype` (Wayland text-injection tool) is on PATH."""
    return bool(_shutil_which_cached("wtype"))


def _linux_paste_via_wtype(text: str | None, is_terminal: bool = False) -> None:
    """Paste on Wayland via `wtype`.

    pynput is X11-only and silently no-ops on Wayland. `wtype`
        is the canonical Wayland text-injection tool.

    (High, Wayland perf): we ALWAYS use the clipboard path
        (``wtype -k ctrl+v``) instead of typing short text directly with
        ``wtype -d 50``. The previous short-text path used a 50ms/keystroke
        delay, which made pasting 300 chars take ~15 seconds, a noticeable
        UX regression for short dictations. Since :meth:`ClipboardManager.copy`
        already populated the Wayland clipboard via ``wl-copy``, the
        ``Ctrl+V`` path is always available and is O(1) regardless of text
        length.

    ``is_terminal=True`` selects the terminal-emulator keystroke
        sequence ``ctrl+shift+v`` instead of ``ctrl+v``. Terminal
        emulators (gnome-terminal, konsole, kitty, etc.) bind paste to
        Ctrl+Shift+V; a plain Ctrl+V is interpreted as the literal
        ``^V`` / 0x16 control byte, which corrupts shell input. The
        caller (:meth:`ClipboardManager.paste`) determines
        ``is_terminal`` via :meth:`_is_terminal_process` using the
        ``_TERMINAL_PROCESS_NAMES`` set in this module.

    (Medium, Wayland race): insert a small settle delay
        (~15 ms) BEFORE forking ``wtype``. The clipboard ownership
        handoff from ``wl-copy`` to the compositor is asynchronous —
        ``wl-copy``'s subprocess exit does NOT guarantee the
        compositor has propagated the new selection to clients. If
        ``wtype -k ctrl+v`` is forked too quickly, the focused Wayland
        surface may paste a stale (or empty) selection. The 15 ms
        settle is below the human-perceptible threshold (~50 ms) but
        large enough to cover the wl-copy round-trip on a typical
        compositor (measured 2-8 ms on sway / GNOME mutter). The
        delay is skipped when ``_cb.time`` has been patched out by
        the test suite (which patches it to a MagicMock).

        Raises ``RuntimeError`` if `wtype` is missing or exits non-zero, or
        ``subprocess.TimeoutExpired``-derived ``RuntimeError`` on hang (5s
    cap, matches the wl-clipboard timeout per ).
    """
    # always paste from clipboard via Ctrl+V (or Ctrl+Shift+V for
    import contextlib as _ctxlib
    import subprocess

    with _ctxlib.suppress(AttributeError, TypeError):  # pragma: no cover, defensive
        _cb.time.sleep(0.015)

    paste_key = "ctrl+shift+v" if is_terminal else "ctrl+v"
    cmd = ["wtype", "-k", paste_key]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        # re-raise the original ``TimeoutExpired`` directly (preserves
        raise
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RuntimeError(f"wtype exited with {proc.returncode}: {stderr.strip()}")


def _linux_copy(text: str) -> None:
    """Copy text to the clipboard on Linux, choosing the right backend."""
    if _cb._is_wayland_session() and _cb._have_wl_clipboard():
        try:
            _cb._linux_wayland_copy(text)
            return
        except Exception as exc:
            _cb.log.warning("[CLIPBOARD] wl-copy failed (%s), falling back to pyperclip", exc)
    _cb.pyperclip.copy(text)


def _linux_paste() -> str:
    """Read clipboard text on Linux, choosing the right backend."""
    if _cb._is_wayland_session() and _cb._have_wl_clipboard():
        try:
            return _cb._linux_wayland_paste()
        except Exception as exc:
            _cb.log.warning("[CLIPBOARD] wl-paste failed (%s), falling back to pyperclip", exc)
    return _cb.pyperclip.paste()


def _copy_to_clipboard(text: str) -> None:
    """Platform-aware clipboard copy dispatcher."""
    if _cb.is_linux():
        _cb._linux_copy(text)
    else:
        _cb.pyperclip.copy(text)


def _paste_from_clipboard() -> str:
    """Platform-aware clipboard read dispatcher (mirrors _copy_to_clipboard)."""
    if _cb.is_linux():
        return _cb._linux_paste()
    return _cb.pyperclip.paste()


__all__ = [
    "_RICH_EDITOR_PROCESS_NAMES",
    "_TERMINAL_PROCESS_NAMES",
    "_WTYPE_SHORT_TEXT_THRESHOLD",
    "_copy_to_clipboard",
    "_ensure_pynput_imported",
    "_have_wl_clipboard",
    "_have_wtype",
    "_is_wayland_paste_session",
    "_is_wayland_session",
    "_linux_copy",
    "_linux_paste",
    "_linux_paste_via_wtype",
    "_linux_wayland_copy",
    "_linux_wayland_paste",
    "_paste_from_clipboard",
    "_shutil_which_cached",
]
