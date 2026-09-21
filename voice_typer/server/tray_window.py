"""Tray window helpers."""

import logging
import subprocess
from typing import TYPE_CHECKING

from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_windows

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray_window")

# Track the PID of the host process we launched
_host_pid: int | None = None


def set_host_pid(pid: int) -> None:
    """Store the PID of the host process for cleanup on shutdown."""
    global _host_pid
    _host_pid = pid


def get_host_pid() -> int | None:
    """Return the PID of the host process, if tracked."""
    return _host_pid


def _host_process_is_running() -> bool:
    """Return True if a Voice Typer host process appears to be alive."""
    from voice_typer.server.backend_pid import _is_pid_alive

    pid = _host_pid
    if pid is not None and pid > 0 and _is_pid_alive(pid):
        return True
    if not is_windows():
        # pgrep -f matches the full command line, so it finds the
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


def _bring_app_to_front_macos() -> bool:
    """Bring the Voice Typer window to front on macOS via AppleScript."""
    if is_windows():
        return False
    try:
        completed = subprocess.run(
            ["osascript", "-e", f'tell application "{APP_NAME}" to activate'],
            capture_output=True,
            timeout=5.0,
        )
        if completed.returncode == 0:
            log.info("[TRAY] App window activated via AppleScript")
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


def _bring_app_to_front_linux() -> bool:
    """Bring the Voice Typer window to front on Linux via wmctrl/xdotool."""
    if is_windows():
        return False
    for tool_cmd in (
        ["wmctrl", "-a", APP_NAME],
        ["xdotool", "search", "--name", APP_NAME, "windowactivate"],
    ):
        try:
            completed = subprocess.run(tool_cmd, capture_output=True, timeout=5.0)
            if completed.returncode == 0:
                log.info("[TRAY] App window activated via %s", tool_cmd[0])
                return True
        except Exception as exc:
            log.debug("[TRAY] %s failed: %s", tool_cmd[0], exc)
    return False


def bring_app_to_front() -> bool:
    """Find an existing Voice Typer window and bring it to front.

    Returns True if a window was found and focused, False otherwise.
    """
    if not is_windows():
        # macOS / Linux focus paths.
        return _bring_app_to_front_macos() or _bring_app_to_front_linux()
    try:
        import ctypes
        from ctypes import wintypes

        # Winlogon / UAC secure desktop: GetForegroundWindow returns 0
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not fg_hwnd:
            log.info("[TRAY] No foreground window (secure desktop / Winlogon active), skipping bring-to-front")
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

        log.info("[TRAY] App window brought to front")
        return True
    except Exception as exc:
        log.warning("[TRAY] Failed to bring app window to front: %s", exc)
        return False


def open_app_window() -> None:
    """Open (or focus) the dashboard window."""
    # 1. Primary: push show_window over TCP.  Cheap, cross-platform,
    import time as _time

    from voice_typer.server import event_bus
    from voice_typer.server.duration import format_duration

    _open_started = _time.perf_counter()
    try:
        published = event_bus.publish({"type": "show_window"})
    except Exception:
        published = False
        log.debug("[TRAY] show_window push raised, trying Win32 focus")

    # ``event_bus.publish`` returns True when ANY in-process subscriber
    live = event_bus.has_live_transport()
    delivered = published and live
    if delivered:
        log.info(
            "[TRAY] Tray icon left-click: show_window request sent to host "
            "over IPC, the host will show, raise and focus the dashboard window"
        )
    else:
        log.info("[TRAY] no live host transport, trying Win32 focus")

    # 2. Native focus: insurance on Windows (see the native-focus note above),
    focused = False
    if is_windows() or not delivered:
        try:
            focused = bring_app_to_front()
        except Exception:
            log.debug("[TRAY] bring_app_to_front raised", exc_info=True)
            focused = False
    log.info(
        "[TRAY] open window request settled live=%s focused=%s%s",
        live,
        focused,
        format_duration(_time.perf_counter() - _open_started),
    )
    if delivered:
        return
    if focused:
        return

    # 3. Duplicate-launch gate: if the focus helpers above failed
    if _host_process_is_running():
        log.warning("[TRAY] App appears to be running but window focus failed, skipping duplicate launch")
        return

    # The host manages its own window lifecycle; this backend only
    log.info("[TRAY] No live transport and focus failed; cannot launch a frontend from the backend")


def open_page(path: str) -> None:
    """Publish a ``navigate`` event so the renderer opens ``path``."""
    from voice_typer.server import event_bus

    try:
        event_bus.publish({"type": "navigate", "data": {"path": path}})
        log.info("[TRAY] Navigate push sent: %s", path)
    except Exception as e:
        log.warning("[TRAY] Failed to push navigate event for %s: %s", path, e)


def open_models_page(tray: "TrayIcon") -> None:
    """Open the app window and navigate to the Models page."""
    open_app_window()
    tray._open_page("/models")


def confirm_quit_while_recording(tray: "TrayIcon") -> None:
    """Quit immediately, regardless of recording state."""
    tray._controller.quit_app()
