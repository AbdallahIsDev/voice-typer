"""Crash recovery: stores last 10 transcriptions, checks on startup.

After each transcription, the text is saved to a recovery file.
On startup, if the recovery file has unpasted transcriptions,
the user is notified. The recovery file is cleared after acknowledgment.

RELIABILITY-005: previously, every call to ``add()``, ``mark_pasted()``,
``mark_latest_pasted()``, and ``clear()`` wrote to disk synchronously
on the calling thread (typically the transcription thread).  Under
repeated crashes or rapid transcriptions, these synchronous writes
blocked the main thread and could compound the crash condition by
delaying restart.  The fix moves disk writes to a dedicated background
thread with a bounded queue: callers enqueue a save request and
return immediately; the worker thread serializes the writes.
"""

import atexit
import collections
import contextlib
import json
import logging
import os
import queue
import threading
import weakref
from pathlib import Path
from typing import Any

# Import _secure_atomic_write at module load time so
# ``_save_sync`` doesn't need to lazily import it during interpreter
# shutdown (where the import machinery can fail, dropping the final
# recovery state).  ``config`` doesn't import ``crash_recovery``, so
# this is safe from circular-import issues.
from voice_typer.server.config import _secure_atomic_write
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

RECOVERY_FILENAME = "voice-typer-recovery.json"
MAX_RECOVERY_ENTRIES = 10

# Bounded queue: if the worker falls behind (e.g. disk is slow),
# drop the oldest pending save rather than blocking the transcription
# thread.  The latest state is what matters; intermediate states are
# not useful for crash recovery.
_SAVE_QUEUE_MAXSIZE = 32


# FT-2: module-level WeakSet tracking all live CrashRecovery instances.
# Tests that construct CrashRecovery via ``_MockApp`` helpers frequently
# leak the instance (and its ``crash-recovery-saver`` daemon thread)
# because the test fixture only calls ``IPCServer.stop()``, which does NOT
# shut down ``app._crash_recovery``. On Windows the accumulated daemon
# threads eventually trip a native limit and crash the whole pytest
# process mid-suite (FT-2). ``tests/conftest.py`` iterates this set after
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

    XZ-R17-13: ``_stopped`` is set to ``True`` before ``_save_sync``
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

    XE-16-6: each ``_save_sync()`` is now wrapped in a bounded-wait
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
            # XE-16-6: bounded-wait helper. Run ``_save_sync`` in a
            # short-lived daemon thread; if it doesn't return within
            # ``_ATEXIT_FLUSH_TIMEOUT_S`` seconds, log WARNING and
            # move on so a hung save doesn't block interpreter exit.
            # AB-44: pass ``durability=True`` so the final shutdown
            # save runs both fsyncs (file data + parent dir). The
            # per-dictation path uses ``durability=False`` (5+ saves/sec
            # under streaming — fsync cost not worth it for non-critical
            # data), but atexit is a one-time cost where the durability
            # guarantee matters (a crash immediately after exit must
            # not lose the final state).
            _run_save_with_timeout(inst, _ATEXIT_FLUSH_TIMEOUT_S, durability=True)
            # XZ-R17-13: mark the final save as done so the subsequent
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
    """XE-16-6: run ``inst._save_sync()`` with a bounded wait.

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

    AB-44: ``durability`` is forwarded to ``_save_sync``. The atexit
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
            inst._save_sync(durability=durability)
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


# XE-16-6: bounded-wait timeout for ``_atexit_flush_all``. 2.0 s is
# generous for a healthy SSD save (~10 ms) and tight enough that the
# interpreter doesn't appear to hang on a stuck disk. Tunable for tests
# via monkeypatch.
_ATEXIT_FLUSH_TIMEOUT_S = 2.0


atexit.register(_atexit_flush_all)


class CrashRecovery:
    """Stores recent transcriptions for crash recovery.

    All disk writes are serialized through a single background worker
    thread (RELIABILITY-005).  The in-memory ``_entries`` list is the
    source of truth for reads; the worker only persists it.
    """

    def __init__(
        self,
        config_dir: Path | None = None,
        thread_registry: Any | None = None,
    ):
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._path = config_dir / RECOVERY_FILENAME
        # Bounded deque: collections.deque(maxlen=...) auto-evicts the
        # oldest entry when full, so the manual ``while len() > MAX:
        # pop(0)`` trim in ``add()`` is now a defensive no-op (kept for
        # readability — it never executes under the bounded deque).
        self._entries: collections.deque = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
        self._lock = threading.Lock()
        # Serializes _save_sync() disk writes so that concurrent
        # callers (the background worker + any post-shutdown sync
        # fallback callers) don't trample each other on the file
        # write.  Distinct from ``_lock`` (which only guards the
        # in-memory ``_entries`` list) so reads/writes to
        # ``_entries`` aren't blocked while the disk I/O runs.
        # RELIABILITY-005 + a-review Finding A1: post-shutdown calls
        # to ``add()`` / ``mark_pasted()`` / etc. fall back to
        # synchronous saves (per the documented shutdown() contract);
        # this lock makes that fallback safe under concurrency.
        self._save_lock = threading.Lock()
        self._save_queue: queue.Queue[dict | None] = queue.Queue(maxsize=_SAVE_QUEUE_MAXSIZE)
        self._save_thread: threading.Thread | None = None
        self._stopped = False
        # XZ-R17-08: ``_dir_ensured`` guards the per-save ``os.chmod``
        # on ``self._path.parent``. The chmod is idempotent (setting
        # 0o700 on an already-0o700 dir is a no-op) but it's still a
        # syscall per transcription — under rapid dictation (5+ saves /
        # second when streaming is on) the redundant chmod dominated
        # the save path's syscall count. The flag is set on the first
        # successful mkdir+chmod; subsequent saves skip the chmod. If
        # the chmod fails (logged at warning), the flag is NOT set so
        # the next save retries — same behavior as before for the
        # failure case. Guarded by ``_save_lock`` (acquired in
        # ``_save_sync``) so the flag is race-free.
        self._dir_ensured = False
        # XZ-R17-13: ``_final_save_done`` deduplicates the final
        # shutdown save between the atexit handler
        # (``_atexit_flush_all``) and ``__del__``. Both paths call
        # ``_save_sync()`` during interpreter shutdown; without the
        # flag, the second path re-serialized the same ``_entries``
        # state and re-wrote the atomic temp file + rename — pure
        # wasted I/O on the shutdown path (where the GIL is being
        # torn down and the write window is most fragile). The flag
        # is set ONLY by ``_atexit_flush_all`` (NOT by ``shutdown()``
        # or ``_save_sync`` itself), so ``shutdown()``'s final save
        # does NOT suppress a subsequent ``__del__`` save for
        # post-shutdown mutations that bypassed ``_enqueue_save``
        # (regression-tested by
        # ``test_del_saves_unpersisted_post_shutdown_mutations``).
        # ``_enqueue_save`` resets the flag to ``False`` BEFORE its
        # post-shutdown synchronous save so a new mutation is never
        # silently dropped. Guarded by ``_save_lock`` so the check+set
        # is atomic.
        self._final_save_done = False
        # THREAD-REGISTRY: optional central registry for shutdown
        # coordination. When provided, the crash-recovery-saver thread
        # is registered so ``shutdown_all()`` can join it during
        # ``VoiceTyperApp.quit()``. We register with ``stop_event=None``
        # because the existing ``shutdown()`` method (called by
        # ``_do_cleanup()``) handles the actual stop via the
        # ``_stopped`` boolean + None sentinel on the queue. The
        # registry's ``shutdown_all()`` just verifies the thread is
        # tracked; the existing per-site cleanup handles the actual
        # shutdown. When ``None`` (e.g. in unit tests), behavior is
        # unchanged.
        self._thread_registry = thread_registry
        self._load()
        self._start_save_thread()
        # Atexit flushing is now handled by the SINGLE module-level
        # ``_atexit_flush_all`` handler (registered once at import time
        # above) which iterates ``_LIVE_INSTANCES`` and calls
        # ``_save_sync()`` on each. Previously this ctor registered a
        # per-instance ``atexit.register(_atexit_save)`` closure that
        # leaked one atexit entry per instance for the process lifetime.
        # Register in the module-level WeakSet so the test conftest
        # can shutdown leaked instances after each test (prevents the
        # daemon saver thread from accumulating across the full pytest run
        # and crashing the process on Windows via native thread-limit
        # exhaustion).
        _LIVE_INSTANCES.add(self)

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load recovery entries from disk.

        M-64: previously this used :meth:`pathlib.Path.read_text`,
        which silently follows symlinks — inconsistent with the
        write side (:meth:`_save_sync`), which already used
        :func:`voice_typer.server.config._secure_atomic_write`
        (POSIX ``O_NOFOLLOW``). A local attacker who replaced the
        recovery file with a symlink to a sensitive file (e.g.
        ``~/.ssh/id_rsa``) could have the JSON parser read its
        contents into memory (and logged in a warning on parse
        failure). Now we use :func:`_secure_read_text` so the read
        fails closed: symlink detected → ``OSError`` → ``_entries``
        is reset to ``[]`` and a warning is logged, matching the
        templates / vocabulary / config load paths.

        When the file exists but can't be parsed (corrupt
        JSON, truncated by a mid-write crash, etc.), rename it to
        ``<path>.corrupt.<timestamp>`` before resetting ``_entries``.
        This preserves the corrupt file for forensic review and
        ensures the next ``_save_sync()`` starts fresh instead of
        re-reading the same corrupt content.  Best-effort — a rename
        failure (e.g. cross-device, permissions) is logged and
        swallowed so ``_load`` still resets ``_entries`` cleanly.
        """
        if not self._path.exists():
            self._entries = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
            return
        try:
            from voice_typer.server.config import _secure_read_text

            raw = _secure_read_text(self._path)
            data = json.loads(raw)
            if isinstance(data, list):
                self._entries = collections.deque(data, maxlen=MAX_RECOVERY_ENTRIES)
            elif isinstance(data, dict) and "entries" in data:
                self._entries = collections.deque(data["entries"], maxlen=MAX_RECOVERY_ENTRIES)
            else:
                # Shape is wrong but JSON parsed — treat as
                # corrupt and quarantine so the next save isn't
                # merged with stale data.
                self._quarantine_corrupt()
                self._entries = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
            log.debug("[RECOVERY] Loaded %d entries", len(self._entries))
        except Exception as exc:
            log.warning("[RECOVERY] Failed to load: %s", exc)
            # Quarantine the corrupt file so the next save creates a
            # fresh one.  Best-effort — failures are logged and
            # swallowed so _load always resets _entries cleanly.
            self._quarantine_corrupt()
            self._entries = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)

    def _quarantine_corrupt(self) -> None:
        """Rename the recovery file to ``<path>.corrupt.<ts>``.

        Preserves the corrupt file for forensic review (e.g. inspecting
        what truncation pattern led to the parse failure) and ensures
        the next ``_save_sync()`` starts fresh instead of being merged
        with stale data.

        Best-effort: if the rename fails (cross-device, permissions,
        file disappeared between the ``exists()`` check and now), the
        failure is logged at ``debug`` level and swallowed.  This must
        never raise — callers (``_load``) rely on a clean reset to
        ``_entries = []`` regardless of quarantine outcome.
        """
        try:
            if not self._path.exists():
                return
            from datetime import datetime

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            corrupt_path = self._path.with_name(f"{self._path.name}.corrupt.{ts}")
            # If a corrupt file with the same timestamp already exists
            # (extremely unlikely — would need two crashes within the
            # same second), disambiguate with a counter.
            counter = 0
            while corrupt_path.exists():
                counter += 1
                corrupt_path = self._path.with_name(f"{self._path.name}.corrupt.{ts}.{counter}")
            self._path.rename(corrupt_path)
            log.warning(
                "[RECOVERY] Quarantined corrupt recovery file: %s -> %s",
                self._path.name,
                corrupt_path.name,
            )
        except Exception as exc:
            log.debug("[RECOVERY] Failed to quarantine corrupt file: %s", exc)

    def _save_sync(self, *, durability: bool = False) -> None:
        """Save recovery entries to disk synchronously.

        This is called only from the background save thread.  All
        other callers go through ``_enqueue_save()``.

        SEC-007: on POSIX, restricts file permissions to 0o600 so
        transcription text in the recovery file is not world-readable.

        NEW-SEC-008: uses the shared _secure_atomic_write which applies
        O_NOFOLLOW on POSIX to prevent symlink TOCTOU attacks.

        a-review Finding A1: ``_save_lock`` serializes the disk write
        so that the background worker and any post-shutdown sync
        fallback callers don't race on the file.  ``_lock`` is still
        acquired only for the in-memory snapshot so reads of
        ``_entries`` aren't blocked during I/O.

        ``_secure_atomic_write`` is imported at module
        load time (top of file) rather than lazily here.  This avoids
        the ``ImportError`` that occurred during interpreter shutdown
        when the import machinery was partially dismantled — the
        lazy import would silently fail and the final recovery state
        would be lost.

        XZ-R17-08: ``_dir_ensured`` guards the per-save
        ``os.chmod(self._path.parent, 0o700)``. The chmod is
        idempotent but it's still a syscall per transcription; the
        flag is set after the first successful mkdir+chmod and
        subsequent saves skip it. If the chmod fails (logged at
        warning), the flag is NOT set so the next save retries.

        AB-44: ``_dir_ensured`` now ALSO gates the per-save ``mkdir``
        on ``self._path.parent`` (same idempotent-syscall rationale
        as the chmod). The flag is set after the first successful
        mkdir+chmod; subsequent saves skip BOTH.  ``durability``
        controls whether ``_secure_atomic_write`` runs the two fsync
        calls (file data + parent dir).  Default ``False`` for the
        per-dictation path (5+ saves/sec under streaming — fsync cost
        is not worth it for non-critical data).  ``True`` may be
        passed by ``_atexit_flush_all`` and ``__del__`` for the final
        shutdown save (one-time cost, durability guarantee matters
        there).

        XZ-R17-13: ``_final_save_done`` deduplicates the final
        shutdown save. ``__del__`` and the atexit handler both call
        ``_save_sync()`` during interpreter shutdown; the flag
        (guarded by ``_save_lock``) makes the second call a no-op
        so the atomic-write + rename happens exactly once on the
        shutdown path. The flag is set ONLY by ``_atexit_flush_all``
        (NOT by this function or ``shutdown()``) — so ``shutdown()``'s
        final save does NOT suppress a subsequent ``__del__`` save
        for post-shutdown mutations. The flag is reset to ``False``
        by ``_enqueue_save`` when a new mutation arrives post-shutdown.

        XE-16-4: the lock acquisition is now INSIDE a top-level
        ``try/except Exception:`` so a lock-acquisition failure (e.g.
        a ``RuntimeError`` from a re-entrant acquire attempt during
        interpreter shutdown, or a ``BrokenPipeError``-style failure
        on a corrupt lock object) is logged and swallowed rather than
        propagating up and killing the worker thread. Pre-fix the
        ``with self._save_lock:`` lived at the function's top level;
        an exception there escaped into ``_save_loop`` (which had no
        top-level handler either — fixed separately in ``_save_loop``).
        """
        try:
            with self._save_lock:
                # XZ-R17-13: short-circuit if the atexit handler already
                # persisted the final state. The flag is set ONLY by
                # ``_atexit_flush_all`` (NOT by ``shutdown()`` or this
                # function) — so ``shutdown()``'s final save does NOT
                # suppress a subsequent ``__del__`` save for post-shutdown
                # mutations (the test
                # ``test_del_saves_unpersisted_post_shutdown_mutations``
                # verifies this). The flag is reset to ``False`` by
                # ``_enqueue_save`` when a new mutation arrives
                # post-shutdown, so post-shutdown ``add()`` calls still
                # persist even if atexit already fired.
                if self._final_save_done:
                    return
                try:
                    # AB-44: skip the mkdir on subsequent saves. The flag
                    # is set after the first successful mkdir+chmod; later
                    # saves only do the atomic write. The flag is cleared
                    # on any mkdir/chmod failure so the next save retries.
                    if not self._dir_ensured:
                        self._path.parent.mkdir(parents=True, exist_ok=True)
                        # XZ-R17-08: skip the chmod on subsequent saves.
                        # First save does the mkdir + chmod; later saves
                        # only do the atomic write. The flag is cleared on
                        # any chmod failure so the next save retries.
                        if not is_windows():
                            try:
                                os.chmod(self._path.parent, 0o700)
                            except OSError as e:
                                log.warning("[RECOVERY] Failed to chmod dir: %s", e)
                                # Don't set _dir_ensured — retry mkdir+chmod
                                # on the next save.  Still proceed with the
                                # save below; the chmod failure is not fatal.
                            else:
                                self._dir_ensured = True
                        else:
                            # Windows: no chmod, but mkdir succeeded — set
                            # the flag so we skip the redundant mkdir on
                            # subsequent saves.
                            self._dir_ensured = True
                    with self._lock:
                        # Convert deque to list for JSON serialization
                        # (collections.deque is not JSON-serializable).
                        snapshot = json.dumps(
                            {"entries": list(self._entries)},
                            indent=2,
                            ensure_ascii=False,
                        )
                    # AB-44: durability=False (default) for the per-dictation
                    # path.  The atexit handler and __del__ may pass
                    # durability=True for the final shutdown save.
                    _secure_atomic_write(self._path, snapshot, durability=durability)
                    # XZ-R17-13: NOTE — the flag is NOT set here. Only
                    # ``_atexit_flush_all`` sets the flag (after its own
                    # successful save). This ensures:
                    #   • ``shutdown()``'s final save does NOT suppress a
                    #     subsequent ``__del__`` save for post-shutdown
                    #     mutations (regression-tested by
                    #     ``test_del_saves_unpersisted_post_shutdown_mutations``).
                    #   • ``atexit`` (which fires after ``shutdown()``)
                    #     suppresses the redundant ``__del__`` save.
                    # The tradeoff: ``shutdown()`` + ``atexit`` produces 2
                    # writes (vs 3 pre-fix: shutdown + atexit + __del__).
                    # The abnormal-exit path (atexit + __del__, no
                    # shutdown) produces 1 write (atexit), since __del__
                    # observes the flag and skips.
                except Exception:
                    log.exception("[RECOVERY] Failed to save")
        except Exception:
            # XE-16-4: lock-acquisition failure (or any other exception
            # escaping the inner try/except). Log and swallow so the
            # caller (the worker thread, ``shutdown()``, ``__del__``,
            # ``_atexit_flush_all``) is not crashed by a save failure.
            log.exception(
                "[RECOVERY] _save_sync top-level failure (lock acquisition or "
                "unexpected error); the recovery file may not be persisted"
            )

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

        XZ-R17-13: before the post-shutdown synchronous save, RESET
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
            # XZ-R17-13: a previous atexit save may have set
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

        DJ-42: the per-call ``timeout=1.0`` was a 1 Hz wakeup on a tray
        app that should sit quietly between dictations. The timeout only
        exists as a safety net so the loop re-checks ``self._stopped``
        if ``shutdown()`` fails to push the ``None`` sentinel (the
        ``with contextlib.suppress(queue.Full)`` at the shutdown call
        site swallows the put failure). A 30s fallback achieves the
        same safety net at 1/30th the wakeup cost. The ``None`` sentinel
        from ``shutdown()`` wakes the blocking ``get()`` immediately on
        normal shutdown — the 30s timeout is ONLY for the rare
        queue.Full failure mode.

        XE-16-4: the loop body is now wrapped in a top-level
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
                    # RW-4: flush barrier sentinel.  All saves queued
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
            except BaseException:
                # XE-16-4: ``BaseException`` (``KeyboardInterrupt``,
                # ``SystemExit``) must propagate so the worker dies
                # cleanly during interpreter shutdown. Re-raise.
                raise
            except Exception:
                # XE-16-4: log and continue. Pre-fix the worker would
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

        RW-4: previously this called ``Queue.join()``, which has no
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

    # ── Public API ───────────────────────────────────────────────────

    def add(self, text: str, *, pasted: bool = False) -> None:
        """Add a transcription to the recovery buffer.

        Keeps only the last MAX_RECOVERY_ENTRIES entries.

        Args:
            text: The transcribed text to store.
            pasted: Whether the text was successfully pasted.
        """
        from datetime import datetime

        entry = {
            "text": text,
            "timestamp": datetime.now().isoformat(),
            "pasted": pasted,
        }
        with self._lock:
            self._entries.append(entry)
            # Trim to max
            while len(self._entries) > MAX_RECOVERY_ENTRIES:
                self._entries.popleft()
        self._enqueue_save()

    def mark_pasted(self, index: int) -> bool:
        """Mark an entry as successfully pasted.

        Args:
            index: The index of the entry to mark.

        Returns:
            True if the entry was found and marked, False otherwise.

        XE-16-1: ``_enqueue_save()`` is now called OUTSIDE the
        ``with self._lock:`` block. Pre-fix, the in-line call could
        deadlock when invoked post-shutdown: ``_enqueue_save()`` falls
        back to ``_save_sync()`` which acquires ``_save_lock`` and then
        (for the snapshot) re-acquires ``self._lock`` — but the calling
        thread was already holding ``self._lock``, so the re-acquire
        deadlocked. Moving the enqueue out of the lock scope breaks the
        re-entrancy. Mirrors the existing pattern in
        ``mark_latest_pasted`` (which already enqueues outside the
        lock) and ``add()`` / ``clear()``.
        """
        found = False
        with self._lock:
            if 0 <= index < len(self._entries):
                self._entries[index]["pasted"] = True
                found = True
        if found:
            self._enqueue_save()
        return found

    def mark_latest_pasted(self) -> None:
        """Mark the most recent entry as pasted.

        This is called after a successful paste operation to indicate
        the transcription was delivered to the target application.
        """
        with self._lock:
            if self._entries:
                self._entries[-1]["pasted"] = True
        self._enqueue_save()

    def get_unpasted(self) -> list[dict]:
        """Return all entries that were not pasted (potential crash losses).

        Returns:
            List of entry dicts with 'text', 'timestamp', and 'pasted' keys.
        """
        with self._lock:
            return [e for e in self._entries if not e.get("pasted", False)]

    def get_all(self) -> list[dict]:
        """Return all recovery entries.

        Returns:
            List of all entry dicts (copies, safe to modify).
        """
        with self._lock:
            return list(self._entries)

    def check_on_startup(self) -> list[dict] | None:
        """Check for unpasted transcriptions from a previous session.

        Returns a list of unpasted entries if any exist, or None.
        The caller should notify the user about these entries so they
        can recover the text that was lost due to a crash or forced close.

        Returns:
            List of unpasted entry dicts, or None if no unpasted entries.
        """
        # Detect interrupted dictations: if the .dictation-in-flight sentinel
        # exists, the previous process crashed mid-dictation. Emit a
        # dictation_lost event so the renderer can notify the user.
        self._detect_and_notify_lost_dictation()
        unpasted = self.get_unpasted()
        if unpasted:
            log.info("[RECOVERY] Found %d unpasted transcriptions from previous session", len(unpasted))
            return unpasted
        return None

    def _detect_and_notify_lost_dictation(self) -> None:
        """Detect if a dictation was in-flight when the previous process crashed.

        If the ``.dictation-in-flight`` sentinel file exists in the config
        directory, the previous process crashed mid-dictation. Delete the
        sentinel (so it doesn't re-fire on every startup) and emit a
        ``dictation_lost`` event via the event bus so the renderer can
        show a user notification.
        """
        with contextlib.suppress(Exception):
            from voice_typer.server import event_bus
            from voice_typer.server._paths import config_dir as _config_dir

            _sentinel = _config_dir() / ".dictation-in-flight"
            if _sentinel.exists():
                # Delete FIRST so a publish failure can't cause a re-fire loop.
                _sentinel.unlink(missing_ok=True)
                log.warning(
                    "[RECOVERY] Detected interrupted dictation from previous session — emitting dictation_lost event"
                )
                event_bus.publish(
                    {
                        "type": "dictation_lost",
                        "data": {
                            "message": "A dictation was interrupted by a crash. Partial audio may be recoverable.",
                            "recoverable": True,
                        },
                    }
                )

    def clear(self) -> None:
        """Clear all recovery entries after user acknowledgment.

        Removes all stored entries and saves the empty state to disk.
        """
        with self._lock:
            self._entries.clear()
        self._enqueue_save()
        log.info("[RECOVERY] Recovery entries cleared")

    @property
    def count(self) -> int:
        """Number of recovery entries."""
        with self._lock:
            return len(self._entries)

    def __del__(self) -> None:
        """Best-effort flush on garbage collection.

        If the background save thread still has pending writes when
        the CrashRecovery instance is collected, do a synchronous
        final save so the latest state is persisted.  This is a
        safety net — explicit ``shutdown()`` + ``flush()`` is the
        preferred shutdown path, but ``__del__`` catches the case
        where the caller forgets (e.g. tests, abnormal exits).

        a-review Finding A3: previously this only saved if
        ``_save_thread.is_alive() and not _save_queue.empty()`` —
        which skipped the save entirely after ``shutdown()`` killed
        the worker, dropping any post-shutdown mutations on GC.
        Now we save whenever ``_entries`` is non-empty, regardless
        of worker state.  ``_save_lock`` serializes against any
        in-flight worker save.  The whole body stays wrapped in
        try/except so interpreter shutdown never raises from GC.

        XZ-R12-16: the previous ``if self._entries:`` read
        ``_entries`` WITHOUT holding ``_lock``. A concurrent
        ``add()`` could mutate the deque mid-check, leaving the
        GC path reading a stale (empty) view and skipping the
        save — losing the just-added entry. The check now
        acquires ``_lock`` for the boolean read. ``_save_sync``
        re-acquires ``_lock`` internally for the snapshot, so the
        race window between the check and the save is the same
        as before (a concurrent ``add()`` may still miss this
        GC save), but at least the check itself is no longer
        torn.

        XZ-R17-13: ``_final_save_done`` (set by ``_atexit_flush_all``
        after a successful atexit save) makes this ``__del__`` save a
        no-op if atexit already persisted the final state —
        eliminating the redundant atomic-write + rename on the
        shutdown path. Note ``shutdown()``'s final save does NOT
        set the flag (only atexit does), so a post-shutdown
        ``__del__`` save for mutations that bypassed
        ``_enqueue_save`` (e.g. direct ``_entries.append`` in tests)
        still persists (regression-tested by
        ``test_del_saves_unpersisted_post_shutdown_mutations``).
        """
        try:
            # Signal the worker to stop, then do one final
            # synchronous save to capture any pending state.
            self._stopped = True
            # XZ-R12-16: acquire ``_lock`` for the empty-check so a
            # concurrent ``add()`` can't mutate ``_entries`` mid-read.
            # The check is best-effort: even with the lock, a
            # concurrent ``add()`` that arrives AFTER this check
            # releases the lock will race with the GC, but that's
            # inherent to ``__del__`` (the instance is being torn
            # down — concurrent mutations are already UB).
            with self._lock:
                has_entries = bool(self._entries)
            # Save whenever there's any data to lose.  This covers
            # both "worker alive with pending queue items" (worker
            # is mid-save; _save_lock serializes) and "worker dead
            # after shutdown() with post-shutdown mutations" (the
            # Finding A3 regression).  If _entries is empty, this
            # is a no-op (matches the original behavior — saves
            # are only triggered by state changes, not by GC).
            # XZ-R17-13: if ``_atexit_flush_all`` already set
            # ``_final_save_done``, ``_save_sync``'s short-circuit
            # returns immediately — no redundant atomic-write +
            # rename on the shutdown path.
            # AB-44: pass ``durability=True`` for this final GC save
            # (one-time cost, durability guarantee matters there).
            # The per-dictation path uses ``durability=False`` (5+
            # saves/sec under streaming — fsync cost not worth it).
            if has_entries:
                self._save_sync(durability=True)
        except Exception:
            pass  # __del__ must never raise

    def entries_metadata_snapshot(self) -> list[dict]:
        """Return a metadata-only snapshot of the recovery entries.

        Used by the diagnostic bundle export (PROD-010 / CR-39) so
        support engineers can see entry counts + timestamps without
        leaking transcription text. Exposed as a public accessor so
        :mod:`voice_typer.server.diagnostics_export` can read entry
        metadata without reaching into ``_entries`` directly elsewhere
        in the codebase.

        DR-27: this method exists alongside the delegate
        :meth:`create_diagnostic_bundle` so callers that only need
        the metadata (e.g. tests, future telemetry) don't have to
        build a full zip just to inspect entry counts.
        """
        with self._lock:
            return [
                {
                    "timestamp": e.get("timestamp"),
                    "pasted": e.get("pasted", False),
                    "text_length": len(e.get("text", "")),
                }
                for e in self._entries
            ]

    def create_diagnostic_bundle(self) -> str | None:
        """PROD-010: Create a diagnostic bundle zip file.

        Collects:
          - voice-typer.log
          - config.json (redacted — API keys removed)
          - System info (platform, Python version, GPU info)
          - Model info (loaded model, device)
          - Crash recovery entries (metadata only — CR-39)

        Returns the path to the created zip file, or None on failure.

        DR-27: the body of this method was extracted to
        :mod:`voice_typer.server.diagnostics_export` so
        ``crash_recovery.py`` can focus on its core concern (storing /
        flushing / replaying recovery entries). This delegate keeps
        the public API (``cr.create_diagnostic_bundle()``) stable so
        existing callers — ``service.diagnostics.DiagnosticsMixin``,
        tests in ``tests/test_crash_recovery*.py``, the CLI in
        ``scripts/diagnostics.py`` — continue to work unchanged.
        """
        from voice_typer.server.diagnostics_export import create_diagnostic_bundle

        return create_diagnostic_bundle(self)
