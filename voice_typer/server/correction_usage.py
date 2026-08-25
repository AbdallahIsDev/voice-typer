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

  - In-memory increments are batched and persisted with a short
    debounce (``FLUSH_INTERVAL_S``) + a forced ``flush()`` on read
    (``get_snapshot``) so the on-disk file is never more than a few
    seconds stale when the UI reads it. A crash loses at most the last
    few seconds of increments — acceptable for usage statistics.
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
        self._maybe_flush()

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
        self._maybe_flush()

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
        self._maybe_flush()

    def flush(self) -> None:
        """Force a write of the current in-memory state."""
        with self._lock:
            if not self._dirty:
                return
            self._prune_days()
            try:
                self._store.save(self._data, durability=False)
                self._dirty = False
                self._last_flush = time.time()
            except Exception:
                # Keep `_dirty` so the next debounce/flush retries; just
                # avoid hammering the disk on persistent failures.
                log.exception("[USAGE] Failed to save correction usage")
                self._last_flush = time.time()

    def _maybe_flush(self) -> None:
        now = time.time()
        if now - self._last_flush >= FLUSH_INTERVAL_S:
            self.flush()

    def _prune_days(self) -> None:
        cutoff = _day_key(time.time() - KEEP_DAYS * 86400)
        for key in ("corrections_by_day", "dictations_by_day"):
            by_day = self._data.get(key)
            if isinstance(by_day, dict):
                for day in [d for d in by_day if d < cutoff]:
                    del by_day[day]
