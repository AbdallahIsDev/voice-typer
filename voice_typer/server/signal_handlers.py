"""POSIX signal + Win32 console handlers ( extraction).

Extracted out of :mod:`voice_typer.server.shutdown_controller` so the
shutdown controller can focus on cleanup orchestration. The functions
here install:

* On POSIX — SIGINT / SIGTERM / SIGHUP handlers ( / MED-PPP
) that set an ``Event`` from the signal context (async-
  signal-safe) and defer the unsafe work (logging + spawning the
  ``quit()`` worker thread) to a long-lived watcher daemon.
* On Windows — a ``SetConsoleCtrlHandler`` callback () that
  keeps the tray app alive when the console window closes (Ctrl+Close
  → ``FreeConsole`` + reopen devnull), and triggers ``quit()`` on
  Ctrl+C / logoff / shutdown.

Each function takes the owning :class:`ShutdownController` instance as
its ``controller`` argument so it can read/write the controller's
shared state (``_shutdown_signal_event``, ``_shutdown_signum``,
``_signal_watcher_started``) and invoke ``controller.quit`` /
``controller._win32_console_handler`` exactly as the original method
bodies did. The bodies are preserved verbatim — only the class context
(``self`` → ``controller``) and the method→function signature changed.

:meth:`ShutdownController._install_signal_handlers` /
:meth:`ShutdownController._signal_watcher_loop` /
:meth:`ShutdownController._install_win32_console_handler` /
:meth:`ShutdownController._win32_console_handler` become thin delegates
that call these module functions, preserving the existing instance-
method API used by tests (``controller._install_signal_handlers()``)
and the ``VoiceTyperApp`` wiring (``app.start()`` registers the
atexit handlers, tray callbacks invoke ``quit_app`` which calls
``quit``).
"""

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
    """Install SIGINT/SIGTERM/SIGHUP handlers for graceful shutdown.

    On POSIX there was no signal handler, so Ctrl+C
        would kill the process without running quit() cleanup
        (stop hotkeys, restore volume, release mutex). This method
        installs handlers that trigger quit() on a separate thread
        to avoid deadlock when the main thread is inside the signal
        handler.

        MED-PPP / XCUT-4: the handler body itself is now
        async-signal-safe — it only calls ``Event.set()`` (a thin
        wrapper around ``PyThread_acquire_lock(NOWAIT_LOCK)`` which
        is reentrant and never blocks). A long-lived watcher thread
        (started lazily here) wakes on the event and performs the
        unsafe work (logging + ``threading.Thread(target=quit)``)
        outside the signal context. This eliminates the deadlock
        risk if the signal fires while the main thread holds the
        logging lock.
    """

    # Start the watcher daemon once. ``_signal_watcher_started`` is
    # set BEFORE the thread is created so a signal arriving during
    # ``Thread.__init__`` (which acquires the import lock) won't
    # race us into starting a second watcher.
    if not controller._signal_watcher_started:
        controller._signal_watcher_started = True
        threading.Thread(
            target=signal_watcher_loop,
            args=(controller,),
            name="shutdown-signal-watcher",
            daemon=True,
        ).start()

    def _signal_handler(signum, frame):
        # Async-signal-safe: only record the signum and set the
        # event. ``Event.set()`` is a thin wrapper around a
        # non-blocking lock acquire in CPython and is safe to
        # call from a signal handler. ``int`` assignment is
        # atomic under the GIL. Everything else (logging,
        # thread creation) is deferred to ``signal_watcher_loop``
        # which runs in a normal thread context.
        controller._shutdown_signum = signum
        controller._shutdown_signal_event.set()

    # also register SIGHUP on POSIX so terminal close
    # / SSH disconnect triggers graceful shutdown (default action
    # would terminate immediately without running atexit). Filtered
    # via ``hasattr`` because Windows doesn't define SIGHUP. The
    # ``contextlib.suppress`` already handles the case where the
    # signal can't be installed (e.g. not in the main thread).
    _posix_signals = [signal.SIGINT, signal.SIGTERM]
    _sighup = getattr(signal, "SIGHUP", None)
    if _sighup is not None:
        _posix_signals.append(_sighup)
    for sig in _posix_signals:
        with contextlib.suppress(OSError, ValueError):
            # SIGTERM not available on Windows; signal.signal can
            # raise if not in the main thread
            signal.signal(sig, _signal_handler)


def signal_watcher_loop(controller: ShutdownController) -> None:
    """Watcher thread for the POSIX signal handlers.

        MED-PPP / XCUT-4: polls ``_shutdown_signal_event`` (1s
        timeout) and, when set, performs the unsafe work that the
        signal handler itself must not do — logging the signal name
        and spawning the quit() worker thread. Runs as a daemon so
        it never blocks process exit; ``quit()`` is idempotent so a
        duplicate signal that re-triggers the watcher is harmless.

    the watcher body is wrapped in ``while True:`` so
        the watcher SURVIVES multiple signals. Pre-fix, the watcher
        exited after the first signal — a second SIGTERM (e.g. user
        double-tapping Ctrl+C because the first one was slow to take
        effect) would fall through to Python's default handler
        (immediate termination with no cleanup). The event is
        cleared after each wakeup so a subsequent signal re-arms the
        watcher for the next dispatch.

    Escalation on the SECOND signal. The first signal spawns
        a ``quit()`` worker (graceful shutdown). If a second signal
        arrives (``controller._signal_count >= 2``), the watcher
        logs "second signal received — forcing immediate exit" and
        calls ``os._exit(1)`` directly, bypassing ``quit()``. This
        matches the Python default behaviour the user expects when
        double-tapping Ctrl+C because the first one was slow. The
        ``os._exit`` call is guarded with ``contextlib.suppress(
        Exception)`` so a logging failure doesn't prevent exit. The
        watcher still runs the first-signal path (spawn ``quit()``)
        for robustness — if ``os._exit`` is somehow patched out
        (e.g. in tests), the graceful path still runs.
    """
    # outer ``while True:`` keeps the watcher alive across
    # multiple signal deliveries. ``quit()`` is idempotent (guarded by
    # ``_shutting_down``) so re-dispatching on a duplicate signal is a
    # no-op; the loop is purely defensive against the case where the
    # first quit() worker hasn't yet flipped ``_shutting_down`` and a
    # second signal arrives.
    while True:
        # block indefinitely — ``Event.set()`` from the signal
        # handler wakes the watcher immediately, and on POSIX CPython's
        # interpreter shutdown releases the underlying pthread condvar
        # lock so the daemon thread never blocks process exit. The
        # previous 1s poll loop caused 60 kernel wakeups/min for the
        # entire app lifetime (preventing deep C-states on battery).
        controller._shutdown_signal_event.wait()
        # clear the event so a subsequent signal arrival is
        # observable (re-arms the ``wait()`` above for the next round).
        controller._shutdown_signal_event.clear()
        # Escalation check. ``_signal_count`` is incremented
        # in the async-signal-safe handler; here we read it in the
        # normal thread context. If this is the SECOND (or later)
        # signal, force-exit immediately — the user is asking for
        # termination and the first graceful ``quit()`` is taking
        # too long (e.g. ``tray.stop()`` hanging on a frozen GNOME
        # Shell, or ``recorder.stop()`` hung on a wedged WASAPI
        # stream). ``os._exit(1)`` bypasses Python's orderly
        # shutdown — safe here because the first signal's ``quit()``
        # worker has already had a chance to run the critical
        # flushes (history_db, crash_recovery) before the user
        # double-tapped.
        signal_count = getattr(controller, "_signal_count", 0)
        if signal_count >= 2:
            try:
                log.warning(
                    "[SIGNAL] second signal received (count=%d) — forcing immediate exit (os._exit(1))",
                    signal_count,
                )
            except Exception:
                # Same async-signal-safe fallback pattern as the
                # handler (logging lock held, broken handler, etc.). ``os.write(2, ...)``
                # is async-signal-safe and gives the operator one
                # line of evidence. Never let a logging failure
                # prevent the force-exit.
                with contextlib.suppress(OSError):
                    os.write(2, b"[SIGNAL] second signal received - forcing immediate exit\n")
            # ``os._exit(1)`` is the whole point of the escalation.
            # Guard with ``contextlib.suppress(Exception)`` so a
            # final logging failure (or any other exception in the
            # escalation path) doesn't prevent the exit.
            with contextlib.suppress(Exception):
                os._exit(1)
        # Outside the signal context — safe to use logging and threading.
        signum = controller._shutdown_signum
        try:
            sig_name = signal.Signals(signum).name if signum is not None else "UNKNOWN"
            log.info("[SIGNAL] %s received, shutting down gracefully", sig_name)
        except Exception:
            # UE-1-F7: async-signal-safe fallback. ``log.info`` could
            # fail if the logging lock is held by an interrupted thread,
            # if the configured handler raises (e.g. ``FileHandler`` on
            # a closed log file during interpreter shutdown), or if the
            # stderr stream has been replaced with a broken object.
            # ``os.write(2, ...)`` is async-signal-safe per POSIX and
            # gives the operator at least one line of evidence that the
            # signal was delivered when nothing else works. Never let a
            # logging failure here prevent shutdown — the signal was
            # delivered and we must still call quit().
            with contextlib.suppress(OSError):
                os.write(2, b"[SIGNAL] received - logging failed, invoking quit()\n")
        # RACE-016: daemon=True is acceptable because quit() is
        # idempotent and the atexit handler covers critical cleanup.
        try:
            threading.Thread(target=controller.quit, daemon=True).start()
        except Exception:
            # UE-1-F7: same async-signal-safe stderr fallback as above
            # — ``log.exception`` itself could fail under the same
            # conditions. The ``os.write`` here is the last-resort
            # evidence that we tried to spawn the quit() worker.
            with contextlib.suppress(OSError):
                os.write(2, b"[SIGNAL] failed to spawn quit() worker thread\n")


def install_win32_console_handler(controller: ShutdownController) -> None:
    """On Windows, install a console control handler to survive console closure.

    skip when running under ``pythonw.exe`` — there's no
        console attached, so SetConsoleCtrlHandler is a no-op that
        spews "no console" warnings in the log.
    """
    # Import the platform helper at call time so tests that monkeypatch
    # voice_typer.server.platform_utils.is_windows still take effect
    # (mirrors the SettingsController convention).
    from voice_typer.server.platform_utils import is_windows

    if not is_windows():
        return
    app = controller._app

    # Idempotency guard: if a handler was already installed, do NOT
    # create a new ``CFUNCTYPE`` wrapper. Each call constructs a fresh
    # wrapper and (without this guard) would overwrite
    # ``app._console_handler``, dropping the only Python reference to
    # the PREVIOUS wrapper. Windows still holds the raw C function
    # pointer in its console-control handler chain, so once the old
    # wrapper is garbage-collected the next console event (Ctrl+C,
    # logoff, close) calls into freed Python memory → use-after-free /
    # segfault. ``SetConsoleCtrlHandler`` ADDS to the chain rather
    # than replacing, so the leaked pointer is never re-validated.
    # Returning here keeps the original wrapper (and its underlying
    # callable) alive for the lifetime of the process.
    if getattr(app, "_console_handler", None) is not None:
        log.debug("[WIN32] console control handler already installed — skipping re-install")
        return

    # detect pythonw.exe (no console) and skip install.
    exe_name = Path(sys.executable).name.lower()
    if exe_name == "pythonw.exe":
        log.debug("[WIN32] pythonw.exe detected — skipping console control handler")
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
            app._kernel32.FreeConsole()
            # PERF-004: reuse the existing devnull object instead of
            # opening a new one on every ctrl_close_event (would hit
            # Windows' 10,000 handle cap after ~250 RDP logout cycles).
            if getattr(app, "_devnull", None) is None or app._devnull.closed:
                app._devnull = open(os.devnull, "w")  # noqa: SIM115
                # Register the devnull FD for shutdown cleanup directly
                # via the canonical log helper. The previous indirection
                # (``voice_typer.server.app._register_devnull_file``)
                # referenced an attribute that does not exist on the app
                # module — it raised AttributeError inside this Ctrl-Close
                # handler, silently breaking devnull-file cleanup.
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
            "[WIN32] System event %d received, invoking fast cleanup (XZ-R17-06)",
            ctrl_type,
        )
        # route Windows logoff/shutdown to ``_do_fast_cleanup()``
        # (NOT ``controller.quit``). The full ``_do_cleanup`` body has a
        # cumulative worst-case of ~25-85s; Windows gives the process
        # ~5s before force-kill. The fast path runs critical-only cleanup
        # with 1s timeouts each (<3s total) and ends with ``os._exit(0)``
        # to bypass atexit handlers (the OS is killing us anyway, so
        # orderly atexit cleanup would race the OS force-kill and lose).
        # ``_do_fast_cleanup`` is idempotent with ``_do_cleanup`` via the
        # shared ``_cleanup_done`` flag, so a subsequent atexit safety
        # net (if it somehow runs before ``os._exit``) is a no-op.
        #
        # Synchronous call: the Win32 console-control callback runs on a
        # dedicated OS thread and returning True signals "handled". We
        # must finish critical cleanup BEFORE returning so the OS doesn't
        # force-kill us mid-flush. ``_do_fast_cleanup`` itself calls
        # ``os._exit(0)`` at the end, so this ``return True`` only runs
        # if the fast cleanup raised (in which case the OS force-kill is
        # our fallback).
        try:
            controller._do_fast_cleanup()
        except Exception:
            log.exception("[WIN32] _do_fast_cleanup raised; relying on OS force-kill for cleanup")
        return True

    if ctrl_type in (ctrl_c_event, ctrl_break_event):
        log.info("[WIN32] Ctrl+C received, shutting down")
        # RACE-016: daemon=True is acceptable because quit() is
        # idempotent and the atexit handler covers critical cleanup.
        threading.Thread(target=controller.quit, daemon=True).start()
        return True

    return False


# Late import — ``os`` is only referenced inside ``win32_console_handler``
# for ``os.devnull``. Imported at module load to mirror the original
# shutdown_controller.py imports (which imported ``os`` at module top
# for the broader cleanup body).


__all__ = [
    "install_signal_handlers",
    "signal_watcher_loop",
    "install_win32_console_handler",
    "win32_console_handler",
]
