"""Electron window management for the system tray.

#13: Extracted from tray.py to separate concerns:
- Win32 window focus (EnumWindows/AttachThreadInput/SetForegroundWindow)
- Electron app launch (build-first, dev fallback)

(Phase 4.5 spaghetti split): extended with the
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

# Track the PID of the Electron subprocess we launched
# so quit() can terminate it explicitly as a safety net.
_electron_pid: int | None = None


def set_electron_pid(pid: int) -> None:
    """Store the PID of the Electron subprocess for cleanup on shutdown."""
    global _electron_pid
    _electron_pid = pid


def get_electron_pid() -> int | None:
    """Return the PID of the Electron subprocess, if tracked."""
    return _electron_pid


def _electron_process_is_running() -> bool:
    """Return True if a Voice Typer Electron process appears to be alive.

    Checks in order:
    1. The tracked ``_electron_pid`` (set when *this* backend launched
       Electron) — via the cross-platform ``_is_pid_alive`` helper.
    2. A ``pgrep -f <APP_NAME>`` process-table match (macOS/Linux) —
       catches an Electron launched by another backend instance or a
       manual start.

    Used by :func:`open_electron_window` to avoid spawning a DUPLICATE
    Electron process when the window-focus fallback fails (EO-16).
    """
    from voice_typer.server.single_instance import _is_pid_alive

    pid = _electron_pid
    if pid is not None and pid > 0 and _is_pid_alive(pid):
        return True
    if not is_windows():
        # pgrep -f matches the full command line, so it finds the
        # Electron process regardless of which backend spawned it.
        try:
            completed = subprocess.run(
                ["pgrep", "-f", APP_NAME],
                capture_output=True,
                timeout=5.0,
            )
            if completed.returncode == 0:
                return True
        except Exception as exc:
            log.debug("[TRAY] pgrep check failed: %s", exc)
    return False


def _bring_electron_to_front_macos() -> bool:
    """Bring the Voice Typer window to front on macOS via AppleScript.

    ``tell application "<name>" to activate`` asks the running app to
    activate (the Electron app registers its bundle name with
    LaunchServices, so this resolves to the running instance). Returns
    True if the AppleScript succeeded.

    EO-16: previously the macOS/Linux paths had NO focus helper at all
    — ``bring_electron_to_front`` returned False outside Windows, so a
    transient TCP blip fell straight through to spawning a DUPLICATE
    Electron process.
    """
    if is_windows():
        return False
    try:
        completed = subprocess.run(
            ["osascript", "-e", f'tell application "{APP_NAME}" to activate'],
            capture_output=True,
            timeout=5.0,
        )
        if completed.returncode == 0:
            log.info("[TRAY] Electron window activated via AppleScript")
            return True
        log.debug(
            "[TRAY] osascript activate failed (rc=%s): %s",
            completed.returncode,
            completed.stderr.decode("utf-8", errors="replace").strip() if completed.stderr else "",
        )
        return False
    except Exception as exc:
        log.debug("[TRAY] osascript activate failed: %s", exc)
        return False


def _bring_electron_to_front_linux() -> bool:
    """Bring the Voice Typer window to front on Linux via wmctrl/xdotool.

    Tries ``wmctrl -a <name>`` first (X11 window manager control;
    matches windows whose title contains the app name), then falls back
    to ``xdotool search --name <name> windowactivate`` (also works
    under Wayland with XWayland). Returns True if either succeeded.

    EO-16: previously the macOS/Linux paths had NO focus helper at all.
    """
    if is_windows():
        return False
    for tool_cmd in (
        ["wmctrl", "-a", APP_NAME],
        ["xdotool", "search", "--name", APP_NAME, "windowactivate"],
    ):
        try:
            completed = subprocess.run(tool_cmd, capture_output=True, timeout=5.0)
            if completed.returncode == 0:
                log.info("[TRAY] Electron window activated via %s", tool_cmd[0])
                return True
        except Exception as exc:
            log.debug("[TRAY] %s failed: %s", tool_cmd[0], exc)
    return False


def bring_electron_to_front() -> bool:
    """Find an existing Voice Typer Electron window and bring it to front.

    Returns True if a window was found and focused, False otherwise.

    - Windows: Win32 EnumWindows search by window title.
    - macOS: AppleScript ``activate`` on the running app.
    - Linux: ``wmctrl -a`` / ``xdotool ... windowactivate``.

    Extracted from TrayIcon._bring_electron_to_front() per #13;
    extended per EO-16 with the macOS/Linux focus helpers that were
    previously missing.
    """
    if not is_windows():
        # macOS / Linux focus paths.
        return _bring_electron_to_front_macos() or _bring_electron_to_front_linux()
    try:
        import ctypes
        from ctypes import wintypes

        # Winlogon / UAC secure desktop: GetForegroundWindow returns 0
        # (NULL). Foreground manipulation is blocked there, so skip the
        # window enumeration + focus dance entirely and report that no
        # window was brought to front.
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not fg_hwnd:
            log.info(
                "[TRAY] No foreground window (secure desktop / Winlogon active) — skipping bring-to-front"
            )
            return False

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
    from voice_typer.server import event_bus

    try:
        published = event_bus.publish({"type": "show_window"})
    except Exception:
        published = False
        log.debug("[TRAY] show_window push raised, trying Win32 focus")

    # ``event_bus.publish`` returns True when ANY in-process subscriber
    # accepted the event — which does NOT prove Electron received it:
    # the IPC transport's push() swallows write failures (it buffers to
    # ``_pending_tcp`` and marks the client dead instead of raising) and
    # the no-client path buffers silently, while unrelated subscribers
    # (e.g. the tray's parakeet-cpu-fallback listener) accept every
    # event. Only treat the push as delivered when a transport probe
    # reports a live host client; otherwise fall through to the Win32
    # focus path so the window still appears.
    if published and event_bus.has_live_transport():
        log.info("[TRAY] show_window pushed to Electron")
        return
    log.info("[TRAY] no live Electron transport — trying Win32 focus")

    # 2. Fallback: platform focus on an existing window.
    if bring_electron_to_front():
        return

    # 3. EO-16 duplicate-launch gate: if the focus helpers above failed
    #    but we KNOW an Electron process is still alive (tracked PID, or
    #    a pgrep match), do NOT spawn a second Electron — the existing
    #    window simply couldn't be focused (e.g. the window manager
    #    refused, or the window is on another desktop). Spawning a
    #    duplicate would surface a confusing "port already in use" crash
    #    or a second window. Previously this gate only existed on
    #    Windows (where bring_electron_to_front actually worked); on
    #    macOS/Linux a transient TCP blip fell straight through to a
    #    duplicate launch.
    if _electron_process_is_running():
        log.warning(
            "[TRAY] Electron appears to be running but window focus failed — "
            "skipping duplicate launch (EO-16)"
        )
        return

    # 4. Last resort: Electron isn't running — build + launch with
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
        # S-7: previously used ``shell=True`` here (which
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
        # track PID for cleanup on shutdown
        set_electron_pid(proc.pid)
        log.info("[TRAY] Electron app launched (dev mode fallback)")
    except Exception as e:
        log.error("[TRAY] Failed to launch Electron app: %s", e)


def open_page(path: str) -> None:
    """Publish a ``navigate`` event so the renderer opens ``path``.

    (): generalization of :func:`open_models_page` so any
        in-app route can be opened from the tray menu (Settings / History /
        Help). Does NOT open the Electron window itself — callers that need
        the window open (e.g. :func:`open_models_page`) call
        :func:`open_electron_window` first, then :func:`open_page`.

    extracted from ``TrayIcon._open_page`` as a
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

    extracted from ``TrayIcon._open_models_page``.
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

    extracted from
        ``TrayIcon._confirm_quit_while_recording``. The method is a thin
        delegate to ``tray._controller.quit_app()``; kept as a separate
        function so the ``TrayIcon`` class is a one-line delegate and the
        quit policy lives with the rest of the window-management code.

        Args:
            tray: The ``TrayIcon`` instance (used to access
                ``tray._controller``).
    """
    tray._controller.quit_app()
