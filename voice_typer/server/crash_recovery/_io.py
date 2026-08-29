"""Crash-recovery disk-I/O concern: load, quarantine, synchronous save.

Mixin :class:`_RecoveryIO` provides ``_load``, ``_quarantine_corrupt``
and ``_save_sync`` (bodies moved verbatim from the pre-split
``crash_recovery.py`` monolith); :class:`CrashRecovery
<voice_typer.server.crash_recovery._store.CrashRecovery>` inherits
them.

``_secure_atomic_write`` is still imported at FACADE module load time
(``voice_typer/server/crash_recovery/__init__.py``) rather than lazily
here — the interpreter-shutdown rationale from the pre-split module
still applies: the import machinery can be partially dismantled during
``__del__``/atexit saves, so the call site resolves the function
through the already-loaded facade module object (``_facade``) instead
of executing any import at call time. Patch-target contract: tests
that monkeypatch ``voice_typer.server.crash_recovery._secure_atomic_write``
rebind the facade attribute, and the call-time facade read below makes
that rebinding visible exactly as when the code lived in one module.
"""

import collections
import json
import logging
import os

# ``_facade`` is a bound reference to the partially-initialized package
# module at import time; by call time the package is fully initialized.
from voice_typer.server import crash_recovery as _facade

# Canonical constants. Defined HERE (the persistence-concern module) and
# re-exported by the package ``__init__`` so
# ``voice_typer.server.crash_recovery.RECOVERY_FILENAME`` and friends
# keep resolving exactly as before the split (``_user_data_files.py``
# imports ``RECOVERY_FILENAME`` from the package for its drift check).
RECOVERY_FILENAME = "recovery.json"
_LEGACY_RECOVERY_FILENAME = "voice-typer-recovery.json"
MAX_RECOVERY_ENTRIES = 10

# Persistence role: ``recovery.json`` is an ACTIVE crash-recovery store
# for the last ``MAX_RECOVERY_ENTRIES`` UNPASTED transcriptions. It is
# NOT obsolete: the dictation pipeline calls ``CrashRecovery.add()``
# (gated by ``config.crash_recovery_enabled``), and startup
# (``startup_sequence`` → ``check_on_startup``) reads it to notify the
# user of recovered text; it is also exported to diagnostics bundles.
# An empty ``{"entries": []}`` is the NORMAL state (nothing pending),
# not a signal that the file can be removed.

# Bounded queue: if the worker falls behind (e.g. disk is slow),
# drop the oldest pending save rather than blocking the transcription
# thread.  The latest state is what matters; intermediate states are
# not useful for crash recovery.
_SAVE_QUEUE_MAXSIZE = 32

# Logger name pinned to the package name (C-LOG-1) — see _worker.py.
log = logging.getLogger("voice_typer.server.crash_recovery")


class _RecoveryIO:
    """Mixin: disk load/quarantine/save for :class:`CrashRecovery`."""

    def _load(self) -> None:
        """Load recovery entries from disk.

        ``_load()`` is now called lazily from
        :meth:`check_on_startup` (which runs on the startup daemon
        thread) and from the read accessors (``get_all``, ``get_unpasted``,
        ``count``, ``entries_metadata_snapshot``) rather than eagerly
        from ``__init__``. This defers the synchronous disk read off
        the main thread so it doesn't block the UI/tray critical path
        during ``VoiceTyperApp.__init__``. In production,
        ``check_on_startup()`` (on the startup thread) is the primary
        load site; the read-accessor calls are a backward-compat
        fallback for callers that read before startup completes.

        The ``_loaded`` guard (double-checked locking under ``_lock``)
        ensures the disk read happens at most once per instance —
        subsequent calls are a cheap boolean check + immediate return.
        ``_loaded`` is set to ``True`` even on failure (corrupt file,
        symlink, OSError) so a transient disk issue doesn't cause a
        retry storm on every subsequent read; the ``_entries`` deque
        is reset to empty on failure, matching the prior behavior.

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
        # Fast-path guard (no lock). If already loaded, return
        # immediately. The boolean read is atomic on CPython; a
        # concurrent caller may also see False and proceed to the
        # locked re-check below (double-checked locking pattern).
        if self._loaded:
            return
        with self._lock:
            # Re-check under the lock so only one thread does the
            # actual disk read. ``_lock`` also guards ``_entries``
            # against concurrent ``add()`` / ``mark_pasted()`` so the
            # deque assignment below is race-free.
            if self._loaded:
                return
            # If ``_entries`` already has data (from ``add()``
            # or a direct mutation in tests), do NOT load from disk —
            # the in-memory data is more current and loading would
            # clobber it. Mark as loaded so future calls skip the
            # check entirely. In production, ``check_on_startup()``
            # runs before any ``add()``, so this branch is only hit
            # in tests or edge cases where mutations happen before
            # the first read.
            if len(self._entries) > 0:
                self._loaded = True
                return
            if not self._path.exists():
                self._entries = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
                self._loaded = True
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
                self._loaded = True
                log.debug("[RECOVERY] Loaded %d entries", len(self._entries))
            except Exception as exc:
                log.warning("[RECOVERY] Failed to load: %s", exc)
                # Quarantine the corrupt file so the next save creates a
                # fresh one.  Best-effort — failures are logged and
                # swallowed so _load always resets _entries cleanly.
                self._quarantine_corrupt()
                self._entries = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
                # Mark as loaded even on failure so a transient disk
                # issue doesn't cause a retry on every subsequent
                # read. _entries is reset to empty, matching
                # the prior failure behavior.
                self._loaded = True

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

    def _save_sync(self, *, durability: bool = False, set_final_save_done: bool = False) -> None:
        """Save recovery entries to disk synchronously.

        This is called only from the background save thread.  All
        other callers go through ``_enqueue_save()``.

        on POSIX, restricts file permissions to 0o600 so
        transcription text in the recovery file is not world-readable.

        uses the shared _secure_atomic_write which applies
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

        ``_dir_ensured`` guards the per-save
        ``os.chmod(self._path.parent, 0o700)``. The chmod is
        idempotent but it's still a syscall per transcription; the
        flag is set after the first successful mkdir+chmod and
        subsequent saves skip it. If the chmod fails (logged at
        warning), the flag is NOT set so the next save retries.

        ``_dir_ensured`` now ALSO gates the per-save ``mkdir``
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

        ``_final_save_done`` deduplicates the final
        shutdown save. ``__del__`` and the atexit handler both call
        ``_save_sync()`` during interpreter shutdown; the flag
        (guarded by ``_save_lock``) makes the second call a no-op
        so the atomic-write + rename happens exactly once on the
        shutdown path. The flag is set ONLY by ``_atexit_flush_all``
        (NOT by ``shutdown()`` or ``__del__``) — so ``shutdown()``'s
        final save does NOT suppress a subsequent ``__del__`` save
        for post-shutdown mutations. The flag is reset to ``False``
        by ``_enqueue_save`` when a new mutation arrives post-shutdown.

        ``set_final_save_done`` (default ``False``)
        atomically sets ``_final_save_done = True`` INSIDE
        ``_save_lock`` after a successful write. Passed as ``True``
        ONLY by the atexit path (via ``_run_save_with_timeout``) so
        the flag-set happens before any concurrent worker thread
        (blocked on ``_save_lock`` waiting for the atexit-save to
        release) can observe ``_final_save_done = False`` and write
        the file again — the redundant-write race that
        ``test_del_skips_when_atexit_already_saved`` reproduces
        order-dependently under xdist. ``shutdown()``, ``__del__``,
        the worker thread, and ``_enqueue_save``'s sync fallback all
        pass the default ``False`` so the design invariant ("only
        atexit sets the flag") holds; ``_atexit_flush_all`` ALSO sets
        the flag outside the lock after ``_run_save_with_timeout``
        returns so the test's ``assert _final_save_done is True``
        passes even when the atexit-save times out (the
        still-blocked atexit-save ``_worker`` thread eventually
        acquires the lock, reads ``True``, returns without writing).

        the lock acquisition is now INSIDE a top-level
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
                # short-circuit if the atexit handler already
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
                    # skip the mkdir on subsequent saves. The flag
                    # is set after the first successful mkdir+chmod; later
                    # saves only do the atomic write. The flag is cleared
                    # on any mkdir/chmod failure so the next save retries.
                    if not self._dir_ensured:
                        self._path.parent.mkdir(parents=True, exist_ok=True)
                        # skip the chmod on subsequent saves.
                        # First save does the mkdir + chmod; later saves
                        # only do the atomic write. The flag is cleared on
                        # any chmod failure so the next save retries.
                        if not _facade.is_windows():
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
                    # durability=False (default) for the per-dictation
                    # path.  The atexit handler and __del__ may pass
                    # durability=True for the final shutdown save.
                    # Facade attribute read at call time: keeps the
                    # load-time-import guarantee (no import machinery
                    # during interpreter shutdown) AND makes
                    # monkeypatching
                    # ``crash_recovery._secure_atomic_write`` visible,
                    # exactly as when this code lived in one module.
                    _facade._secure_atomic_write(self._path, snapshot, durability=durability)
                    # When called from the atexit path
                    # (``set_final_save_done=True``), set the flag
                    # INSIDE ``_save_lock`` after the successful write
                    # so a concurrent worker thread blocked on
                    # ``_save_lock`` (waiting for the atexit-save to
                    # release) observes ``_final_save_done = True`` and
                    # short-circuits instead of redundantly re-writing
                    # the file. Without this, there's a race window
                    # between the atexit-save releasing the lock and
                    # ``_atexit_flush_all`` setting the flag outside
                    # the lock — the worker thread can win that race
                    # (it's directly waiting on the lock acquire,
                    # while the test thread has to wake from
                    # ``done.wait()``, return from
                    # ``_run_save_with_timeout``, then reach the
                    # flag-set line). Reproduces order-dependently under
                    # xdist in ``test_del_skips_when_atexit_already_saved``.
                    #
                    # NOTE — the flag is NOT set here for the DEFAULT
                    # (``set_final_save_done=False``) callers. Only
                    # ``_atexit_flush_all`` (via
                    # ``_run_save_with_timeout``) passes ``True``. This
                    # ensures:
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
                    if set_final_save_done:
                        self._final_save_done = True
                except Exception:
                    log.exception("[RECOVERY] Failed to save")
        except Exception:
            # lock-acquisition failure (or any other exception
            # escaping the inner try/except). Log and swallow so the
            # caller (the worker thread, ``shutdown()``, ``__del__``,
            # ``_atexit_flush_all``) is not crashed by a save failure.
            log.exception(
                "[RECOVERY] _save_sync top-level failure (lock acquisition or "
                "unexpected error); the recovery file may not be persisted"
            )
