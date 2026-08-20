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
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Module-level registry of live ``ThreadRegistry`` instances so the test
# suite can drain (``shutdown_all``) any threads still alive when a test
# ends (mirrors ``crash_recovery._LIVE_INSTANCES``); production
# behaviour is unaffected — a WeakSet drops instances automatically on
# GC.
_LIVE_REGISTRIES: weakref.WeakSet = weakref.WeakSet()

# Hard cap on one ``_drain_live_thread_registries()`` call (test-suite
# infrastructure). Each registry's ``shutdown_all()`` joins its threads
# with their per-thread ``join_timeout`` (up to 2-3s for a still-running
# vad-preload / model-load thread). Under xdist a worker accumulates
# dozens of registries over the suite; without a cap, a single test's
# autouse teardown drain could take 100+ seconds (2s × ~50 registries),
# pushing the next test past pytest-timeout's per-test limit — the
# timeout's thread method then kills the WHOLE worker via os._exit(1)
# (``[gwN] node down: Not properly terminated``). The cap bounds any
# single drain to a few seconds; leftover threads are daemons that exit
# on their own and are re-visited on the next drain.
_TEST_DRAIN_BUDGET_S = 5.0


def _drain_live_thread_registries() -> None:
    """Call ``shutdown_all()`` on every live ``ThreadRegistry``.

    Tests that construct a real ``VoiceTyperApp`` spawn real daemon
    threads registered on ``app._thread_registry`` (e.g. the ``vad-preload``
    worker armed by ``VoiceTyperApp.__init__`` -> ``_preload_vad_model``,
    join_timeout=2.0). Under xdist a worker process lives for the WHOLE
    suite, so those threads accumulate; if a ``vad-preload`` worker wakes
    during a ``@pytest.mark.real_torch`` test window (which evicts the
    session torch mock), it loads REAL torch + the real Silero model and
    runs inference concurrently with history_db file copies — a
    combination that produced rare native heap corruption
    (``Windows fatal exception: code 0xc0000374``) on a fully-loaded
    worker. Draining every live registry between tests quiesces those
    threads.

    ``shutdown_all()`` is idempotent and joins with each thread's own
    bounded ``join_timeout``. The drain is time-budgeted (see
    ``_TEST_DRAIN_BUDGET_S``) and drops each drained registry from the
    WeakSet so repeated per-test drains stay O(1) instead of re-walking
    the whole accumulated set every time.
    """
    # Deadline is computed on ``time.perf_counter()`` (NOT
    # ``time.monotonic()``): a test that leaks a bare
    # ``mod.time.monotonic = MagicMock(return_value=...)`` mutation (see
    # the clipboard test files) permanently freezes the GLOBAL
    # ``time.monotonic`` for the whole xdist worker, which would make a
    # monotonic-based deadline never expire and turn the drain's join
    # loop into an infinite spin — the silent ``[gwN] node down``
    # worker-death signature. ``perf_counter`` is a distinct clock and
    # is not a patch target in the suite. As a second line of defense,
    # ``shutdown_all`` also caps the join loop by ITERATION COUNT (see
    # there), which is immune to ANY clock being frozen/mocked.
    deadline = time.perf_counter() + _TEST_DRAIN_BUDGET_S
    for registry in list(_LIVE_REGISTRIES):
        if time.perf_counter() >= deadline:
            break
        try:
            registry.shutdown_all(deadline=deadline)
        except Exception:
            log.debug("[THREAD-REGISTRY] drain shutdown_all() failed", exc_info=True)
        # The registry is quiesced (or exhausted its join budget); drop
        # it so later drains don't re-walk it. Fresh registries created
        # by subsequent tests re-add themselves on construction.
        _LIVE_REGISTRIES.discard(registry)


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
        # Register in the module-level WeakSet so the test-suite drain
        # (``_drain_live_thread_registries``) can join any threads still
        # alive when a test ends. Mirrors the ``_LIVE_INSTANCES``
        # pattern in crash_recovery / transcription_watchdog; production
        # behaviour is unaffected — a WeakSet drops registries on GC.
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
        """Register a thread for shutdown tracking.

        If ``name`` is already registered AND the existing entry's
        thread is a different object, logs a warning and overwrites the
        entry. Re-registering the same name with the same thread object
        is silent (used to update ``stop_event`` / ``join_timeout``).

        (auto-prune): dead entries are removed from the
        registry at the start of every ``register()`` call. Threads
        that exited naturally but were never ``unregister()``'d would
        otherwise accumulate; pruning here keeps ``self._entries`` from
        growing without bound.

        (optional join-previous): if *join_previous_timeout* >
        0 and *name* is already registered with a DIFFERENT thread
        object, the previous thread's ``stop_event`` (if any) is set
        and the thread is joined with up to *join_previous_timeout*
        seconds before the entry is overwritten. This closes the
        "re-register overwrites without stopping the old thread" gap.
        Default ``0.0`` preserves the prior behavior (no join, just
        overwrite with a warning).

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
        join_previous_timeout : float
            If > 0 and *name* is already registered with a different
            thread, signal the old thread's ``stop_event`` (if any)
            and join it with up to this many seconds before
            overwriting. Default ``0.0`` = no join (preserve prior
            behavior).
        """
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
                # thread before overwriting.
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

        closes the "forgot to register" gap. The typical
        pattern at spawn sites is::

            stop = threading.Event()
            t = threading.Thread(
                target=worker, name="my-worker", daemon=True, args=(stop,),
            )
            t.start()
            registry.register("my-worker", t, stop, join_timeout=2.0)

        ``spawn_and_register`` does all four steps (create, start,
        register, plus optional join-previous) in one call so a thread
        can never be left untracked.

        Parameters
        ----------
        name : str
            Human-readable identifier for the thread (also used as
            the thread's ``name``).
        target : callable
            The thread's target callable.
        args : tuple
            Positional args for *target*.
        kwargs : dict | None
            Keyword args for *target*. ``None`` -> empty dict.
        stop_event : threading.Event | None
            Event to set to signal the thread to stop. ``None`` if the
            thread doesn't support graceful shutdown.
        join_timeout : float
            Maximum seconds to wait for the thread to exit during
            ``shutdown_all()``.
        daemon : bool
            Whether the thread is a daemon (default True).
        join_previous_timeout : float
            Forwarded to :meth:`register`. Default ``0.0`` = no join.

        Returns
        -------
        threading.Thread
            The started, registered thread.
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

        threads that exited naturally but were never
        ``unregister()``'d would otherwise accumulate. This is called
        from ``register()`` (under ``self._lock``) and at the end of
        ``shutdown_all()`` (under ``self._lock``) to keep the registry
        from growing without bound.

        Returns the number of entries removed so the public
        ``reap_dead()`` wrapper (and tests) can assert on the count.

        Caller MUST hold ``self._lock``.
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
        Called automatically at the start of ``register()`` (via
        ``_prune_dead_locked``) to prevent unbounded growth from callers
        that forget to call ``unregister()``. This is the public alias
        for the lock-holding ``_prune_dead_locked`` helper.
        """
        with self._lock:
            return self._prune_dead_locked()

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

    def shutdown_all(self, *, deadline: float | None = None) -> None:
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

        (auto-prune): at the start of each join-loop slice,
        dead entries are pruned from the local iteration set so we
        don't keep re-checking threads that have already exited. At
        the very end (after Phase 3 logging), ``self._entries`` is
        pruned of dead entries so the registry doesn't accumulate
        stale entries across repeated ``shutdown_all()`` calls.
        """
        with self._lock:
            entries = list(self._entries.values())

        if not entries:
            return

        log.info(
            "[THREAD-REGISTRY] shutdown_all: signaling %d registered threads: %s",
            len(entries),
            ", ".join(entry.name for entry in entries),
        )

        # signal ALL stop_events first (no join).
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

        # bounded join loop.
        # Each thread has its OWN deadline (``start + entry.join_timeout``).
        # We loop, joining each still-alive thread with a short slice
        # (0.1s) per iteration, until either:
        #   - all threads are dead, OR
        #   - every remaining alive thread has passed its individual
        #     deadline (i.e. its per-thread join_timeout has elapsed).
        # This bounds the total shutdown time by ``max(join_timeout)``
        # (NOT ``sum(join_timeout)``) — a stuck thread no longer blocks
        # shutdown of the other threads.
        join_slice = 0.1

        start = time.monotonic()
        # Per-thread deadline: ``start + entry.join_timeout``.
        deadlines = {id(entry): start + entry.join_timeout for entry in entries}
        # ITERATION CAP (clock-independent): each join slice is
        # ``join_slice`` (0.1s) of real time, so the number of slices
        # we may spend is bounded by the wall-clock budget DIVIDED BY
        # the slice length — no reliance on any clock reading. A test
        # that leaks ``time.monotonic``/``time.perf_counter`` freezes
        # (e.g. ``mod.time.monotonic = MagicMock(return_value=100.3)``
        # in the clipboard tests) would otherwise make the deadline
        # checks below never fire and turn this loop into an infinite
        # spin that kills the whole xdist worker via pytest-timeout's
        # ``os._exit(1)`` (``[gwN] node down``). The cap makes the loop
        # terminate after at most ``budget`` seconds of real joining
        # even with a fully frozen clock. In production (no deadline)
        # the cap is derived from the largest per-thread join_timeout,
        # preserving the existing bounded-shutdown contract.
        if deadline is not None:
            # Wall-clock budget in seconds; the join loop must never
            # exceed it by more than one slice. The deadline was
            # computed by the caller on ``time.perf_counter()``.
            budget_seconds = max(0.0, deadline - time.perf_counter())
            max_join_iterations = int(budget_seconds / join_slice) + 2
        else:
            # No caller deadline: bound by the longest per-thread
            # join_timeout (the pre-existing PERF-23 contract).
            max_join_iterations = int(max((e.join_timeout for e in entries), default=0.0) / join_slice) + 2
        max_join_iterations = max(1, min(max_join_iterations, 1_000_000))
        # work on a mutable ``pending`` list so we can prune
        # dead entries at the start of each slice without losing the
        # original ``entries`` snapshot (which Phase 3 iterates for
        # per-entry "exited cleanly" / "did not exit" logging).
        pending = list(entries)
        join_iterations = 0
        while True:
            join_iterations += 1
            if join_iterations > max_join_iterations:
                log.warning(
                    "[THREAD-REGISTRY] shutdown_all: join iteration cap "
                    "(%d) reached with %d threads still alive — "
                    "giving up (daemons will be reaped on process exit)",
                    max_join_iterations,
                    len([e for e in pending if e.thread.is_alive()]),
                )
                break
            # prune dead entries at the start of each slice
            # so we don't keep re-checking threads that have already
            # exited.
            pending = [e for e in pending if e.thread.is_alive()]
            if not pending:
                break
            now = time.monotonic()
            # If every alive thread has passed its individual deadline,
            # we're done — give up on the laggards.
            joinable = [e for e in pending if now < deadlines[id(e)]]
            if not joinable:
                break
            # Wall-clock cap (test-suite drain): if a caller passed an
            # overall deadline (e.g. ``_drain_live_thread_registries``'s
            # 5s budget), stop mid-join once it elapses so a single
            # slow/hung thread (or a pathological ``join_timeout``
            # value) can never pin the main thread for the whole
            # per-test timeout — the exact worker-death signature seen
            # when a leaked registry's join ran 100+ seconds.
            #
            # ``perf_counter`` is used (NOT ``monotonic``) so a leaked
            # ``time.monotonic`` freeze can't defeat the cap.
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

        # auto-prune dead entries from self._entries so the
        # registry doesn't accumulate stale entries across repeated
        # shutdown_all() calls. Phase 3 above already logged each dead
        # entry as "exited cleanly" using the original snapshot, so
        # pruning here doesn't lose any logging. Alive entries stay
        # (they may exit on a subsequent shutdown_all() call).
        with self._lock:
            self._prune_dead_locked()
