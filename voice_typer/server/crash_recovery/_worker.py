"""Crash-recovery save-worker concern: background save thread + atexit flush.

Owns everything that *schedules or performs* persistence outside the
request path:

- ``_LIVE_INSTANCES``  -- module-level WeakSet of live CrashRecovery
  instances (iterated by ``tests/conftest.py`` after each test to shut
  down leaked instances, and by the atexit flush).
- :func:`_atexit_flush_all` / :func:`_run_save_with_timeout` /
  ``_ATEXIT_FLUSH_TIMEOUT_S`` -- the module-level atexit machinery
  (bounded-wait final flush; bodies moved verbatim from the pre-split
  ``crash_recovery.py`` monolith).
- :class:`_SaveWorker` -- mixin providing ``_enqueue_save``,
  ``_start_save_thread``, ``_save_loop``, ``flush`` and ``shutdown``
  (bodies verbatim). :class:`CrashRecovery
  <voice_typer.server.crash_recovery._store.CrashRecovery>` inherits
  these; the public/instance-state methods stay in ``_store.py``.

Patch-target contract (same shape as the ``autostart/`` split): tests
monkeypatch helpers on the facade
(``voice_typer.server.crash_recovery.X``); leaf modules keep the
facade bindings resolvable because the facade re-exports every name
that lived on the pre-split module.
"""

import contextlib
import logging
import queue
import threading
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only forward reference (F821-free): the string annotations
    # below name CrashRecovery, which is assembled in ``_store.py``.
    from voice_typer.server.crash_recovery._store import CrashRecovery

# Logger name is PINNED to the package name so log records keep the
# exact pre-split logger identity (C-LOG-1) even though the bodies now
# live in a submodule (whose ``__name__`` would otherwise grow a
# ``._worker`` suffix).
log = logging.getLogger("voice_typer.server.crash_recovery")


# Bounded queue: if the worker falls behind (e.g. disk is slow),
# drop the oldest pending save rather than blocking the transcription
# thread.  The latest state is what matters; intermediate states are
# not useful for crash recovery.
_SAVE_QUEUE_MAXSIZE = 32


# module-level WeakSet tracking all live CrashRecovery instances.
# Tests that construct CrashRecovery via ``_MockApp`` helpers frequently
# leak the instance (and its ``crash-recovery-saver`` daemon thread)
# because the test fixture only calls ``IPCServer.stop()``, which does NOT
# shut down ``app._crash_recovery``. On Windows the accumulated daemon
# threads eventually trip a native limit and crash the whole pytest
# process mid-suite (). ``tests/conftest.py`` iterates this set after
# each test and calls ``shutdown()`` on any still-alive instance.
_LIVE_INSTANCES: "weakref.WeakSet[CrashRecovery]" = weakref.WeakSet()


def _atexit_flush_all() -> None:
    """Module-level atexit — flush every still-live CrashRecovery instance.

    Previously, ``CrashRecovery.__init__`` registered a PER-INSTANCE
    ``atexit.register(_atexit_save)`` closure that held a ``weakref`` to
    the instance. That worked but leaked one atexit entry per instance
    for the lifetime of the process (atexit has no public unregister-by-
    callable API, and the closures accumulated even after the
    CrashRecovery was GC'd). With a single module-level handler iterating
    ``_LIVE_INSTANCES`` (a WeakSet), atexit grows by exactly ONE entry
    regardless of how many CrashRecovery instances are constructed and
    GC'd over the process lifetime.

    Best-effort: each ``_save_sync()`` is wrapped in
    ``contextlib.suppress(Exception)`` so a failure on one instance
    doesn't skip the others. Mirrors the original per-instance handler's
    contract — atexit must never raise.

    ``_stopped`` is set to ``True`` before ``_save_sync``
    so the worker drain path (if any) knows to exit. After the save,
    ``_final_save_done`` is set to ``True`` so the subsequent
    ``__del__`` (which fires later during GC) observes the flag and
    skips the redundant atomic-write + rename. Setting ``_stopped``
    here is safe: the worker thread has already been torn down by
    interpreter shutdown (daemon threads are joined by the interpreter
    before atexit fires). The flag is set OUTSIDE ``_save_sync`` (not
    inside it) so ``shutdown()``'s final save does NOT set the flag —
    a post-shutdown ``__del__`` save for mutations that bypassed
    ``_enqueue_save`` (e.g. direct ``_entries.append`` in tests) must
    still persist (regression-tested by
    ``test_del_saves_unpersisted_post_shutdown_mutations``).

    each ``_save_sync()`` is now wrapped in a bounded-wait
    helper using a separate thread + ``Event.wait(timeout=2.0)``. Pre-fix
    a hung ``_save_sync`` (e.g. an NFS hang on the atomic write, an
    antivirus lock on Windows, fsync on a dying SSD) blocked atexit
    indefinitely — the interpreter refused to exit until the save
    returned, which on a misbehaving disk could be never. Post-fix
    the save runs in a short-lived worker thread; if it doesn't
    complete within 2.0 s, the atexit handler logs a WARNING and
    continues to the next instance (the worker thread is daemon, so
    it's reaped when the process exits). 2.0 s is generous enough
    for a healthy SSD save (~10 ms) and tight enough that the
    interpreter doesn't appear to hang on a stuck disk.
    """
    for inst in list(_LIVE_INSTANCES):
        with contextlib.suppress(Exception):
            inst._stopped = True
            # bounded-wait helper. Run ``_save_sync`` in a
            # short-lived daemon thread; if it doesn't return within
            # ``_ATEXIT_FLUSH_TIMEOUT_S`` seconds, log WARNING and
            # move on so a hung save doesn't block interpreter exit.
            # pass ``durability=True`` so the final shutdown
            # save runs both fsyncs (file data + parent dir). The
            # per-dictation path uses ``durability=False`` (5+ saves/sec
            # under streaming — fsync cost not worth it for non-critical
            # data), but atexit is a one-time cost where the durability
            # guarantee matters (a crash immediately after exit must
            # not lose the final state).
            _run_save_with_timeout(inst, _ATEXIT_FLUSH_TIMEOUT_S, durability=True)
            # mark the final save as done so the subsequent
            # __del__ (fired by GC) skips the redundant write. Set
            # under no lock here — atexit is single-threaded by
            # definition (the interpreter only fires it once, after
            # all non-daemon threads have exited), so there's no race
            # with another final-save path. ``_save_sync`` checks the
            # flag under ``_save_lock``, but the check+set here is
            # safe because no concurrent caller can reset the flag
            # (the only reset path is ``_enqueue_save``, which
            # requires the worker thread to be running — but the
            # worker is a daemon thread that has already exited by
            # the time atexit fires).
            inst._final_save_done = True


def _run_save_with_timeout(inst: "CrashRecovery", timeout: float, *, durability: bool = False) -> None:
    """run ``inst._save_sync()`` with a bounded wait.

    Spawns a daemon thread to invoke ``_save_sync``; if the call
    doesn't return within ``timeout`` seconds, logs WARNING and
    returns (the daemon worker is reaped when the process exits).

    Rationale: ``_save_sync`` does atomic-write + rename + (on the
    first save) mkdir + chmod. On a healthy disk this completes in
    <50 ms, but on a misbehaving disk (NFS hang, antivirus lock,
    dying SSD with slow fsync) it can block for tens of seconds.
    Pre-fix a hung save blocked atexit indefinitely; post-fix the
    atexit handler moves on after ``timeout`` so the interpreter can
    exit. The hung save itself is best-effort — if it eventually
    completes (e.g. NFS recovers), the file lands on disk; if it
    doesn't, the recovery state for that instance is lost (acceptable
    — atexit is a safety net, not a guarantee).

    ``durability`` is forwarded to ``_save_sync``. The atexit
    caller passes ``durability=True`` (one-time final shutdown save —
    durability guarantee matters there); other callers use the
    default ``False``.

    The helper is module-level (not a method) so it doesn't capture
    ``self`` and can be unit-tested in isolation.
    """
    done = threading.Event()
    worker_exc: list[BaseException] = []

    def _worker() -> None:
        try:
            # ``set_final_save_done=True`` closes the redundant-write
            # race where a concurrent worker thread (blocked on
            # ``_save_lock`` waiting for this atexit-save to release)
            # would otherwise observe ``_final_save_done = False`` and
            # re-write the file. See ``_save_sync``'s docstring for
            # the full rationale.
            inst._save_sync(durability=durability, set_final_save_done=True)
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            worker_exc.append(exc)
        finally:
            done.set()

    t = threading.Thread(
        target=_worker,
        name="crash-recovery-atexit-save",
        daemon=True,
    )
    t.start()
    completed = done.wait(timeout=timeout)
    if not completed:
        log.warning(
            "[RECOVERY] atexit _save_sync() did not complete within %.2fs; "
            "continuing (the daemon worker thread will be reaped on exit). "
            "The recovery file for this instance may not be persisted.",
            timeout,
        )
        return
    # If the worker raised, re-raise so the outer ``contextlib.suppress``
    # in ``_atexit_flush_all`` catches it (preserves the original
    # best-effort contract — atexit must never raise).
    if worker_exc:
        raise worker_exc[0]


# bounded-wait timeout for ``_atexit_flush_all``. 2.0 s is
# generous for a healthy SSD save (~10 ms) and tight enough that the
# interpreter doesn't appear to hang on a stuck disk. Tunable for tests
# via monkeypatch.
_ATEXIT_FLUSH_TIMEOUT_S = 2.0


class _SaveWorker:
    """Mixin: background save-thread machinery for :class:`CrashRecovery`.

    All disk writes are serialized through a single background worker
    thread (RELIABILITY-005).  Method bodies are moved verbatim from
    the pre-split ``crash_recovery.py``; they operate purely on
    ``self`` state (``_save_queue`` / ``_save_thread`` / ``_stopped``
    / ``_save_lock``) created by ``CrashRecovery.__init__``.
    """

    def _enqueue_save(self) -> None:
        """Enqueue a save request to the background worker.

        If the queue is full (worker fell behind), drop the oldest
        pending save.  This is safe because saves are idempotent —
        only the latest snapshot matters for crash recovery.

        a-review Finding A1: after ``shutdown()`` the worker thread
        has exited, so enqueuing would silently lose the mutation.
        Fall back to a synchronous save (serialized via
        ``_save_lock``) to honor the documented shutdown() contract:
        "After shutdown, any further calls to ``add()`` /
        ``mark_pasted()`` / etc. will fall back to synchronous saves".

        before the post-shutdown synchronous save, RESET
        ``_final_save_done`` to ``False``. The flag may have been
        set by the previous atexit save; without this reset, the new
        mutation would be silently dropped by ``_save_sync``'s
        short-circuit. The reset is safe because we're about to call
        ``_save_sync`` immediately after (and ``_save_sync`` does
        NOT re-set the flag — only ``_atexit_flush_all`` does).
        """
        if self._stopped:
            # Worker has exited (or never started) — persist on the
            # caller's thread.  ``_save_sync`` takes ``_save_lock``
            # so concurrent post-shutdown callers serialize cleanly.
            # a previous atexit save may have set
            # ``_final_save_done``; this new mutation MUST be
            # persisted, so clear the flag before the save.
            self._final_save_done = False
            self._save_sync()
            return
        try:
            self._save_queue.put_nowait({"snapshot": True})
        except queue.Full:
            # Drop oldest pending save and try again.  The latest
            # state will be persisted by the next put.  We must call
            # task_done() on the dropped item so that any pending
            # Queue.join() (e.g. from flush()) doesn't block forever
            # waiting for a task that will never be processed.
            try:
                self._save_queue.get_nowait()
                self._save_queue.task_done()
            except queue.Empty:
                pass
            try:
                self._save_queue.put_nowait({"snapshot": True})
            except queue.Full:
                log.warning("[RECOVERY] save queue full; skipping save")

    def _start_save_thread(self) -> None:
        """Start (or restart) the background save worker thread.

        THREAD-REGISTRY: when a registry was provided to ``__init__``,
        the worker thread is registered so ``shutdown_all()`` can join
        it during ``VoiceTyperApp.quit()``. We register with
        ``stop_event=None`` because the existing ``shutdown()`` method
        handles the actual stop via the ``_stopped`` boolean + None
        sentinel on the queue. The registry's ``shutdown_all()`` just
        verifies the thread is tracked; the existing per-site cleanup
        (``flush()`` + ``shutdown()``) handles the actual shutdown.
        """
        if self._save_thread is not None and self._save_thread.is_alive():
            return
        self._stopped = False
        self._save_thread = threading.Thread(
            target=self._save_loop,
            name="crash-recovery-saver",
            daemon=True,
        )
        self._save_thread.start()
        if self._thread_registry is not None:
            self._thread_registry.register(
                name="crash-recovery-saver",
                thread=self._save_thread,
                stop_event=None,
                # Short timeout: shutdown_all() can't actually stop this
                # thread (no stop_event), so we just verify it's tracked.
                # The existing ``_do_cleanup()`` path calls ``flush()`` +
                # ``shutdown()`` which gracefully drains and stops the
                # worker. A 0.5s join gives the worker a brief window to
                # exit naturally if it happens to be at a checkpoint.
                join_timeout=0.5,
            )

    def _save_loop(self) -> None:
        """Background worker: drain the save queue, writing to disk.

        the per-call ``timeout=1.0`` was a 1 Hz wakeup on a tray
        app that should sit quietly between dictations. The timeout only
        exists as a safety net so the loop re-checks ``self._stopped``
        if ``shutdown()`` fails to push the ``None`` sentinel (the
        ``with contextlib.suppress(queue.Full)`` at the shutdown call
        site swallows the put failure). A 30s fallback achieves the
        same safety net at 1/30th the wakeup cost. The ``None`` sentinel
        from ``shutdown()`` wakes the blocking ``get()`` immediately on
        normal shutdown — the 30s timeout is ONLY for the rare
        queue.Full failure mode.

        the loop body is now wrapped in a top-level
        ``try/except Exception:`` that logs and continues. Pre-fix, an
        unexpected exception (e.g. ``OSError`` from a transient disk
        failure that ``_save_sync``'s inner try/except didn't catch,
        ``MemoryError`` during snapshot serialization, or a stray
        ``RuntimeError`` from the JSON encoder) killed the worker
        thread silently — subsequent ``add()`` calls enqueued saves
        that were never drained, and the final shutdown save was the
        only path to disk. Post-fix the worker logs the exception at
        ERROR (so the operator sees the degradation) and continues
        processing the next queued item. ``BaseException``
        (``KeyboardInterrupt``, ``SystemExit``) is deliberately NOT
        caught so the worker dies cleanly when the interpreter is
        shutting down.
        """
        while not self._stopped:
            try:
                try:
                    item = self._save_queue.get(timeout=30.0)
                except queue.Empty:
                    continue
                if item is None:
                    # Sentinel: stop signal
                    # Balance the ``get()`` with ``task_done()`` before
                    # breaking — maintains the ``get()``/``task_done()``
                    # pairing invariant for any future ``Queue.join()`` caller
                    # (no current caller exists, but the pairing is the
                    # documented contract). ``flush()`` does NOT rely on this
                    # — it uses an explicit ``flush_event`` sentinel +
                    # ``threading.Event.wait(timeout)``, NOT the
                    # unfinished-tasks counter.
                    self._save_queue.task_done()
                    break
                if isinstance(item, dict) and "flush_event" in item:
                    # flush barrier sentinel.  All saves queued
                    # before this sentinel have now been processed, so
                    # signal the waiting flush() caller.  Do NOT treat
                    # this as a save — it is a barrier, not a snapshot
                    # request.  The worker continues running and remains
                    # ready for more items.
                    event = item.get("flush_event")
                    if event is not None:
                        with contextlib.suppress(Exception):
                            event.set()
                    self._save_queue.task_done()
                    continue
                self._save_sync()
                self._save_queue.task_done()
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                # only the "exit" ``BaseException`` subclasses
                # must propagate so the worker dies cleanly during
                # interpreter shutdown. Re-raise.
                #
                # This clause was previously ``except BaseException:``,
                # which also matched every ``Exception`` subclass —
                # making the ``except Exception:`` log-and-continue
                # clause below unreachable dead code. Any regular
                # exception that escaped ``_save_sync`` (or any other
                # line in the loop body) was re-raised, killing the
                # worker thread silently; subsequent ``add()`` calls
                # enqueued saves that were never drained. The explicit
                # tuple restricts propagation to the three "exit"
                # signals so ordinary ``Exception`` subclasses fall
                # through to the log-and-continue handler.
                raise
            except Exception:
                # log and continue. Pre-fix the worker would
                # die silently on an unexpected exception, leaving
                # subsequent saves un-processed. The ``task_done()``
                # for the current item may not have fired yet — the
                # ``Queue.join()`` invariant is best-effort (no current
                # caller exists), and a missed ``task_done()`` only
                # affects ``flush()`` if the failing item happened to
                # be a flush sentinel (handled separately above).
                log.exception(
                    "[RECOVERY] _save_loop worker caught unexpected exception "
                    "(continuing; the item was logged above if it was a save)"
                )

    def flush(self, timeout: float = 2.0) -> bool:
        """Wait for all pending saves to complete.

        Parameters
        ----------
        timeout : float
            Maximum seconds to wait.  Returns True if all saves
            completed, False if the timeout elapsed.

        Notes
        -----
        Useful at process shutdown (called from ``quit()`` /
        ``restart_app()``) to ensure the final state is persisted
        before the process exits.

        previously this called ``Queue.join()``, which has no
        ``timeout`` parameter in the stdlib — if the worker stalled
        (disk full, NFS hang, fsync on a dying SSD, antivirus lock
        on Windows), ``flush()`` blocked forever, preventing clean
        shutdown.  Now we enqueue a sentinel carrying a
        ``threading.Event``; the worker sets the event when it
        reaches the sentinel (meaning all prior saves are done).
        We wait on the event with the timeout, so the timeout is
        actually enforced.  The worker thread is NOT killed when
        the timeout fires — it keeps running and will eventually
        process the sentinel (the event.set() becomes a no-op).
        """
        event = threading.Event()
        sentinel = {"flush_event": event}
        # Enqueue the sentinel.  If the queue is full (worker is way
        # behind), try to make room by dropping the oldest pending
        # item — matching the _enqueue_save strategy.  We call
        # task_done() on the dropped item to keep the queue's
        # unfinished_tasks counter consistent.
        try:
            self._save_queue.put_nowait(sentinel)
        except queue.Full:
            try:
                self._save_queue.get_nowait()
                self._save_queue.task_done()
            except queue.Empty:
                pass
            try:
                self._save_queue.put_nowait(sentinel)
            except queue.Full:
                log.warning("[RECOVERY] flush: save queue full; cannot enqueue sentinel")
                return False
        completed = event.wait(timeout=timeout)
        if not completed:
            log.warning(
                "[RECOVERY] flush timed out after %.2fs; pending saves may be lost",
                timeout,
            )
            return False
        return True

    def shutdown(self) -> None:
        """Signal the background save thread to stop.

        Safe to call multiple times.  After shutdown, any further
        calls to ``add()`` / ``mark_pasted()`` / etc. will fall
        back to synchronous saves (so the data is still persisted,
        just on the calling thread).

        S-6: ``_save_thread`` is now joined with a short timeout
        after the sentinel is enqueued, so the thread is properly
        tracked and doesn't leak in test start/stop cycles.

        a-review Finding A3: after joining the worker, do one final
        ``_save_sync()`` so any mutations queued or in-flight when
        shutdown was called are guaranteed persisted.  This is cheap
        insurance on top of the worker's natural drain — and it also
        covers the rare window where a concurrent ``add()`` races
        with shutdown() and enqueues after the sentinel.
        """
        self._stopped = True
        with contextlib.suppress(queue.Full):
            self._save_queue.put_nowait(None)  # sentinel
        if self._save_thread is not None and self._save_thread.is_alive():
            self._save_thread.join(timeout=1.0)
        # Final synchronous save after the worker has exited.  The
        # worker should have drained the queue, but if it timed out
        # or if a concurrent caller raced in a final mutation, this
        # guarantees the latest ``_entries`` state is on disk.
        # ``_save_lock`` makes this safe even if a post-shutdown
        # ``add()`` is in flight on another thread.
        self._save_sync()
