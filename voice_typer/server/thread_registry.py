"""Thread registry for orderly shutdown joins."""

from __future__ import annotations

import logging
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Module-level registry of live ``ThreadRegistry`` instances so the test
_LIVE_REGISTRIES: weakref.WeakSet = weakref.WeakSet()

# Hard cap on one ``_drain_live_thread_registries()`` call (test-suite
_TEST_DRAIN_BUDGET_S = 5.0


def _drain_live_thread_registries() -> None:
    """Tests that construct a real ``LausuApp`` spawn real daemon
    ``LausuApp.__init__``'s now-removed ``_preload_vad_model``
    """
    # Deadline is computed on ``time.perf_counter()`` (NOT
    deadline = time.perf_counter() + _TEST_DRAIN_BUDGET_S
    for registry in list(_LIVE_REGISTRIES):
        if time.perf_counter() >= deadline:
            break
        try:
            registry.shutdown_all(deadline=deadline)
        except Exception:
            log.debug("[THREAD-REGISTRY] drain shutdown_all() failed", exc_info=True)
        # The registry is quiesced (or exhausted its join budget); drop
        _LIVE_REGISTRIES.discard(registry)


@dataclass
class ThreadRegistryEntry:
    """A single registered thread."""

    name: str
    thread: threading.Thread
    stop_event: threading.Event | None
    join_timeout: float


class ThreadRegistry:
    """Central registry for tracking and shutting down daemon threads."""

    def __init__(self) -> None:
        self._entries: dict[str, ThreadRegistryEntry] = {}
        # Register in the module-level WeakSet so the test-suite drain
        _LIVE_REGISTRIES.add(self)
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        thread: threading.Thread,
        stop_event: threading.Event | None,
        join_timeout: float,
        *,
        join_previous_timeout: float = 0.0,
    ) -> None:
        """Register a thread for shutdown tracking."""
        with self._lock:
            # auto-prune dead entries.
            self._prune_dead_locked()

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
                # optionally signal + join the previous
                if join_previous_timeout > 0 and existing.thread.is_alive():
                    if existing.stop_event is not None:
                        try:
                            existing.stop_event.set()
                        except Exception:
                            log.debug(
                                "[THREAD-REGISTRY] Failed to set stop_event for previous %r during re-register",
                                name,
                                exc_info=True,
                            )
                    try:
                        existing.thread.join(timeout=join_previous_timeout)
                    except Exception:
                        log.debug(
                            "[THREAD-REGISTRY] join() raised for previous %r during re-register",
                            name,
                            exc_info=True,
                        )
                    if existing.thread.is_alive():
                        log.warning(
                            "[THREAD-REGISTRY] Previous thread %r did "
                            "not exit within %.2fs during re-register "
                            "— overwriting anyway (daemon will be "
                            "reaped on process exit)",
                            name,
                            join_previous_timeout,
                        )
            self._entries[name] = ThreadRegistryEntry(
                name=name,
                thread=thread,
                stop_event=stop_event,
                join_timeout=join_timeout,
            )

    def spawn_and_register(
        self,
        name: str,
        target: Callable[..., None],
        *,
        args: tuple = (),
        kwargs: dict | None = None,
        stop_event: threading.Event | None = None,
        join_timeout: float = 5.0,
        daemon: bool = True,
        join_previous_timeout: float = 0.0,
    ) -> threading.Thread:
        """Create, start, and register a worker thread in one call.

        Returns
        """
        if kwargs is None:
            kwargs = {}
        t = threading.Thread(
            target=target,
            name=name,
            args=args,
            kwargs=kwargs,
            daemon=daemon,
        )
        t.start()
        self.register(
            name,
            t,
            stop_event,
            join_timeout,
            join_previous_timeout=join_previous_timeout,
        )
        return t

    def _prune_dead_locked(self) -> int:
        """Remove dead entries from ``self._entries``.

        Returns the number of entries removed so the public
        """
        dead_names = [name for name, entry in self._entries.items() if not entry.thread.is_alive()]
        for name in dead_names:
            del self._entries[name]
        if dead_names:
            log.debug(
                "[THREAD-REGISTRY] Reaped %d dead thread entries: %s",
                len(dead_names),
                dead_names,
            )
        return len(dead_names)

    def reap_dead(self) -> int:
        """Remove entries whose threads have exited naturally.

        Returns the number of entries removed. Safe to call at any time.
        """
        with self._lock:
            return self._prune_dead_locked()

    def unregister(self, name: str) -> None:
        """Remove a thread from the registry."""
        with self._lock:
            self._entries.pop(name, None)

    def list_active(self) -> list[str]:
        """Return a list of names of registered threads that are still alive."""
        with self._lock:
            return [name for name, entry in self._entries.items() if entry.thread.is_alive()]

    def list_all(self) -> list[str]:
        """Return a list of all registered thread names (alive or not)."""
        with self._lock:
            return list(self._entries.keys())

    def shutdown_all(self, *, deadline: float | None = None) -> None:
        """PERF-23: threads are signaled in a single pass (no join in"""
        with self._lock:
            entries = list(self._entries.values())

        if not entries:
            return

        # Each name is labeled ``thread=<name>`` (not joined bare):
        log.info(
            "[THREAD-REGISTRY] shutdown_all: signaling %d registered threads: %s",
            len(entries),
            ", ".join(f"thread={entry.name}" for entry in entries),
        )

        # signal ALL stop_events first (no join).
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

        # bounded join loop.
        join_slice = 0.1

        start = time.monotonic()
        # Per-thread deadline: ``start + entry.join_timeout``.
        deadlines = {id(entry): start + entry.join_timeout for entry in entries}
        # ITERATION CAP (clock-independent): each join slice is
        if deadline is not None:
            # Wall-clock budget in seconds; the join loop must never
            budget_seconds = max(0.0, deadline - time.perf_counter())
            max_join_iterations = int(budget_seconds / join_slice) + 2
        else:
            # join_timeout (the pre-existing PERF-23 contract).
            max_join_iterations = int(max((e.join_timeout for e in entries), default=0.0) / join_slice) + 2
        max_join_iterations = max(1, min(max_join_iterations, 1_000_000))
        # work on a mutable ``pending`` list so we can prune
        pending = list(entries)
        join_iterations = 0
        while True:
            join_iterations += 1
            if join_iterations > max_join_iterations:
                log.warning(
                    "[THREAD-REGISTRY] shutdown_all: join iteration cap "
                    "(%d) reached with %d threads still alive, "
                    "giving up (daemons will be reaped on process exit)",
                    max_join_iterations,
                    len([e for e in pending if e.thread.is_alive()]),
                )
                break
            # prune dead entries at the start of each slice
            pending = [e for e in pending if e.thread.is_alive()]
            if not pending:
                break
            now = time.monotonic()
            # If every alive thread has passed its individual deadline,
            joinable = [e for e in pending if now < deadlines[id(e)]]
            if not joinable:
                break
            # Wall-clock cap (test-suite drain): if a caller passed an
            if deadline is not None and time.perf_counter() >= deadline:
                break
            # Join each joinable thread with a short slice.
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

        # Step 3: log final state of each entry (exit / still alive).
        for entry in entries:
            if not entry.thread.is_alive():
                log.debug(
                    "[THREAD-REGISTRY] Thread %r exited cleanly after join",
                    entry.name,
                )
                continue
            if entry.stop_event is not None:
                # We signaled the thread but it didn't exit. This is a
                log.warning(
                    "[THREAD-REGISTRY] Thread %r did not exit within "
                    "%.2fs after stop_event was set (it will exit as a "
                    "daemon on its next iteration boundary)",
                    entry.name,
                    entry.join_timeout,
                )
            else:
                # No stop_event was provided, so we couldn't signal
                log.debug(
                    "[THREAD-REGISTRY] Thread %r has no stop_event and "
                    "did not exit within %.2fs (existing per-site "
                    "cleanup should handle it)",
                    entry.name,
                    entry.join_timeout,
                )

        # auto-prune dead entries from self._entries so the
        with self._lock:
            self._prune_dead_locked()
