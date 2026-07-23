"""Linux / Wayland clipboard primitives (PVT-23 split).

Extracted from the original ``clipboard.py`` monolith. Contains:

* Wayland ``wl-copy`` / ``wl-paste`` fallback (ADR-0020 §6.6).
* Wayland ``wtype`` text-injection fallback (XPLAT-15).
* Platform-aware ``_copy_to_clipboard`` / ``_paste_from_clipboard``
  dispatchers.
* pynput lazy-import helpers (``_Key`` / ``_Controller`` /
  ``_ensure_pynput_imported``).

Design contract: all patchable symbols (``pyperclip``, ``subprocess``,
``is_linux``, ``_is_wayland_session``, etc.) are looked up via the
PACKAGE (``_cb.X``) at call time — NOT via this module's globals —
so test patches like ``monkeypatch.setattr(clip_mod, "pyperclip",
mock)`` actually take effect on the code paths in this module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

# Use a local alias to avoid circular import at module load time. The
# package itself is only partially initialized when this submodule is
# first imported (it is imported BY the package's __init__.py). All
# ``_cb.X`` attribute access happens at function-call time, by which
# point the package is fully initialized.
from voice_typer.server import clipboard as _cb

if TYPE_CHECKING:  # pragma: no cover
    pass

# Local logger — mirrors the package logger name so log records appear
# under the same "voice_typer.server.clipboard" namespace. Tests that
# patch ``voice_typer.server.clipboard.log`` should patch the package
# attribute; this module's ``log`` is a separate Logger object but
# with the same name (so handlers/levels configured on the package
# logger still apply).
log = logging.getLogger("voice_typer.server.clipboard")


# ─── pynput lazy-import helpers ──────────────────────────────────────
#
# Lazy-import pynput at instance creation time, not module import time.
# pynput.keyboard imports a platform backend (X11 on Linux, IOKit on mac,
# Win32 on Windows) that requires a running display / window manager.
# Importing at module level breaks `python -m voice_typer --version`
# in headless containers / SSH sessions without DISPLAY.
#
# TASK-10: _Key / _Controller are lazily populated by
# _ensure_pynput_imported() on first use. They are typed as ``Any`` so
# pyrefly can follow the .cmd / .ctrl / .press() / .release() accesses
# without flagging every call site (the actual pynput import is
# deferred to runtime so headless installs don't break at import time).
#
# PVT-23 note: the actual _Key / _Controller state lives in the PACKAGE
# namespace (``voice_typer.server.clipboard._Key`` / ``._Controller``)
# so test patches like ``patch.object(clip_mod, "_Controller", MagicMock())``
# and ``clip_mod._Key = None`` take effect. This module's
# _ensure_pynput_imported() reads + writes via ``_cb._Key`` /
# ``_cb._Controller``.
def _ensure_pynput_imported() -> None:
    """Lazily import pynput.keyboard Key and Controller on first use.

    Reads and writes the ``_Key`` / ``_Controller`` attributes on the
    PACKAGE (``voice_typer.server.clipboard``), not on this submodule.
    This lets tests patch / reset them via
    ``clip_mod._Key = None`` etc.
    """
    if _cb._Key is not None and _cb._Controller is not None:
        return
    from pynput.keyboard import Controller as _c  # noqa: N813
    from pynput.keyboard import Key as _k  # noqa: N813

    _cb._Key = _k
    _cb._Controller = _c


# ─── Process-name data (terminal + rich-editor lists) ───────────────
# Used by ClipboardManager._is_terminal_process / paste() for routing.
# Defined here (close to the Linux/Wayland primitives) because the
# terminal list spans Linux + Windows + macOS terminal names.

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
    if not _cb.is_linux():
        return False
    import os

    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _have_wl_clipboard() -> bool:
    """Return True if both `wl-copy` and `wl-paste` are on PATH."""
    import shutil

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

    XZ-CLIP-02 (session-XZ, High, Security): the text is piped via
    ``stdin`` (NOT passed as a positional CLI argument). Passing
    dictated text as a CLI arg made it visible to ANY local user via
    ``/proc/<pid>/cmdline`` (world-readable on Linux), leaking
    dictated secrets to other accounts on the box. The pre-fix
    docstring claimed "piped to stdin" but the implementation
    contradicted it — the docstring is now accurate. ``wl-copy`` with
    no positional argument reads text from stdin, so we pass an empty
    argv (``["wl-copy"]``) and feed the UTF-8-encoded text via
    ``input=...``.
    """
    if not text:
        # `wl-copy` with no args clears the clipboard; that matches our
        # "empty text → no-op" semantics in ClipboardManager.copy().
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
    import subprocess

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
    if not _cb.is_linux():
        return False
    import os

    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _have_wtype() -> bool:
    """Return True if `wtype` (Wayland text-injection tool) is on PATH."""
    import shutil

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
    import subprocess

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
    if _cb._is_wayland_session() and _cb._have_wl_clipboard():
        try:
            _cb._linux_wayland_copy(text)
            return
        except Exception as exc:
            _cb.log.warning("[CLIPBOARD] wl-copy failed (%s) — falling back to pyperclip", exc)
    _cb.pyperclip.copy(text)


def _linux_paste() -> str:
    """Read clipboard text on Linux, choosing the right backend.

    Mirrors :func:`_linux_copy` — uses `wl-paste` on Wayland when
    available, otherwise `pyperclip.paste()`.
    """
    if _cb._is_wayland_session() and _cb._have_wl_clipboard():
        try:
            return _cb._linux_wayland_paste()
        except Exception as exc:
            _cb.log.warning("[CLIPBOARD] wl-paste failed (%s) — falling back to pyperclip", exc)
    return _cb.pyperclip.paste()


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
]
