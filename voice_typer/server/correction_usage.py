"""Per-correction usage tracking for the custom vocabulary feature."""

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

# Keep per-day totals for at most this many days (bounded file growth).
KEEP_DAYS = 90
# Batched writes: flush at most this often while dirty.
FLUSH_INTERVAL_S = 5.0

# Live-tracker registry + shared flush sweeper (single daemon thread
_LIVE_TRACKERS: weakref.WeakSet[CorrectionUsageTracker] = weakref.WeakSet()
_LIVE_TRACKERS_LOCK = threading.Lock()
_sweeper_wake = threading.Event()
# Generation counter guarding the stop/restart race: every sweeper thread
_sweeper_generation = 0
_sweeper_thread: threading.Thread | None = None

_DICT_CATEGORIES = ("misspellings", "technical_terms", "names", "products")
_LIST_CATEGORIES = ("phrase_corrections", "extra_word_patterns")


def _day_key(ts: float) -> str:
    """Local-calendar ``YYYY-MM-DD`` key for a unix timestamp."""
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

    def record_corrections(
        self,
        hits: list[tuple[str, str, int]],
        ts: float | None = None,
    ) -> None:
        """Record correction firings from one ``apply_to_text`` pass."""
        if not hits:
            return
        ts = ts if ts is not None else time.time()
        day = _day_key(ts)
        with self._lock:
            entries = self._data.setdefault("entries", {})
            by_day = self._data.setdefault("corrections_by_day", {})
            # A hand-edited or partially-written file can leave a
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

    def get_snapshot(self) -> dict[str, object]:
        """Return a deep copy of the usage data (IPC read path)."""
        with self._lock:
            snapshot = copy.deepcopy(self._data)
        # Flush any pending increments so the on-disk file reflects
        self.flush()
        return snapshot

    def prune_entries(self, vocab_data: dict[str, Any] | None) -> None:
        """Drop usage records for corrections no longer in *vocab_data*."""
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
        """Force a write of the current in-memory state."""
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
                log.exception("[USAGE] Failed to save correction usage")
                with self._lock:
                    self._dirty = True

    def _schedule_flush(self) -> None:
        """Signal the shared sweeper that a flush may be due."""
        _ensure_flush_sweeper()
        _sweeper_wake.set()

    def _flush_if_due(self, now: float) -> float | None:
        """Flush when the debounce window has elapsed (sweeper-only).

        Returns ``None`` when there is nothing pending or a flush was
        """
        with self._lock:
            if not self._dirty:
                return None
            remaining = FLUSH_INTERVAL_S - (now - self._last_flush)
            if remaining > 0:
                return remaining
        # flush() re-checks ``_dirty`` under the lock, so a concurrent
        self.flush()
        return None

    def _prune_days(self) -> None:
        cutoff = _day_key(time.time() - KEEP_DAYS * 86400)
        for key in ("corrections_by_day", "dictations_by_day"):
            by_day = self._data.get(key)
            if isinstance(by_day, dict):
                for day in [d for d in by_day if d < cutoff]:
                    del by_day[day]


def _ensure_flush_sweeper() -> None:
    """Start the shared flush-sweeper daemon thread (idempotent)."""
    global _sweeper_generation, _sweeper_thread
    with _LIVE_TRACKERS_LOCK:
        if _sweeper_thread is not None and _sweeper_thread.is_alive():
            return
        # Bind the new thread to the generation it starts under. Bumping
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
    """Background sweeper: persist debounced usage increments."""
    timeout: float = FLUSH_INTERVAL_S
    while my_generation == _sweeper_generation:
        _sweeper_wake.wait(timeout)
        if my_generation != _sweeper_generation:
            return
        # A record that landed while the window was already elapsed set
        _sweeper_wake.clear()
        try:
            earliest = _sweep_live_trackers(time.time())
        except Exception:
            log.exception("[USAGE] Flush sweeper pass failed")
            earliest = None
        timeout = earliest if earliest is not None else FLUSH_INTERVAL_S


def stop_flush_sweeper(timeout: float = 2.0) -> None:
    """Stop the shared sweeper thread and wait (bounded) for its exit."""
    global _sweeper_generation, _sweeper_thread
    with _LIVE_TRACKERS_LOCK:
        thread = _sweeper_thread
        _sweeper_thread = None
        _sweeper_generation += 1
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
