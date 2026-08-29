"""CrashRecovery: instance state + public API (package assembly point).

:class:`CrashRecovery` stores the last ``MAX_RECOVERY_ENTRIES``
UNPASTED transcriptions for crash recovery. The persistence
(``_load`` / ``_quarantine_corrupt`` / ``_save_sync``) and save-worker
(``_enqueue_save`` / ``_start_save_thread`` / ``_save_loop`` /
``flush`` / ``shutdown``) concerns live in the sibling mixins
(:mod:`._io`, :mod:`._worker`) and are inherited; this module holds
the constructor, the instance state, and the public/edge-path methods
(add / mark_*/ get_*/ check_on_startup / clear / count / __del__ /
entries_metadata_snapshot / create_diagnostic_bundle) with bodies
moved verbatim from the pre-split ``crash_recovery.py`` monolith.
"""

import collections
import contextlib
import logging
import queue
import threading
from pathlib import Path
from typing import Any

from voice_typer.server.crash_recovery._io import (
    _LEGACY_RECOVERY_FILENAME,
    _SAVE_QUEUE_MAXSIZE,
    MAX_RECOVERY_ENTRIES,
    RECOVERY_FILENAME,
    _RecoveryIO,
)
from voice_typer.server.crash_recovery._worker import _LIVE_INSTANCES, _SaveWorker

# Logger name pinned to the package name (C-LOG-1) — see _worker.py.
log = logging.getLogger("voice_typer.server.crash_recovery")


class CrashRecovery(_SaveWorker, _RecoveryIO):
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
        # One-time migration of the legacy prefixed name
        # (``voice-typer-recovery.json`` → ``recovery.json``). Best-effort —
        # a failed rename falls back to the canonical name on the next
        # write, so the legacy file is never silently clobbered and the
        # migration is idempotent (mirrors the O4 prewarm-status pattern).
        _legacy = config_dir / _LEGACY_RECOVERY_FILENAME
        if _legacy.exists() and not self._path.exists():
            try:
                _legacy.rename(self._path)
                log.debug(
                    "[RECOVERY] migrated legacy %s -> %s",
                    _LEGACY_RECOVERY_FILENAME,
                    RECOVERY_FILENAME,
                )
            except OSError as exc:
                log.debug("[RECOVERY] legacy file migration failed: %s", exc)
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
        # ``_dir_ensured`` guards the per-save ``os.chmod``
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
        # ``_final_save_done`` deduplicates the final
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
        # ``_load()`` is deferred out of ``__init__`` (which runs
        # on the main thread during ``VoiceTyperApp.__init__``) and into
        # ``check_on_startup()`` (which runs on the startup daemon thread
        # via ``StartupSequence.run``) + the read accessors (``get_all``,
        # ``get_unpasted``, ``count``, ``entries_metadata_snapshot``) as
        # a lazy-load fallback. This moves the synchronous disk read
        # off the main thread so it doesn't block the UI/tray critical
        # path. The ``_loaded`` guard (double-checked locking under
        # ``_lock`` in ``_load``) ensures the disk read happens at most
        # once per instance — all subsequent ``_load()`` calls are a
        # cheap boolean check + immediate return.
        self._loaded = False
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

    # ── Persistence (``_load`` / ``_quarantine_corrupt`` / ``_save_sync``)
    # and ── Save worker (``_enqueue_save`` / ``_start_save_thread`` /
    # ``_save_loop`` / ``flush`` / ``shutdown``) methods are inherited
    # unchanged from the ``_RecoveryIO`` / ``_SaveWorker`` mixins.

    # ── Public API ───────────────────────────────────────────────────

    def add(
        self,
        text: str,
        *,
        pasted: bool = False,
        cycle_id: str | None = None,
    ) -> None:
        """Add a transcription to the recovery buffer.

        Keeps only the last MAX_RECOVERY_ENTRIES entries.

        Args:
            text: The transcribed text to store.
            pasted: Whether the text was successfully pasted.
            cycle_id: Optional correlation id for the dictation cycle
                that produced this text. When provided,
                :meth:`_detect_and_notify_lost_dictation` can determine
                whether partial text from THIS specific cycle was saved
                before a hard crash (and is therefore recoverable). When
                ``None`` (the default — backward-compatible with existing
                callers in ``dictation_pipeline`` that don't yet pass a
                cycle_id), the entry is anonymous and won't match any
                cycle-specific lookup.
        """
        from datetime import datetime

        entry: dict = {
            "text": text,
            "timestamp": datetime.now().isoformat(),
            "pasted": pasted,
        }
        if cycle_id is not None:
            entry["cycle_id"] = cycle_id
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

        ``_enqueue_save()`` is now called OUTSIDE the
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

        Lazily loads entries from disk on first access if
        ``check_on_startup()`` hasn't run yet. The ``_loaded`` guard
        makes this a no-op after the first load. In production,
        ``check_on_startup()`` (startup thread) is the primary load
        site; this lazy-load fallback preserves backward compat for
        callers that read before startup completes.
        """
        self._load()
        with self._lock:
            return [e for e in self._entries if not e.get("pasted", False)]

    def get_all(self) -> list[dict]:
        """Return all recovery entries.

        Returns:
            List of all entry dicts (copies, safe to modify).

        Lazily loads entries from disk on first access if
        ``check_on_startup()`` hasn't run yet. The ``_loaded`` guard
        makes this a no-op after the first load.
        """
        self._load()
        with self._lock:
            return list(self._entries)

    def check_on_startup(self) -> list[dict] | None:
        """Check for unpasted transcriptions from a previous session.

        Returns a list of unpasted entries if any exist, or None.
        The caller should notify the user about these entries so they
        can recover the text that was lost due to a crash or forced close.

        Returns:
            List of unpasted entry dicts, or None if no unpasted entries.

        This method (called from ``StartupSequence.run`` on the
        startup daemon thread) is the PRIMARY load site for recovery
        entries. ``_load()`` was moved here from ``__init__`` so the
        synchronous disk read happens on the background thread rather
        than blocking the main thread during ``VoiceTyperApp.__init__``.
        The ``_loaded`` guard in ``_load()`` makes this a no-op if the
        entries were already loaded (e.g. by an earlier read-accessor
        lazy-load call).
        """
        # Load recovery entries from disk. Deferred from
        # __init__ so the disk read happens on this (daemon) thread
        # rather than the main thread. Idempotent via _loaded guard.
        self._load()
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
        directory, the previous process crashed mid-dictation. The sentinel
        contains the ``cycle_id`` of the interrupted dictation (written by
        ``dictation_pipeline._transcribe``). Delete the sentinel (so it
        doesn't re-fire on every startup), then look up that ``cycle_id``
        in the in-memory recovery store:

        • If an unpasted entry with a matching ``cycle_id`` exists, the
          crash was SOFT (a Python exception was caught and
          :meth:`add` was called from the exception handler before the
          process died) — the partial TEXT is recoverable. Set
          ``recoverable: True`` and ``recovery_type: "text_only"``.
        • If no such entry exists, the crash was HARD (SIGKILL / OOM /
          segfault) — the transcription thread never reached the
          exception handler, so no text was saved. Nothing is
          recoverable. Set ``recoverable: False`` and
          ``recovery_type: "none"``.

        AUDIO IS NEVER RECOVERABLE. The audio buffer lives only in
        process memory (see ``dictation_pipeline._transcribe``'s finally
        block — it zero-fills the numpy array after transcription
        completes), and the ``.dictation-in-flight`` sentinel only
        persists the ``cycle_id`` correlation string, never audio
        samples. The previous message ("Partial audio may be
        recoverable") was misleading and is replaced with an accurate
        message that distinguishes soft-crash text recovery from
        hard-crash total loss.
        """
        with contextlib.suppress(Exception):
            from voice_typer.server import event_bus
            from voice_typer.server._paths import config_dir as _config_dir

            _sentinel = _config_dir() / ".dictation-in-flight"
            if _sentinel.exists():
                # Read the cycle_id BEFORE deleting the sentinel so we
                # can look up matching recovery entries.
                # ``dictation_pipeline`` writes ``str(cycle_id)`` to this
                # file when a dictation starts; if the write was partial
                # or the file is empty (e.g. crashed mid-write),
                # ``cycle_id`` will be "" — the lookup below explicitly
                # excludes the empty string so it falls through to the
                # "hard crash, nothing recoverable" branch, which is the
                # correct outcome.
                cycle_id = ""
                with contextlib.suppress(Exception):
                    # HU-10: read through ``_secure_read_text`` (POSIX
                    # ``O_NOFOLLOW`` / Windows reparse-point check) —
                    # same helper the recovery-file load path uses — so
                    # a symlink planted at the sentinel path can never
                    # exfiltrate an arbitrary file's content into the
                    # production log at WARNING. On refusal
                    # (OSError/ValueError) ``cycle_id`` stays "" → the
                    # hard-crash (nothing recoverable) branch below.
                    from voice_typer.server.config import _secure_read_text

                    cycle_id = _secure_read_text(_sentinel).strip()
                # Delete FIRST so a publish failure can't cause a re-fire loop.
                _sentinel.unlink(missing_ok=True)
                # Look up matching unpasted entries. ``_lock`` guards the
                # in-memory ``_entries`` deque against concurrent
                # ``add()`` / ``mark_pasted()`` mutations during the scan.
                # The ``bool(cycle_id)`` guard ensures a missing / blank
                # sentinel (hard crash mid-write) is treated as
                # unrecoverable rather than matching an unrelated entry
                # that also lacks a ``cycle_id`` field.
                with self._lock:
                    recoverable = any(
                        bool(cycle_id) and e.get("cycle_id") == cycle_id and not e.get("pasted", False)
                        for e in self._entries
                    )
                recovery_type = "text_only" if recoverable else "none"
                log.warning(
                    "[RECOVERY] Detected interrupted dictation from previous session "
                    "(cycle_id=%r) — emitting dictation_lost event "
                    "(recoverable=%s, recovery_type=%s)",
                    cycle_id,
                    recoverable,
                    recovery_type,
                )
                event_bus.publish(
                    {
                        "type": "dictation_lost",
                        "data": {
                            "message": (
                                "A dictation was interrupted by a crash. "
                                "Partial text may be recoverable if the crash "
                                "was soft; no audio is recoverable."
                            ),
                            "recoverable": recoverable,
                            "recovery_type": recovery_type,
                            "cycle_id": cycle_id,
                        },
                    }
                )

    def clear(self) -> None:
        """Clear all recovery entries after user acknowledgment.

        Removes all stored entries and saves the empty state to disk.
        """
        with self._lock:
            self._entries.clear()
            # Mark as loaded so a subsequent lazy ``_load()`` (from
            # ``count`` / ``get_all`` / ``get_unpasted`` / a second
            # ``check_on_startup``) does NOT re-read the stale on-disk
            # entries and resurrect the just-cleared state. Without the
            # flag, a ``clear()`` that ran before the first ``_load()``
            # would see the disk file still holding the entries and
            # repopulate them — breaking the "cleared after
            # acknowledgment" contract.
            self._loaded = True
        self._enqueue_save()
        log.info("[RECOVERY] Recovery entries cleared")

    @property
    def count(self) -> int:
        """Number of recovery entries.

        Lazily loads entries from disk on first access if
        ``check_on_startup()`` hasn't run yet. The ``_loaded`` guard
        makes this a no-op after the first load.
        """
        self._load()
        with self._lock:
            return len(self._entries)

    def __del__(self) -> None:
        """Best-effort GC-time flush — delegates to ``_cleanup_*`` helpers.

        Defense-in-depth outer ``try/except`` preserves the original
        "never raise from GC" contract; each helper also owns its
        own ``try/except`` so one failure does not skip the rest.
        See ``_cleanup_flush_pending`` for the 3-tier safety-net
        rationale (``shutdown()`` + atexit + ``__del__``).
        """
        try:
            self._cleanup_signal_stop()
            self._cleanup_flush_pending()
        except BaseException:
            # Defense-in-depth: each helper owns its own try/except,
            # but this outer catch preserves the "never raise from GC"
            # contract if a helper is replaced with a broken stub.
            # Full ``BaseException`` rationale in ``_cleanup_flush_pending``.
            pass

    def _cleanup_signal_stop(self) -> None:
        """``__del__`` helper: signal the background worker to stop.

        Sets ``self._stopped = True`` so any subsequent ``_save_loop``
        iteration exits cleanly. Idempotent (``shutdown()`` also sets
        this at line ~849). Wrapped in its own ``try/except
        BaseException`` so a failure here does not prevent
        ``_cleanup_flush_pending`` from running — the pre-extraction
        ``__del__`` wrapped everything in ONE ``try/except``, so a
        failure in ``self._stopped = True`` would have skipped the
        final save. Extraction isolates the failure (slight resilience
        upgrade, not a downgrade — the original behavior is preserved
        for the success path; only the failure path is improved).
        """
        try:  # noqa: SIM105 — explicit try/except preserves the
            #       "never raise from GC" pattern's visual parity with
            #       ``__del__`` and ``_cleanup_flush_pending`` (both of
            #       which use the same try/except BaseException: pass
            #       shape, just with substantial bodies so SIM105
            #       doesn't fire on them). ``contextlib.suppress``
            #       would also work but obscures the BaseException
            #       catch that's the whole point of this helper.
            # Signal the worker to stop. ``shutdown()`` already sets
            # this on the explicit-shutdown path; the redundant set
            # here is the GC-time safety net for the
            # ``shutdown()``-was-never-called case.
            self._stopped = True
        except BaseException:
            # ``__del__``-path helpers must NEVER raise — see
            # ``_cleanup_flush_pending`` for the full ``BaseException``
            # rationale (``KeyboardInterrupt`` / ``SystemExit`` /
            # ``GeneratorExit`` during interpreter shutdown).
            pass

    def _cleanup_flush_pending(self) -> None:
        """``__del__`` helper: synchronously save any pending state.

        If ``_entries`` is non-empty, call ``_save_sync(durability=True)``
        to persist the latest state. This is the safety-net save that
        catches the case where the caller forgot ``shutdown()`` +
        ``flush()`` (e.g. tests, abnormal exits).

        a-review Finding A3: previously ``__del__`` only saved if
        ``_save_thread.is_alive() and not _save_queue.empty()`` —
        which skipped the save entirely after ``shutdown()`` killed
        the worker, dropping any post-shutdown mutations on GC.
        Now we save whenever ``_entries`` is non-empty, regardless
        of worker state.  ``_save_lock`` serializes against any
        in-flight worker save.

        the previous ``if self._entries:`` read
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

        ``_final_save_done`` (set by ``_atexit_flush_all``
        after a successful atexit save) makes this save a
        no-op if atexit already persisted the final state —
        eliminating the redundant atomic-write + rename on the
        shutdown path. Note ``shutdown()``'s final save does NOT
        set the flag (only atexit does), so a post-shutdown
        ``__del__`` save for mutations that bypassed
        ``_enqueue_save`` (e.g. direct ``_entries.append`` in tests)
        still persists (regression-tested by
        ``test_del_saves_unpersisted_post_shutdown_mutations``).

        Design decision: ``__del__`` is INTENTIONALLY
        retained as the third safety-net tier alongside
        ``shutdown()`` and atexit. The original fix proposal
        ("Remove ``__del__`` entirely — atexit is documented safety
        net") was rejected because: (a) atexit does NOT fire for
        non-Python-initiated exits (SIGKILL, segfault, os._exit) —
        ``__del__`` is the only path that catches the
        ``shutdown()``-was-never-called case under those exits; (b)
        the ``_final_save_done`` dedup (checked inside ``_save_sync``
        under ``_save_lock``) makes the redundant-write cost zero
        when atexit already fired — the only remaining cost is the
        ``_lock`` acquisition + ``bool(self._entries)`` read, which
        is negligible; (c) removing ``__del__`` would regress
        ``test_del_saves_unpersisted_post_shutdown_mutations``
        (post-shutdown direct ``_entries.append`` would no longer
        persist on GC). The 3-tier contract is documented here and
        in ``_atexit_flush_all`` / ``shutdown``.
        """
        try:
            # acquire ``_lock`` for the empty-check so a
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
            # if ``_atexit_flush_all`` already set
            # ``_final_save_done``, ``_save_sync``'s short-circuit
            # returns immediately — no redundant atomic-write +
            # rename on the shutdown path.
            # pass ``durability=True`` for this final GC save
            # (one-time cost, durability guarantee matters there).
            # The per-dictation path uses ``durability=False`` (5+
            # saves/sec under streaming — fsync cost not worth it).
            if has_entries:
                self._save_sync(durability=True)
        except BaseException:
            # ``__del__`` must NEVER raise — including for
            # ``BaseException`` subclasses (``KeyboardInterrupt``,
            # ``SystemExit``, ``GeneratorExit``) that ``except Exception:``
            # would NOT catch. A ``KeyboardInterrupt`` raised during
            # interpreter shutdown while ``__del__`` is mid-save would
            # otherwise propagate out of GC, which can crash the
            # interpreter or leave dangling state. Catching
            # ``BaseException`` (rather than just ``Exception``) honors
            # the documented "never raise" contract in full.
            pass

    def entries_metadata_snapshot(self) -> list[dict]:
        """Return a metadata-only snapshot of the recovery entries.

        Used by the diagnostic bundle export ( / ) so
        support engineers can see entry counts + timestamps without
        leaking transcription text. Exposed as a public accessor so
        :mod:`voice_typer.server.diagnostics_export` can read entry
        metadata without reaching into ``_entries`` directly elsewhere
        in the codebase.

        this method exists alongside the delegate
        :meth:`create_diagnostic_bundle` so callers that only need
        the metadata (e.g. tests, future telemetry) don't have to
        build a full zip just to inspect entry counts.

        Lazily loads entries from disk on first access if
        ``check_on_startup()`` hasn't run yet. The ``_loaded`` guard
        makes this a no-op after the first load.
        """
        self._load()
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
        """Create a diagnostic bundle zip file.

        Collects:
          - voice-typer.log
          - config.json (redacted — API keys removed)
          - System info (platform, Python version, GPU info)
          - Model info (loaded model, device)
          - Crash recovery entries (metadata only — )

        Returns the path to the created zip file, or None on failure.

        the body of this method was extracted to
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
