"""Per-correction usage tracking for the custom vocabulary feature.

Counts how often each vocabulary correction actually fires during real
dictation (``VocabularyManager.apply_to_text``) plus how many
dictations were completed, so:

  - the Vocabulary page can show a real "used Nx" / "last triggered"
    per entry instead of flagging the metric as unavailable, and
  - the Analytics page can show a corrections-applied rate
    (corrections ÷ dictations) from the authoritative engine pass.

Data model (``correction-usage.json`` in the config dir, version 1)::

    {
      "version": 1,
      "entries": {
        "misspellings": {
          "recieve": { "count": 7, "last_ts": 1723800000.0 }
        }
      },
      "corrections_by_day": { "2026-08-16": 4 },
      "dictations_by_day": { "2026-08-16": 2 }
    }

  - ``entries``  — per (category, original) cumulative count + last
    trigger timestamp. Powers "used Nx" on the Vocabulary page.
  - ``corrections_by_day`` / ``dictations_by_day`` — per-local-calendar-
    day totals, keyed the same way the renderer buckets history
    (``YYYY-MM-DD`` in LOCAL time, matching ``localDateKey`` in the
    dashboard lib). Powers the range-aware corrections rate on the
    Analytics page.

Design notes:

  - In-memory increments are batched and persisted by a background
    daemon sweeper thread once the debounce window (``FLUSH_INTERVAL_S``)
    has elapsed, plus a forced ``flush()`` on read (``get_snapshot``) so
    the snapshot the UI is served is always current and the on-disk
    file is never more than a few seconds stale. The dictation path
    (``record_corrections`` / ``record_dictation`` — called between
    "transcription done" and "text pasted") therefore only mutates an
    in-memory dict under a lock and signals the sweeper; it never
    performs file I/O. A crash loses at most the last few seconds of
    increments — acceptable for usage statistics.
  - Writes go through :class:`PersistedJSON` (atomic replace + .bak +
    quarantine), the same durability layer the vocabulary itself uses.
  - Day totals are pruned after ``KEEP_DAYS`` so the file stays
    bounded even for long-lived installs.
  - ``prune_entries`` drops usage records for corrections that no
    longer exist in the vocabulary (called from the save path), so
    deleting a correction also removes its counter.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import weakref
from datetime import datetime
from pathlib import Path
from typing import Any

from voice_typer.server.secure_file_io import PersistedJSON

log = logging.getLogger(__name__)

CORRECTION_USAGE_FILENAME = "correction-usage.json"
USAGE_SCHEMA_VERSION = 1

# Persistence ownership (do NOT merge into vocabulary.json):
# ``correction-usage.json`` is INDEPENDENT ANALYTICS / time-series data —
# per-(category, original) cumulative counts + per-local-day correction /
# dictation totals that feed the Vocabulary page's "used Nx" and the
# Analytics page's corrections-applied rate. It is NOT vocabulary data:
# it has a different lifecycle (batched debounced writes, 90-day prune,
# prune-on-delete from the save path) and a different producer (the
# dictation engine, via ``record_corrections`` / ``record_dictation``).
# The vocabulary's own authoritative user store is ``vocabulary.json``
# (VocabularyManager); the two files must stay separate.

# Keep per-day totals for at most this many days (bounded file growth).
KEEP_DAYS = 90
# Batched writes: flush at most this often while dirty.
FLUSH_INTERVAL_S = 5.0

# Live-tracker registry + shared flush sweeper (single daemon thread
# for ALL tracker instances). The sweeper owns the debounced flush so
# the dictation hot path never pays for json.dumps + the atomic write
# + .bak rotation. ONE thread per process (not one per tracker) keeps
# the thread count O(1): trackers are created per VocabularyManager,
# and the suite constructs many of them — per-instance threads would
# accumulate for the process lifetime (the failure mode documented on
# the crash-recovery worker: accumulated daemon threads eventually trip
# a native thread limit on Windows). A WeakSet drops GC'd trackers, so
# test-instance churn self-cleans; the thread itself is a daemon and is
# reaped by the interpreter at exit.
_LIVE_TRACKERS: weakref.WeakSet[CorrectionUsageTracker] = weakref.WeakSet()
_LIVE_TRACKERS_LOCK = threading.Lock()
_sweeper_wake = threading.Event()
# Generation counter guarding the stop/restart race: every sweeper thread
# is bound to the generation it was started under and exits as soon as the
# live generation moves past it. Both ``stop_flush_sweeper`` and a restart
# in ``_ensure_flush_sweeper`` bump the counter, so a stale thread that
# survives a timed-out stop (e.g. still inside one slow save pass) can
# never keep looping alongside its replacement — at most ONE sweeper loop
# is live at any time. (A stale thread may briefly finish its in-flight
# pass concurrently with the replacement: safe, because per-tracker
# flushes serialize on the tracker's instance lock and re-check
# ``_dirty``.) Replaces a plain stop-flag Event, which a restart could
# clear while the stale thread was mid-pass — resurrecting a second
# permanent loop.
_sweeper_generation = 0
_sweeper_thread: threading.Thread | None = None

_DICT_CATEGORIES = ("misspellings", "technical_terms", "names", "products")
_LIST_CATEGORIES = ("phrase_corrections", "extra_word_patterns")


def _day_key(ts: float) -> str:
    """Local-calendar ``YYYY-MM-DD`` key for a unix timestamp.

    Deliberately LOCAL time (not UTC) so the server's day buckets match
    the renderer's ``localDateKey`` — the Analytics page joins the
    per-day maps to its range window by string key.
    """
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class CorrectionUsageTracker:
    """Thread-safe, batched-persist counter for vocabulary corrections."""

    def __init__(self, config_dir: Path | str):
        self._store = PersistedJSON(
            Path(config_dir) / CORRECTION_USAGE_FILENAME,
            default={},
        )
        self._lock = threading.Lock()
        # Serializes each tracker's snapshot→save pair: one save at a
        # time per tracker, AND the payload snapshot is taken while this
        # lock is held (see ``flush``) — so snapshot order equals save
        # order and a slower older payload can never land AFTER a newer
        # one. Deliberately separate from ``_lock``: the instance lock
        # covers only the in-memory snapshot, so a dictation record
        # never waits on a disk write.
        self._save_lock = threading.Lock()
        self._data: dict[str, Any] = self._store.load()
        if not isinstance(self._data, dict) or self._data.get("version") != USAGE_SCHEMA_VERSION:
            self._data = {
                "version": USAGE_SCHEMA_VERSION,
                "entries": {},
                "corrections_by_day": {},
                "dictations_by_day": {},
            }
        self._dirty = False
        self._last_flush = 0.0
        _LIVE_TRACKERS.add(self)

    # ── Recording ───────────────────────────────────────────────────

    def record_corrections(
        self,
        hits: list[tuple[str, str, int]],
        ts: float | None = None,
    ) -> None:
        """Record correction firings from one ``apply_to_text`` pass.

        ``hits`` is an iterable of ``(category, original, count)``
        triples where ``count`` is the number of substitutions applied
        for that entry in this call (a phrase can fire multiple times
        in one text). Merged per (category, original) in memory and
        persisted on the debounce.
        """
        if not hits:
            return
        ts = ts if ts is not None else time.time()
        day = _day_key(ts)
        with self._lock:
            entries = self._data.setdefault("entries", {})
            by_day = self._data.setdefault("corrections_by_day", {})
            # A hand-edited or partially-written file can leave a
            # non-dict bucket; skip it rather than letting one corrupt
            # key kill usage tracking for the whole session (the caller
            # swallows the exception, so the failure would be silent).
            if not isinstance(entries, dict) or not isinstance(by_day, dict):
                return
            for cat, original, count in hits:
                if not cat or not original or count <= 0:
                    continue
                cat_entries = entries.setdefault(cat, {})
                if not isinstance(cat_entries, dict):
                    continue
                cur = cat_entries.get(original)
                if isinstance(cur, dict):
                    cur["count"] = int(cur.get("count", 0)) + count
                    cur["last_ts"] = ts
                else:
                    cat_entries[original] = {"count": count, "last_ts": ts}
                by_day[day] = int(by_day.get(day, 0)) + count
            self._dirty = True
        self._schedule_flush()

    def record_dictation(self, ts: float | None = None) -> None:
        """Record one completed dictation (denominator for the rate)."""
        ts = ts if ts is not None else time.time()
        day = _day_key(ts)
        with self._lock:
            by_day = self._data.setdefault("dictations_by_day", {})
            if not isinstance(by_day, dict):
                return
            by_day[day] = int(by_day.get(day, 0)) + 1
            self._dirty = True
        self._schedule_flush()

    # ── Reads ───────────────────────────────────────────────────────

    def get_snapshot(self) -> dict[str, object]:
        """Return a deep copy of the usage data (IPC read path)."""
        with self._lock:
            snapshot = copy.deepcopy(self._data)
        # Flush any pending increments so the on-disk file reflects
        # what we just served (crash-safe for a subsequent restart).
        self.flush()
        return snapshot

    # ── Maintenance ─────────────────────────────────────────────────

    def prune_entries(self, vocab_data: dict[str, Any] | None) -> None:
        """Drop usage records for corrections no longer in *vocab_data*.

        Called from the vocabulary save path with the FULL merged
        category payload, so deleting a correction also removes its
        counter (the usage file can't grow with dead entries forever).
        """
        if not vocab_data:
            return
        present: set[tuple[str, str]] = set()
        for cat, raw in vocab_data.items():
            if cat in _DICT_CATEGORIES and isinstance(raw, dict):
                for key in raw:
                    present.add((cat, key))
            elif cat in _LIST_CATEGORIES and isinstance(raw, list):
                for item in raw:
                    if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[0], str):
                        present.add((cat, item[0]))
        with self._lock:
            entries = self._data.get("entries")
            if not isinstance(entries, dict):
                return
            for cat, cat_entries in list(entries.items()):
                if not isinstance(cat_entries, dict):
                    continue
                for original in list(cat_entries.keys()):
                    if (cat, original) not in present:
                        del cat_entries[original]
                if not cat_entries:
                    del entries[cat]
            self._dirty = True
        self._schedule_flush()

    def flush(self) -> None:
        """Force a write of the current in-memory state.

        Called from :meth:`get_snapshot` (IPC read path — keeps snapshot
        reads consistent with the in-memory counters) and by the shared
        sweeper thread once the debounce window has elapsed. Safe to
        call from any thread; concurrent callers serialize on the
        instance lock and the ``_dirty`` flag makes redundant calls
        no-ops.

        Locking shape — ``_save_lock`` is acquired FIRST, the snapshot
        is taken UNDER the instance lock INSIDE it, and the write
        itself runs on ``_save_lock`` but OUTSIDE ``_lock``: the
        store's ``save`` runs ``json.dumps`` on the payload it is
        handed, which must therefore be a private deep copy —
        serializing the LIVE dict while a recorder mutates it would
        either raise (dict changed size during iteration) or persist a
        torn state. Holding ``_lock`` across the whole save would
        instead stall a dictation record for one full save duration;
        with this shape a record landing during a slow save only
        mutates memory (still authoritative) and re-arms ``_dirty`` for
        the next debounce — nothing is lost, nothing on the dictation
        path blocks. Because the snapshot is taken while ``_save_lock``
        is held, snapshot ORDER equals write ORDER: a flusher queued
        behind an in-flight save cannot snapshot until that save has
        landed, so a newer payload can never be overwritten on disk by
        an older one (snapshot-then-write was previously two separate
        critical sections, which allowed exactly that inversion).
        Lock order is always ``_save_lock`` → ``_lock`` — no path takes
        them the other way round (recorders take only ``_lock``, and
        ``get_snapshot`` releases ``_lock`` before calling ``flush``),
        so no lock cycle exists. ``_last_flush`` is advanced at
        snapshot time, so the debounce window spaces SAVE STARTS at
        least ``FLUSH_INTERVAL_S`` apart (the window also throttles
        retries after a failed save).

        Every ``flush()`` call also acts as a barrier on ``_save_lock``
        even when it has nothing to write: it returns only after any
        save already in flight has completed. That preserves the
        read-path guarantee the lock-across-save shape used to provide —
        when ``get_snapshot`` returns, the on-disk file reflects
        everything pending at read time (or newer), so a crash right
        after a snapshot read loses nothing that was just served. The
        only residual gap is a save queued in the microseconds between
        the dirty check and the lock acquisition; it lands immediately
        after, keeping the file within the few-seconds staleness bound
        documented in the module header. Only ``flush()`` callers pay
        this wait; recorders never enter it.
        """
        with self._save_lock:
            with self._lock:
                payload: dict[str, Any] | None = None
                if self._dirty:
                    self._prune_days()
                    payload = copy.deepcopy(self._data)
                    self._dirty = False
                    self._last_flush = time.time()
            if payload is None:
                return
            try:
                self._store.save(payload, durability=False)
            except Exception:
                # Keep `_dirty` so the next debounce/flush retries; the
                # already-advanced `_last_flush` throttles the retry to
                # one attempt per window (no disk hammering).
                log.exception("[USAGE] Failed to save correction usage")
                with self._lock:
                    self._dirty = True

    def _schedule_flush(self) -> None:
        """Signal the shared sweeper that a flush may be due.

        Replaces the previous inline ``_maybe_flush``: the dictation
        path must never block on json.dumps + the atomic file save, so
        the debounce decision runs on the sweeper thread instead. The
        wake event is process-wide (one sweeper serves every tracker),
        and setting it is a no-op when the sweeper is already awake.
        """
        _ensure_flush_sweeper()
        _sweeper_wake.set()

    def _flush_if_due(self, now: float) -> float | None:
        """Flush when the debounce window has elapsed (sweeper-only).

        Returns ``None`` when there is nothing pending or a flush was
        performed; otherwise returns the seconds remaining until the
        pending increments come due, so the sweeper can sleep exactly
        until the next flush time instead of polling.
        """
        with self._lock:
            if not self._dirty:
                return None
            remaining = FLUSH_INTERVAL_S - (now - self._last_flush)
            if remaining > 0:
                return remaining
        # flush() re-checks ``_dirty`` under the lock, so a concurrent
        # forced flush (e.g. get_snapshot) that landed between the check
        # above and here degrades to a cheap no-op.
        self.flush()
        return None

    def _prune_days(self) -> None:
        cutoff = _day_key(time.time() - KEEP_DAYS * 86400)
        for key in ("corrections_by_day", "dictations_by_day"):
            by_day = self._data.get(key)
            if isinstance(by_day, dict):
                for day in [d for d in by_day if d < cutoff]:
                    del by_day[day]


# ── Shared flush sweeper ────────────────────────────────────────────


def _ensure_flush_sweeper() -> None:
    """Start the shared flush-sweeper daemon thread (idempotent).

    Started lazily on the first recorded increment — a tracker that
    never records never needs the thread. Restarts the thread if it
    somehow died, so a sweeper crash can never permanently disable
    debounced persistence.
    """
    global _sweeper_generation, _sweeper_thread
    with _LIVE_TRACKERS_LOCK:
        if _sweeper_thread is not None and _sweeper_thread.is_alive():
            return
        # Bind the new thread to the generation it starts under. Bumping
        # here (not only in ``stop_flush_sweeper``) is what closes the
        # stop→restart race: a record landing between a stop and this
        # restart starts a replacement while the old thread may still be
        # inside one save pass — the old thread sees the generation bump
        # at its next loop check and exits instead of looping forever
        # beside the replacement.
        _sweeper_generation += 1
        generation = _sweeper_generation
        _sweeper_thread = threading.Thread(
            target=_flush_sweeper_loop,
            args=(generation,),
            name="correction-usage-flusher",
            daemon=True,
        )
        _sweeper_thread.start()


def _sweep_live_trackers(now: float) -> float | None:
    """Flush every live tracker whose debounce window has elapsed.

    Returns the seconds until the earliest still-pending due time (so
    the sweeper can wake exactly then), or ``None`` when no tracker has
    pending increments. Per-tracker failures are contained: a broken
    ``PersistedJSON`` save is already logged inside ``flush()`` and must
    never kill the sweeper for the other trackers.
    """
    earliest: float | None = None
    for tracker in list(_LIVE_TRACKERS):
        try:
            remaining = tracker._flush_if_due(now)
        except Exception:
            log.exception("[USAGE] Flush sweeper pass failed for a tracker")
            continue
        if remaining is not None and (earliest is None or remaining < earliest):
            earliest = remaining
    return earliest


def _flush_sweeper_loop(my_generation: int) -> None:
    """Background sweeper: persist debounced usage increments.

    Bound to the generation it was started under: the loop exits as soon
    as the live ``_sweeper_generation`` moves past ``my_generation`` (a
    stop, or a restart that started a replacement thread), so a stale
    thread can never keep sweeping alongside its successor.

    Owns the debounced flush that used to run INLINE on the dictation
    path (between "transcription done" and "text pasted") — the caller
    now only mutates memory and signals ``_sweeper_wake``. Wake-ups:

    * ``_sweeper_wake`` — set by ``_schedule_flush`` when new increments
      land, so a window that has already elapsed flushes immediately
      (same timing as the old inline flush, minus the disk I/O on the
      caller's thread);
    * the computed timeout — sleeps exactly until the earliest pending
      tracker's debounce horizon, bounded by ``FLUSH_INTERVAL_S`` when
      idle so the loop re-checks live trackers periodically.

    Exit exceptions (``KeyboardInterrupt`` / ``SystemExit`` /
    ``GeneratorExit``) propagate so the daemon dies cleanly during
    interpreter shutdown; every other error is logged and the loop
    continues (mirrors the crash-recovery save worker).
    """
    timeout: float = FLUSH_INTERVAL_S
    while my_generation == _sweeper_generation:
        _sweeper_wake.wait(timeout)
        if my_generation != _sweeper_generation:
            return
        # A record that landed while the window was already elapsed set
        # the wake (sub-FLUSH_INTERVAL_S responsiveness, matching the old
        # inline flush timing); a record inside the window left it unset
        # and the computed timeout below wakes the sweeper exactly at the
        # due time. Clear AFTER the generation check so the exit path
        # stays prompt; a signal cleared here is never lost — the sweep
        # reads each tracker's ``_dirty`` state directly, and the
        # recomputed timeout covers anything that lands mid-pass.
        _sweeper_wake.clear()
        try:
            earliest = _sweep_live_trackers(time.time())
        except Exception:
            log.exception("[USAGE] Flush sweeper pass failed")
            earliest = None
        timeout = earliest if earliest is not None else FLUSH_INTERVAL_S


def stop_flush_sweeper(timeout: float = 2.0) -> None:
    """Stop the shared sweeper thread and wait (bounded) for its exit.

    Production never calls this — the daemon is reaped by the
    interpreter at exit. Exposed so tests can quiesce persistence
    deterministically (e.g. after simulating a slow disk) and for
    orderly teardown in embedding hosts. Safe to call repeatedly; the
    next ``_schedule_flush`` transparently restarts the thread.

    The bump of ``_sweeper_generation`` (under the registry lock, in the
    same critical section that drops the thread reference) is what makes
    the exit unconditional: a record landing between this stop and a
    restart starts a NEW thread bound to a fresh generation, and the old
    thread — which may be blocked inside one slow save pass and unable
    to observe anything until that pass returns — exits at its next loop
    check instead of being resurrected by the restart. The ``join``
    below is therefore best-effort: if it times out, the stale thread
    still terminates itself as soon as its in-flight pass completes.
    """
    global _sweeper_generation, _sweeper_thread
    with _LIVE_TRACKERS_LOCK:
        thread = _sweeper_thread
        _sweeper_thread = None
        _sweeper_generation += 1
    _sweeper_wake.set()  # interrupt a pending wait so the exit check runs
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
