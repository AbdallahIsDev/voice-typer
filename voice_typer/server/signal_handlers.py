"""Process signal handlers for clean shutdown."""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.shutdown_controller import ShutdownController

log = logging.getLogger(__name__)


def install_signal_handlers(controller: ShutdownController) -> None:
    """Install SIGINT/SIGTERM/SIGHUP handlers for graceful shutdown."""

    # Start the watcher daemon once. ``_signal_watcher_started`` is
    if not controller._signal_watcher_started:
        controller._signal_watcher_started = True
        threading.Thread(
            target=signal_watcher_loop,
            args=(controller,),
            name="shutdown-signal-watcher",
            daemon=True,
        ).start()

    def _signal_handler(signum, frame):
        # Async-signal-safe: only record the signum, count the
        controller._shutdown_signum = signum
        # Delivery counter for the watcher's second-signal
        controller._signal_count = getattr(controller, "_signal_count", 0) + 1
        controller._shutdown_signal_event.set()

    # also register SIGHUP on POSIX so terminal close
    _posix_signals = [signal.SIGINT, signal.SIGTERM]
    _sighup = getattr(signal, "SIGHUP", None)
    if _sighup is not None:
        _posix_signals.append(_sighup)
    for sig in _posix_signals:
        with contextlib.suppress(OSError, ValueError):
            # SIGTERM not available on Windows; signal.signal can
            signal.signal(sig, _signal_handler)


def signal_watcher_loop(controller: ShutdownController) -> None:
    """Watcher thread for the POSIX signal handlers."""
    # outer ``while True:`` keeps the watcher alive across
    while True:
        # block indefinitely, ``Event.set()`` from the signal
        controller._shutdown_signal_event.wait()
        # clear the event so a subsequent signal arrival is
        controller._shutdown_signal_event.clear()
        # Escalation check. ``_signal_count`` is incremented
        signal_count = getattr(controller, "_signal_count", 0)
        if signal_count >= 2:
            try:
                log.warning(
                    "[SIGNAL] second signal received (count=%d), forcing immediate exit (os._exit(1))",
                    signal_count,
                )
            except Exception:
                # Same async-signal-safe fallback pattern as the
                with contextlib.suppress(OSError):
                    os.write(2, b"[SIGNAL] second signal received - forcing immediate exit\n")
            # ``os._exit(1)`` is the whole point of the escalation.
            with contextlib.suppress(Exception):
                os._exit(1)
        # Outside the signal context, safe to use logging and threading.
        signum = controller._shutdown_signum
        try:
            sig_name = signal.Signals(signum).name if signum is not None else "UNKNOWN"
            log.info("[SIGNAL] %s received, shutting down gracefully", sig_name)
        except Exception:
            # Async-signal-safe fallback. ``log.info`` could
            with contextlib.suppress(OSError):
                os.write(2, b"[SIGNAL] received - logging failed, invoking quit()\n")
        # RACE-016: daemon=True is acceptable because quit() is
        try:
            threading.Thread(target=controller.quit, daemon=True).start()
        except Exception:
            # same async-signal-safe stderr fallback as above
            with contextlib.suppress(OSError):
                os.write(2, b"[SIGNAL] failed to spawn quit() worker thread\n")


def install_win32_console_handler(controller: ShutdownController) -> None:
    """On Windows, install a console control handler to survive console closure."""
    # Import the platform helper at call time so tests that monkeypatch
    from voice_typer.server.platform_utils import is_windows

    if not is_windows():
        return
    app = controller._app

    # Idempotency guard: if a handler was already installed, do NOT
    if getattr(app, "_console_handler", None) is not None:
        log.debug("[WIN32] console control handler already installed, skipping re-install")
        return

    # detect pythonw.exe (no console) and skip install.
    exe_name = Path(sys.executable).name.lower()
    if exe_name == "pythonw.exe":
        log.debug("[WIN32] pythonw.exe detected, skipping console control handler")
        return

    try:
        import ctypes
        from ctypes import wintypes

        handler_routine = ctypes.CFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        app._console_handler = handler_routine(lambda ctrl_type: win32_console_handler(controller, ctrl_type))
        app._kernel32 = ctypes.windll.kernel32
        kernel32 = app._kernel32
        kernel32.SetConsoleCtrlHandler.argtypes = [handler_routine, wintypes.BOOL]
        kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
        kernel32.FreeConsole.argtypes = []
        kernel32.FreeConsole.restype = wintypes.BOOL

        result = kernel32.SetConsoleCtrlHandler(app._console_handler, True)
        if result:
            log.info("[WIN32] Console control handler installed")
        else:
            log.warning("[WIN32] SetConsoleCtrlHandler failed")
    except Exception:
        log.exception("[WIN32] Failed to install console control handler")


def win32_console_handler(controller: ShutdownController, ctrl_type) -> bool:
    """Callback for Windows console control events."""
    app = controller._app
    ctrl_c_event = 0
    ctrl_break_event = 1
    ctrl_close_event = 2
    ctrl_logoff_event = 5
    ctrl_shutdown_event = 6

    if ctrl_type == ctrl_close_event:
        log.info("[WIN32] Console window closing -- keeping process alive (tray app survives)")
        try:
            # FreeConsole resets the process handler table to its initial
            app._kernel32.FreeConsole()
            # PERF-004: reuse the existing devnull object instead of
            if getattr(app, "_devnull", None) is None or app._devnull.closed:
                # app._devnull = open(os.devnull, "w")  # noqa: SIM115
                from voice_typer.server.log import register_devnull_file

                register_devnull_file(app._devnull)
            sys.stdout = app._devnull
            sys.stderr = app._devnull
            log.info("[WIN32] Detached from console (FreeConsole)")
        except Exception:
            log.warning("[WIN32] FreeConsole() failed")
        return True

    if ctrl_type in (ctrl_logoff_event, ctrl_shutdown_event):
        log.info(
            "[WIN32] System event %d received, invoking fast cleanup",
            ctrl_type,
        )
        # route Windows logoff/shutdown to ``_do_fast_cleanup()``
        try:
            controller._do_fast_cleanup()
        except Exception:
            log.exception("[WIN32] _do_fast_cleanup raised; relying on OS force-kill for cleanup")
        return True

    if ctrl_type in (ctrl_c_event, ctrl_break_event):
        log.info("[WIN32] Ctrl+C received, shutting down")
        # RACE-016: daemon=True is acceptable because quit() is
        threading.Thread(target=controller.quit, daemon=True).start()
        return True

    return False


__all__ = [
    "install_signal_handlers",
    "signal_watcher_loop",
    "install_win32_console_handler",
    "win32_console_handler",
]
