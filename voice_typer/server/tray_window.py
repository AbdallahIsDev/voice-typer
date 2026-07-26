"""Electron window management for the system tray.

#13: Extracted from tray.py to separate concerns:
- Win32 window focus (EnumWindows/AttachThreadInput/SetForegroundWindow)
- Electron app launch (build-first, dev fallback)

DT-FIX-9 / DT-27 (Phase 4.5 spaghetti split): extended with the
remaining window-management + quit-confirmation concerns that were
still inlined on ``TrayIcon``:

  - :func:`open_page` — publish a ``navigate`` event so the renderer
    opens the given route (Settings / History / Help / Models).
  - :func:`open_models_page` — open the Electron window and navigate
    to ``/models``.
  - :func:`confirm_quit_while_recording` — quit immediately via the
    controller (the old confirmation dialog was removed; crash
    recovery + ``quit_app`` handle in-flight transcriptions).

These operations are platform/IPC-specific and independent of the
pystray icon lifecycle, so they belong in their own module. The
``TrayIcon`` class keeps one-line delegate methods for each so tests
that do ``monkeypatch.setattr("voice_typer.server.tray.TrayIcon.X", ...)``
keep working and source-grep tests that scan ``tray.py`` for the
method signatures still pass.
"""

import logging
import os
import subprocess
from typing import TYPE_CHECKING

from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_windows

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray_window")

# PROD-003: Track the PID of the Electron subprocess we launched
# so quit() can terminate it explicitly as a safety net.
_electron_pid: int | None = None


def set_electron_pid(pid: int) -> None:
    """Store the PID of the Electron subprocess for cleanup on shutdown."""
    global _electron_pid
    _electron_pid = pid


def get_electron_pid() -> int | None:
    """Return the PID of the Electron subprocess, if tracked."""
    return _electron_pid


def bring_electron_to_front() -> bool:
    """Find an existing Voice Typer Electron window and bring it to front.

    Returns True if a window was found and focused, False otherwise.
    Uses Win32 EnumWindows to search by window title.

    Extracted from TrayIcon._bring_electron_to_front() per #13.
    """
    if not is_windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        found_hwnd = None

        def _enum_cb(hwnd, _):
            nonlocal found_hwnd
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value
            if title and APP_NAME in title:
                found_hwnd = hwnd
                return False
            return True

        wndenumproc = ctypes.CFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        ctypes.windll.user32.EnumWindows(wndenumproc(_enum_cb), 0)

        if found_hwnd is None:
            return False

        # Restore from minimized, OR reveal if hidden via close-to-tray.
        # SW_SHOW (5) makes a hidden window visible without activating;
        # SW_RESTORE (9) both restores a minimized window and shows it.
        # We handle both states so the tray "Open app" works whether the
        # window was minimized normally or hidden to tray.
        if ctypes.windll.user32.IsIconic(found_hwnd):
            ctypes.windll.user32.ShowWindow(found_hwnd, 9)  # SW_RESTORE
        elif not ctypes.windll.user32.IsWindowVisible(found_hwnd):
            ctypes.windll.user32.ShowWindow(found_hwnd, 5)  # SW_SHOW

        our_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        target_tid = ctypes.windll.user32.GetWindowThreadProcessId(found_hwnd, None)
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()

        if target_tid != our_tid:
            ctypes.windll.user32.AttachThreadInput(our_tid, target_tid, True)
        if fg_hwnd:
            fg_tid = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None)
            if fg_tid and fg_tid != target_tid and fg_tid != our_tid:
                ctypes.windll.user32.AttachThreadInput(our_tid, fg_tid, False)

        ctypes.windll.user32.BringWindowToTop(found_hwnd)
        ctypes.windll.user32.SetForegroundWindow(found_hwnd)
        ctypes.windll.user32.SetActiveWindow(found_hwnd)

        if target_tid != our_tid:
            ctypes.windll.user32.AttachThreadInput(our_tid, target_tid, False)

        log.info("[TRAY] Electron window brought to front")
        return True
    except Exception as exc:
        log.warning("[TRAY] Failed to bring Electron window to front: %s", exc)
        return False


def open_electron_window() -> None:
    """Open (or focus) the Electron dashboard window.

    Primary path (1 hop): push ``show_window`` over the TCP channel that
    is always up between us (the backend) and our parent Electron
    process.  Electron's ``showMainWindow()`` then shows + focuses the
    dashboard (creating it lazily if autostart started it hidden).

    Fallback: if the push doesn't land (TCP momentarily down, or this
    backend was started standalone without Electron), use the Win32
    ``bring_electron_to_front`` focus path, then finally launch
    Electron dev mode as a last resort.

    Extracted from TrayIcon.open_electron_window() per #13.
    """
    # 1. Primary: push show_window over TCP.  Cheap, cross-platform,
    #    and works whether the window is hidden (close-to-tray) or
    #    minimized.
    try:
        from voice_typer.server import event_bus

        if event_bus.publish({"type": "show_window"}):
            log.info("[TRAY] show_window pushed to Electron")
            return
    except Exception:
        log.debug("[TRAY] show_window push failed, trying Win32 focus")

    # 2. Fallback: Win32 EnumWindows focus on an existing window.
    if bring_electron_to_front():
        return

    # 3. Last resort: Electron isn't running — build + launch with
    #    electron . (production path, no Vite).
    from voice_typer.server.autostart_launcher import _ensure_built_and_launch

    if _ensure_built_and_launch(hidden=False):
        log.info("[TRAY] Electron app launched (build-first)")
        return
    # If build-first also failed, try dev mode as absolute last resort.
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        client_dir = os.path.join(project_root, "voice_typer", "client")
        log.info("[TRAY] Build-first failed, trying dev mode from %s", client_dir)
        # NEW-XPLAT-003 / S-7: previously used ``shell=True`` here (which
        # spawns a shell to find npm, propagating PATH/env to it — a
        # shell-injection risk and breaks on paths with spaces).  We now
        # resolve the npm path explicitly via the shared
        # :func:`_electron_build._npm_command` helper, which uses
        # ``shutil.which`` (and on Windows checks ``PATHEXT`` so ``npm``
        # resolves to ``npm.cmd``).  When npm truly cannot be resolved,
        # we log and skip — never fall back to ``shell=True``.
        from voice_typer.server._electron_build import _npm_command

        cmd = _npm_command("dev")
        if cmd is None:
            log.error("[TRAY] npm not on PATH; cannot launch dev mode. Install Node.js / npm or add it to PATH.")
            return
        proc = subprocess.Popen(cmd, cwd=client_dir)
        # PROD-003: track PID for cleanup on shutdown
        set_electron_pid(proc.pid)
        log.info("[TRAY] Electron app launched (dev mode fallback)")
    except Exception as e:
        log.error("[TRAY] Failed to launch Electron app: %s", e)


def open_page(path: str) -> None:
    """Publish a ``navigate`` event so the renderer opens ``path``.

    UX-33 (FIX-10): generalization of :func:`open_models_page` so any
    in-app route can be opened from the tray menu (Settings / History /
    Help). Does NOT open the Electron window itself — callers that need
    the window open (e.g. :func:`open_models_page`) call
    :func:`open_electron_window` first, then :func:`open_page`.

    DT-FIX-9 / DT-27: extracted from ``TrayIcon._open_page`` as a
    pure module-level function (no instance state needed — just
    publishes via the event bus).

    Args:
        path: The renderer route to navigate to (e.g. ``/settings``).
    """
    from voice_typer.server import event_bus

    try:
        event_bus.publish({"type": "navigate", "data": {"path": path}})
        log.info("[TRAY] Navigate push sent: %s", path)
    except Exception as e:
        log.warning("[TRAY] Failed to push navigate event for %s: %s", path, e)


def open_models_page(tray: "TrayIcon") -> None:
    """Open the Electron window and navigate to the Models page.

    Called from the tray menu's "More models..." item. Opens/focuses
    the Electron window (same as :func:`open_electron_window`) and then
    delegates to :func:`open_page` with ``'/models'`` so the renderer
    navigates to the Models page instead of staying on whatever page
    was last open.

    DT-FIX-9 / DT-27: extracted from ``TrayIcon._open_models_page``.
    The delegate on ``TrayIcon`` calls ``tray._open_page('/models')``
    (NOT this module's :func:`open_page` directly) so tests that do
    ``monkeypatch.setattr(tray, "_open_page", fake_open_page)`` keep
    working — the patched instance attribute is consulted at call
    time, not the module-level function.

    Args:
        tray: The ``TrayIcon`` instance (used to access the
            ``_open_page`` delegate).
    """
    open_electron_window()
    tray._open_page("/models")


def confirm_quit_while_recording(tray: "TrayIcon") -> None:
    """Quit immediately, regardless of recording state.

    The old confirmation dialog was removed because crash recovery
    already protects in-flight transcriptions, and ``quit_app()``
    handles discarding active recordings and waiting for transcription
    to finish (with timeout).

    DT-FIX-9 / DT-27: extracted from
    ``TrayIcon._confirm_quit_while_recording``. The method is a thin
    delegate to ``tray._controller.quit_app()``; kept as a separate
    function so the ``TrayIcon`` class is a one-line delegate and the
    quit policy lives with the rest of the window-management code.

    Args:
        tray: The ``TrayIcon`` instance (used to access
            ``tray._controller``).
    """
    tray._controller.quit_app()
