"""Central registry for tracking and shutting down daemon threads.

Provides coordinated lifecycle management for all daemon threads spawned
by the application. Each thread is registered with a human-readable name,
a ``stop_event`` (used to signal graceful shutdown), and a ``join_timeout``.

Why this exists
---------------
Before this module, daemon threads were spawned across the codebase with
no central tracking:

- ``voice_typer/server/recording.py`` — audio-worker thread (Task 9),
  scipy-preloader thread (module-import-time one-shot)
- ``voice_typer/server/app.py`` — bubble-level-pusher thread (noted as a
  leaked daemon at app.py:1377)
- ``voice_typer/server/streaming.py`` — StreamingTranscription worker
- ``voice_typer/server/crash_recovery.py`` — crash-recovery-saver thread

The ``quit()`` path in ``app.py`` stopped each thread via ad-hoc code at
the spawn sites, with no central place to verify all of them had exited.
This module provides that central place: a ``ThreadRegistry`` that
``VoiceTyperApp`` owns and that each spawn site registers with.

Design contract
---------------
1. **Defensive registration.** Re-registering an existing name logs a
   warning and overwrites the entry. The caller is responsible for
   stopping the previous thread if needed; ``register()`` does NOT
   join or signal it.
2. **Idempotent shutdown.** ``shutdown_all()`` can be called any number
   of times. Subsequent calls re-attempt joins on any threads that
   didn't exit on the first call.
3. **Per-thread timeout.** Each registration carries its own
   ``join_timeout``. A thread that doesn't exit in time is logged and
   skipped — shutdown is never blocked by a single stuck thread.
4. **Optional stop_event.** Threads that don't support graceful
   shutdown (e.g. one-shot preloaders) register with
   ``stop_event=None``. ``shutdown_all()`` will still join such
   threads (with the registered timeout) but won't try to signal them.
5. **Cross-platform.** Uses only ``threading.Thread`` / ``threading.Event``
   — no platform-specific primitives. Safe on Windows, macOS, Linux.

Integration
-----------
``VoiceTyperApp.__init__`` creates a ``ThreadRegistry`` and passes it
to the subsystems that spawn threads (``Recorder``, ``CrashRecovery``,
``StreamingTranscriptionSession``). ``VoiceTyperApp.quit()`` calls
``shutdown_all()`` before the existing ``_do_cleanup()`` sequence so
that the registry's signal-and-join runs first; the existing per-site
shutdown methods then run as a safety net (they're idempotent).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class ThreadRegistryEntry:
    """A single registered thread.

    Attributes
    ----------
    name : str
        Human-readable identifier for the thread. Must be unique within
        a registry; re-registering the same name overwrites the entry
        (with a warning log).
    thread : threading.Thread
        The tracked thread. Should already be started (or about to
        start) at registration time.
    stop_event : threading.Event | None
        Event to set to signal the thread to stop gracefully. ``None``
        if the thread doesn't support graceful shutdown (e.g. one-shot
        preloaders); ``shutdown_all()`` will just join such threads
        without signaling them first.
    join_timeout : float
        Maximum seconds to wait for the thread to exit during
        ``shutdown_all()``. If the thread doesn't exit in time, a
        warning is logged and shutdown continues.
    """

    name: str
    thread: threading.Thread
    stop_event: threading.Event | None
    join_timeout: float


class ThreadRegistry:
    """Central registry for tracking and shutting down daemon threads.

    Owns a mapping from human-readable names to ``ThreadRegistryEntry``
    instances. The registry is thread-safe — all public methods take a
    lock to guard the internal dict.

    Typical usage::

        registry = ThreadRegistry()
        stop_event = threading.Event()
        thread = threading.Thread(target=worker, name="my-worker", daemon=True)
        thread.start()
        registry.register("my-worker", thread, stop_event, join_timeout=2.0)
        # ... later, during shutdown:
        registry.shutdown_all()
    """

    def __init__(self) -> None:
        self._entries: dict[str, ThreadRegistryEntry] = {}
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        thread: threading.Thread,
        stop_event: threading.Event | None,
        join_timeout: float,
    ) -> None:
        """Register a thread for shutdown tracking.

        If ``name`` is already registered AND the existing entry's
        thread is a different object, logs a warning and overwrites the
        entry. The caller is responsible for stopping the previous
        thread if needed — ``register()`` does NOT join or signal it.
        Re-registering the same name with the same thread object is
        silent (used to update ``stop_event`` / ``join_timeout``).

        Parameters
        ----------
        name : str
            Human-readable identifier for the thread.
        thread : threading.Thread
            The thread to track.
        stop_event : threading.Event | None
            Event to set to signal the thread to stop. ``None`` if the
            thread doesn't support graceful shutdown.
        join_timeout : float
            Maximum seconds to wait for the thread to exit during
            ``shutdown_all()``.
        """
        with self._lock:
            existing = self._entries.get(name)
            if existing is not None and existing.thread is not thread:
                log.warning(
                    "[THREAD-REGISTRY] Re-registering name %r "
                    "(old thread alive=%s, new thread alive=%s). "
                    "Caller should ensure the old thread is properly "
                    "stopped before re-registering.",
                    name,
                    existing.thread.is_alive(),
                    thread.is_alive(),
                )
            self._entries[name] = ThreadRegistryEntry(
                name=name,
                thread=thread,
                stop_event=stop_event,
                join_timeout=join_timeout,
            )

    def unregister(self, name: str) -> None:
        """Remove a thread from the registry.

        Safe to call for a name that's not registered (no-op). Does
        NOT join or signal the thread — the caller is responsible for
        cleanup. Use this when a thread exits naturally and you want
        to remove it from tracking, or when a thread is being restarted
        and you want to clear the stale entry before re-registering.
        """
        with self._lock:
            self._entries.pop(name, None)

    def list_active(self) -> list[str]:
        """Return a list of names of registered threads that are still alive.

        Useful for diagnostics — e.g. logging which threads are still
        running during shutdown. The list is a snapshot at call time;
        threads may exit between this call and a subsequent
        ``shutdown_all()``.
        """
        with self._lock:
            return [name for name, entry in self._entries.items() if entry.thread.is_alive()]

    def list_all(self) -> list[str]:
        """Return a list of all registered thread names (alive or not)."""
        with self._lock:
            return list(self._entries.keys())

    def shutdown_all(self) -> None:
        """Signal all registered threads to stop and join them.

        Idempotent — safe to call multiple times. Subsequent calls
        re-attempt joins on any threads that didn't exit on the first
        call (e.g. because they were stuck in a long operation).

        For each registered thread:
        1. Set the ``stop_event`` (if not ``None``) to signal graceful
           shutdown.
        2. If the thread is still alive, join with the registered
           ``join_timeout``.
        3. If the thread doesn't exit within the timeout:
           - If a ``stop_event`` was provided: log a WARNING (the
             thread was signaled but didn't exit — possible deadlock
             or stuck I/O).
           - If ``stop_event`` was ``None``: log a DEBUG message (no
             signal was sent, so the timeout is expected; the existing
             per-site cleanup is responsible for stopping the thread).

        PERF-23: threads are signaled in a single pass (no join in
        the signal phase), then joined in a bounded poll loop. The
        total shutdown time is bounded by ``max(join_timeout)`` (NOT
        ``sum(join_timeout)``) — a stuck thread no longer blocks
        shutdown of the other threads. Each iteration joins every
        still-alive thread with a short ``timeout=0.1`` slice; the
        loop exits when all threads are dead or when the total
        elapsed time exceeds the maximum per-thread ``join_timeout``.
        """
        with self._lock:
            entries = list(self._entries.values())

        if not entries:
            return

        log.info(
            "[THREAD-REGISTRY] shutdown_all: signaling %d registered thread(s): %s",
            len(entries),
            ", ".join(entry.name for entry in entries),
        )

        # PERF-23 Phase 1: signal ALL stop_events first (no join).
        # This lets every thread begin its shutdown sequence in
        # parallel; we don't block on the slowest thread before
        # signaling the next.
        for entry in entries:
            if entry.stop_event is not None:
                try:
                    entry.stop_event.set()
                except Exception:
                    log.debug(
                        "[THREAD-REGISTRY] Failed to set stop_event for %r",
                        entry.name,
                        exc_info=True,
                    )

        # PERF-23 Phase 2: bounded join loop.
        # Each thread has its OWN deadline (``start + entry.join_timeout``).
        # We loop, joining each still-alive thread with a short slice
        # (0.1s) per iteration, until either:
        #   - all threads are dead, OR
        #   - every remaining alive thread has passed its individual
        #     deadline (i.e. its per-thread join_timeout has elapsed).
        # This bounds the total shutdown time by ``max(join_timeout)``
        # (NOT ``sum(join_timeout)``) — a stuck thread no longer blocks
        # shutdown of the other threads.
        if not entries:
            return
        join_slice = 0.1
        import time as _time

        start = _time.monotonic()
        # Per-thread deadline: ``start + entry.join_timeout``. A thread
        # is "expired" once monotonic time passes its deadline; we stop
        # joining it (but keep checking is_alive() so a late exit is
        # still logged as "exited cleanly").
        deadlines = {id(entry): start + entry.join_timeout for entry in entries}
        while True:
            now = _time.monotonic()
            alive_entries = [e for e in entries if e.thread.is_alive()]
            if not alive_entries:
                break
            # If every alive thread has passed its individual deadline,
            # we're done — give up on the laggards.
            joinable = [e for e in alive_entries if now < deadlines[id(e)]]
            if not joinable:
                break
            # Join each joinable thread with a short slice. Threads
            # that exit early return immediately; threads that need
            # more time get another slice on the next iteration.
            for entry in joinable:
                if not entry.thread.is_alive():
                    continue
                try:
                    entry.thread.join(timeout=join_slice)
                except Exception:
                    log.debug(
                        "[THREAD-REGISTRY] join() raised for %r",
                        entry.name,
                        exc_info=True,
                    )

        # Phase 3: log final state of each entry (exit / still alive).
        for entry in entries:
            if not entry.thread.is_alive():
                log.debug(
                    "[THREAD-REGISTRY] Thread %r exited cleanly after join",
                    entry.name,
                )
                continue
            if entry.stop_event is not None:
                # We signaled the thread but it didn't exit. This is a
                # potential deadlock or stuck I/O — surface it as a
                # warning so it shows up in logs.
                log.warning(
                    "[THREAD-REGISTRY] Thread %r did not exit within "
                    "%.2fs after stop_event was set (it will exit as a "
                    "daemon on its next iteration boundary)",
                    entry.name,
                    entry.join_timeout,
                )
            else:
                # No stop_event was provided, so we couldn't signal
                # the thread. The timeout is expected; the existing
                # per-site cleanup (e.g. CrashRecovery.shutdown()) is
                # responsible for actually stopping this thread.
                log.debug(
                    "[THREAD-REGISTRY] Thread %r has no stop_event and "
                    "did not exit within %.2fs (existing per-site "
                    "cleanup should handle it)",
                    entry.name,
                    entry.join_timeout,
                )
