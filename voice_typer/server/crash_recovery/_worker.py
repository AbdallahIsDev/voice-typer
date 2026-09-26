"""Crash-recovery background worker."""

import contextlib
import logging
import queue
import threading
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only forward reference (F821-free): the string annotations
    from voice_typer.server.crash_recovery._store import CrashRecovery

# exact pre-split logger identity (C-LOG-1) even though the bodies now
log = logging.getLogger("voice_typer.server.crash_recovery")


# Bounded queue: if the worker falls behind (e.g. disk is slow),
_SAVE_QUEUE_MAXSIZE = 32


# module-level WeakSet tracking all live CrashRecovery instances.
_LIVE_INSTANCES: "weakref.WeakSet[CrashRecovery]" = weakref.WeakSet()


def _atexit_flush_all() -> None:
    """Module-level atexit, flush every still-live CrashRecovery instance."""
    for inst in list(_LIVE_INSTANCES):
        with contextlib.suppress(Exception):
            inst._stopped = True
            # bounded-wait helper. Run ``_save_sync`` in a
            _run_save_with_timeout(inst, _ATEXIT_FLUSH_TIMEOUT_S, durability=True)
            # mark the final save as done so the subsequent
            inst._final_save_done = True


def _run_save_with_timeout(inst: "CrashRecovery", timeout: float, *, durability: bool = False) -> None:
    """run ``inst._save_sync()`` with a bounded wait."""
    done = threading.Event()
    worker_exc: list[BaseException] = []

    def _worker() -> None:
        try:
            # ``set_final_save_done=True`` closes the redundant-write
            inst._save_sync(durability=durability, set_final_save_done=True)
        except BaseException as exc:  # noqa: BLE001, re-raised below
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
    if worker_exc:
        raise worker_exc[0]


# bounded-wait timeout for ``_atexit_flush_all``. 2.0 s is
_ATEXIT_FLUSH_TIMEOUT_S = 2.0


class _SaveWorker:
    """Mixin: background save-thread machinery for :class:`CrashRecovery`."""

    def _enqueue_save(self) -> None:
        """Enqueue a save request to the background worker."""
        if self._stopped:
            # Worker has exited (or never started), persist on the
            self._final_save_done = False
            self._save_sync()
            return
        try:
            self._save_queue.put_nowait({"snapshot": True})
        except queue.Full:
            # Drop oldest pending save and try again.  The latest
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
        it during ``LausuApp.quit()``. We register with
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
                join_timeout=0.5,
            )

    def _save_loop(self) -> None:
        """Background worker: drain the save queue, writing to disk."""
        while not self._stopped:
            try:
                try:
                    item = self._save_queue.get(timeout=30.0)
                except queue.Empty:
                    continue
                if item is None:
                    # Sentinel: stop signal
                    self._save_queue.task_done()
                    break
                if isinstance(item, dict) and "flush_event" in item:
                    # flush barrier sentinel.  All saves queued
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
                raise
            except Exception:
                # log and continue. Pre-fix the worker would
                log.exception(
                    "[RECOVERY] _save_loop worker caught unexpected exception "
                    "(continuing; the item was logged above if it was a save)"
                )

    def flush(self, timeout: float = 2.0) -> bool:
        """Wait for all pending saves to complete."""
        event = threading.Event()
        sentinel = {"flush_event": event}
        # Enqueue the sentinel.  If the queue is full (worker is way
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
        """Signal the background save thread to stop."""
        self._stopped = True
        with contextlib.suppress(queue.Full):
            self._save_queue.put_nowait(None)  # sentinel
        if self._save_thread is not None and self._save_thread.is_alive():
            self._save_thread.join(timeout=1.0)
        # Final synchronous save after the worker has exited.  The
        self._save_sync()
