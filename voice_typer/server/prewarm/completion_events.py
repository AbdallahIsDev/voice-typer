# ARCH-045 / SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""CPU-04: event-based prewarm completion notification.

Phase 4.5 / ARCH-045 — this module holds the *signal* side (prewarm
process) and the *wait* side (the app) of the event-based completion
notification that replaced the old 500ms poll loop in
:func:`wait_for_prewarm`.

- :func:`_completion_event_name` — PID-scoped name for the prewarm
  completion event.
- :func:`_create_completion_event` — prewarm creates a PID-scoped named
  ``CreateEventW`` (manual-reset).
- :func:`_signal_completion_event` — prewarm signals the event on
  completion.
- :func:`_close_completion_event` — prewarm closes the event handle.
- :func:`_wait_for_completion_event` — the app waits on the event
  (zero-CPU on Windows, fd-based on Linux).
- :func:`_wait_completion_windows` — Windows-specific wait helper.
- :func:`_wait_completion_linux` — Linux-specific pidfd+poll helper.

Patch-path compatibility
------------------------
``_wait_for_completion_event`` is patched on the package namespace by
tests, and :func:`wait_for_prewarm` (in :mod:`.process_tracker`) looks
it up via ``_pkg._wait_for_completion_event()`` so the patch takes
effect.  ``_read_prewarm_pid`` (in :mod:`.process_tracker`) is also
looked up via ``_pkg._read_prewarm_pid()`` for consistency.
"""

from __future__ import annotations

import contextlib
import logging
import os
import select

# Patch-path bridge: route lookups of cross-submodule helpers through
# the package namespace.  ``_read_prewarm_pid`` lives in
# :mod:`.process_tracker`; we look it up via ``_pkg._read_prewarm_pid()``
# at call time so the package namespace is the single source of truth.
from voice_typer.server import prewarm as _pkg
from voice_typer.server.platform_utils import is_linux, is_windows

log = logging.getLogger("voice_typer.server.prewarm")


# ── CPU-04: Event-based prewarm completion notification ─────────────────────────────────────────────────────────
#
# wait_for_prewarm() previously polled is_prewarm_running() every
# 500ms (120 polls over 60s), each poll reading the PID file and calling
# _process_alive(). This was wasteful even with the small per-call cost
# (~5ms on Windows).
#
# CPU-04 replaces the poll loop with true event-based waiting:
#   - Windows: prewarm creates a PID-scoped named CreateEventW
#     (manual-reset). The app opens the event for the *current* prewarm
#     PID and calls WaitForSingleObject with a timeout — a zero-CPU
#     kernel wait that returns immediately when prewarm signals
#     completion. The event name is scoped by PID (not a single global
#     name) so a stale signal from a previous boot can't make a later
#     launch skip waiting.
#   - Linux: the app uses os.pidfd_open(pid) (Linux 5.3+, Python 3.9+)
#     to get a file descriptor that becomes readable when the process
#     exits, then uses select.poll() to wait on it with timeout.
#   - Fallback (macOS, old kernels, or any error): _wait_for_completion_event
#     returns False and wait_for_prewarm() degrades to the 1s poll loop
#     (60 polls max instead of 120).
#
# The *signal* side (prewarm process) is implemented by
# _create_completion_event / _signal_completion_event /
# _close_completion_event. The *wait* side (the app) is implemented by
# _wait_for_completion_event and its per-OS helpers below.


def _completion_event_name(pid: int) -> str:
    """PID-scoped name for the prewarm completion event.

    Scoping by PID avoids cross-run contamination: a manual-reset event
    stays signaled until explicitly reset, so a single global name could
    let a later launch observe a stale "done" signal from a previous boot.
    """
    return f"Local\\VoiceTyperPrewarmCompletion_{pid}"


def _create_completion_event(pid: int) -> int | None:
    if is_windows():
        import ctypes
        from ctypes import wintypes

        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateEventW.restype = wintypes.HANDLE
            kernel32.CreateEventW.argtypes = [
                ctypes.c_void_p,
                wintypes.BOOL,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            handle = kernel32.CreateEventW(None, True, False, _completion_event_name(pid))
            return handle if handle else None
        except Exception:
            return None
    return None


def _signal_completion_event(handle: int | None) -> None:
    if handle is not None and is_windows():
        try:
            import ctypes

            ctypes.windll.kernel32.SetEvent(handle)
        except Exception:
            pass


def _close_completion_event(handle: int | None) -> None:
    if handle is not None and is_windows():
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


def _wait_for_completion_event(timeout_s: float) -> bool:
    """CPU-04: block (near-zero CPU) until prewarm signals completion.

    Returns True if completion was observed within ``timeout_s``; False if
    the platform/OS version doesn't support event-based waiting, the PID
    file vanished in the brief window before we could attach, or the wait
    timed out. A False return lets ``wait_for_prewarm()`` fall back to the
    degraded 1s poll loop.

      - Windows: open the PID-scoped named event and WaitForSingleObject
        (a kernel-side wait — no CPU spin).
      - Linux: pidfd_open(pid) + select.poll() on the fd (readable when the
        process exits). Requires Linux 5.3+ / Python 3.9+.
      - Other platforms: return False (poll fallback).
    """
    pid = _pkg._read_prewarm_pid()
    if pid is None:
        return False
    if is_windows():
        return _wait_completion_windows(pid, timeout_s)
    if is_linux():
        return _wait_completion_linux(pid, timeout_s)
    return False


def _wait_completion_windows(pid: int, timeout_s: float) -> bool:
    """Open the PID-scoped completion event and wait on it (zero-CPU)."""
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        SYNCHRONIZE = 0x00100000  # noqa: N806
        WAIT_OBJECT_0 = 0  # noqa: N806
        handle = kernel32.OpenEventW(SYNCHRONIZE, False, _completion_event_name(pid))
        if not handle:
            return False
        try:
            rc = kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
            return rc == WAIT_OBJECT_0
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        log.debug("[PREWARM] Windows completion-event wait failed", exc_info=True)
        return False


def _wait_completion_linux(pid: int, timeout_s: float) -> bool:
    """Wait for process exit via pidfd + poll (fd-readable on exit)."""
    try:
        fd = os.pidfd_open(pid, 0)  # Linux 5.3+, Python 3.9+
    except (AttributeError, OSError):
        return False  # kernel too old or pid already gone — poll fallback
    try:
        poll = select.poll()
        poll.register(fd, select.POLLIN)
        events = poll.poll(timeout_s * 1000)
        return bool(events)
    except (OSError, ValueError):
        return False
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
