"""User vocabulary store (C-PERSIST-1 categories)."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_typer.server.correction_usage import CorrectionUsageTracker

log = logging.getLogger(__name__)

VOCAB_FILENAME = "vocabulary.json"
_LEGACY_VOCAB_FILENAME = "voice-typer-vocabulary.json"
# single source of truth for the bundled corrections file path.
BUNDLED_CORRECTIONS_PATH = Path(__file__).parent / "corrections.json"

# Persistence model (do NOT flatten or re-merge):
CATEGORIES = [
    "misspellings",
    "phrase_corrections",
    "extra_word_patterns",
    "technical_terms",
    "names",
    "products",
]


# Limits for corrections entries to prevent resource exhaustion
MAX_CORRECTIONS_ENTRIES = 5000
MAX_PATTERN_LENGTH = 200
MAX_REPLACEMENT_LENGTH = 500


class VocabularyManager:
    """Manages custom vocabulary entries across 6 categories."""

    def __init__(
        self,
        config_dir: Path | None = None,
        bundled_path: Path | None = None,
        usage_tracker: CorrectionUsageTracker | None = None,
    ):
        """``usage_tracker``: optional shared"""
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._config_dir = config_dir
        self._usage_tracker: CorrectionUsageTracker | None = usage_tracker
        self._user_path = config_dir / VOCAB_FILENAME
        # One-time migration of the legacy prefixed name
        _legacy = config_dir / _LEGACY_VOCAB_FILENAME
        if _legacy.exists() and not self._user_path.exists():
            try:
                _legacy.rename(self._user_path)
                log.debug(
                    "[VOCAB] migrated legacy %s -> %s",
                    _LEGACY_VOCAB_FILENAME,
                    VOCAB_FILENAME,
                )
            except OSError as exc:
                log.debug("[VOCAB] legacy file migration failed: %s", exc)

        if bundled_path is None:
            # use the shared BUNDLED_CORRECTIONS_PATH constant.
            bundled_path = BUNDLED_CORRECTIONS_PATH
        self._bundled_path = bundled_path

        # Route user-vocabulary persistence through PersistedJSON
        from voice_typer.server.secure_file_io import PersistedJSON

        self._user_store = PersistedJSON(self._user_path, default={})

        # Active merged data: {category: data}
        self._data: dict[str, Any] = {}
        # Deletion tombstones: {category: [key | [wrong, correct], ...]}.
        self._deleted: dict[str, list] = {}
        # guards read-modify-write mutations of self._data (add/remove/
        self._lock = threading.Lock()
        # Raw bundled defaults (as loaded by ``_load_bundled``, before any
        self._bundled_raw: dict[str, Any] = {}
        # Combined-alternation regex cache for phrase-level categories
        self._combined_phrase_cache: (
            dict[str, tuple[re.Pattern[str], dict[str, tuple[str, str]]] | tuple[()]] | None
        ) = None
        self._load_and_merge()

    def _invalidate_pattern_cache(self) -> None:
        """invalidate the compiled-pattern cache. Called on any mutation."""
        self._combined_phrase_cache = None

    def _get_combined_phrase_pattern(self, category: str) -> tuple[re.Pattern[str], dict[str, tuple[str, str]]] | None:
        """Build (or fetch from cache) the combined-alternation regex for a

        Returns ``(pattern, lookup)`` where ``pattern`` is a single
        """
        if self._combined_phrase_cache is None:
            self._combined_phrase_cache = {}
        cached = self._combined_phrase_cache.get(category)
        if cached is not None:
            return cached or None  # (empty sentinel) → None
        with self._lock:
            entries = self._data.get(category, [])
            if not isinstance(entries, list) or not entries:
                self._combined_phrase_cache[category] = ()  # negative cache
                return None
            sorted_entries = sorted(
                (e for e in entries if isinstance(e, list | tuple) and len(e) >= 2),
                key=lambda e: len(e[0]),
                reverse=True,
            )
            if not sorted_entries:
                self._combined_phrase_cache[category] = ()  # negative cache
                return None
            lookup: dict[str, tuple[str, str]] = {}
            alternations: list[str] = []
            for entry in sorted_entries:
                key = entry[0].lower()
                if key in lookup:
                    # Duplicate original (case-insensitive), first
                    continue
                lookup[key] = (entry[1], entry[0])
                alternations.append(re.escape(entry[0]))
        pattern = re.compile("(?:" + "|".join(alternations) + ")", re.IGNORECASE)
        compiled: tuple[re.Pattern[str], dict[str, tuple[str, str]]] = (pattern, lookup)
        self._combined_phrase_cache[category] = compiled
        return compiled

    def _load_and_merge(self) -> None:
        """Load bundled corrections then merge user vocabulary on top."""
        # Start with bundled corrections
        bundled = self._load_bundled()
        # Keep the RAW defaults around so the diff-style save path can
        self._bundled_raw = bundled
        user = self._load_user()

        # Deletion tombstones (reserved ``_deleted`` key in the user file)
        self._deleted = {}
        raw_deleted = user.get("_deleted")
        if isinstance(raw_deleted, dict):
            self._deleted = {
                cat: (list(v) if isinstance(v, list) else []) for cat, v in raw_deleted.items() if cat in CATEGORIES
            }

        # Merge: user extends bundled
        for cat in CATEGORIES:
            bundled_cat = bundled.get(cat)
            user_cat = user.get(cat)

            if cat in ("misspellings", "technical_terms", "names", "products"):
                # Dict-based: user keys override bundled
                merged = dict(bundled_cat) if isinstance(bundled_cat, dict) else {}
                if isinstance(user_cat, dict):
                    merged.update(user_cat)
                self._data[cat] = merged
            elif cat in ("phrase_corrections", "extra_word_patterns"):
                # List-based: user entries are appended
                merged = list(bundled_cat) if isinstance(bundled_cat, list) else []
                if isinstance(user_cat, list):
                    merged.extend(user_cat)
                self._data[cat] = merged
            else:
                # Fallback
                self._data[cat] = user_cat if user_cat is not None else bundled_cat

        # Apply deletion tombstones: bundled (or previously user-added)
        for cat in CATEGORIES:
            removed = self._deleted.get(cat)
            if not removed:
                continue
            if cat in ("misspellings", "technical_terms", "names", "products"):
                cat_data = self._data.get(cat)
                if isinstance(cat_data, dict):
                    for k in removed:
                        if isinstance(k, str):
                            cat_data.pop(k, None)
            else:
                cat_data = self._data.get(cat)
                if isinstance(cat_data, list):
                    removed_pairs = {tuple(r) for r in removed if isinstance(r, (list, tuple)) and len(r) >= 2}
                    if removed_pairs:
                        self._data[cat] = [
                            e
                            for e in cat_data
                            if not (isinstance(e, (list, tuple)) and len(e) >= 2 and tuple(e) in removed_pairs)
                        ]

        # invalidate pattern cache after data reload.
        self._invalidate_pattern_cache()

    def _load_bundled(self) -> dict:
        """Load the bundled corrections.json."""
        if not self._bundled_path.exists():
            return {}
        try:
            # use _secure_read_text to prevent symlink-TOCTOU attacks
            from voice_typer.server.config import _secure_read_text

            raw = _secure_read_text(self._bundled_path, encoding="utf-8")
            data = json.loads(raw)
            return self._normalize_data(data)
        except Exception as exc:
            log.warning("[VOCAB] Failed to load bundled: %s", exc)
            return {}

    def _load_user(self) -> dict:
        """Load the user vocabulary file."""
        data = self._user_store.load()
        if not isinstance(data, dict):
            return {}
        return self._normalize_data(data)

    @staticmethod
    def _normalize_data(data: dict) -> dict:
        """Normalize raw JSON data into canonical category format."""
        if not isinstance(data, dict):
            return {
                cat: ({} if cat in ("misspellings", "technical_terms", "names", "products") else [])
                for cat in CATEGORIES
            }
        result: dict[str, Any] = {}
        for cat in CATEGORIES:
            val = data.get(cat)
            if cat in ("misspellings", "technical_terms", "names", "products"):
                result[cat] = dict(val) if isinstance(val, dict) else {}
            elif cat in ("phrase_corrections", "extra_word_patterns"):
                result[cat] = list(val) if isinstance(val, list) else []
            else:
                result[cat] = val
        # Carry the reserved ``_deleted`` tombstone key through
        if isinstance(data.get("_deleted"), dict):
            result["_deleted"] = {
                cat: (list(v) if isinstance(v, list) else [])
                for cat, v in data["_deleted"].items()
                if cat in CATEGORIES
            }
        return result

    def _save_user(self) -> None:
        """Save only user vocabulary data (not bundled) to the user file."""
        import time as _time

        max_retries = 3
        # track the final failure so we can raise after the
        final_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                # PersistedJSON.save handles atomic write + .bak
                payload: dict[str, Any] = dict(self._data)
                if self._deleted:
                    payload["_deleted"] = self._deleted
                self._user_store.save(payload, durability=False)
                log.debug("[VOCAB] Saved user vocabulary")
                return
            except PermissionError as exc:
                final_exc = exc
                if attempt < max_retries - 1:
                    backoff = 0.05 * (2**attempt)  # 50ms, 100ms, 200ms
                    log.warning(
                        "[VOCAB] PermissionError on save (attempt %d/%d), retrying in %.0fms: %s",
                        attempt + 1,
                        max_retries,
                        backoff * 1000,
                        exc,
                    )
                    _time.sleep(backoff)
                else:
                    log.exception(
                        "[VOCAB] Failed to save user vocabulary after %d attempts: %s",
                        max_retries,
                        exc,
                    )
            except OSError as exc:
                final_exc = exc
                # use log.exception so the traceback is
                log.exception("[VOCAB] Failed to save user vocabulary")
                break
        # surface the failure to callers so they can roll back
        if final_exc is not None:
            raise final_exc

    def get_category(self, category: str) -> object:
        """Get all entries for a category."""
        with self._lock:
            if category in ("misspellings", "technical_terms", "names", "products"):
                return dict(self._data.get(category, {}))
            return list(self._data.get(category, []))

    def get_all(self) -> dict:
        """Return a shallow copy of all merged data."""
        with self._lock:
            return {cat: (dict(v) if isinstance(v, dict) else list(v)) for cat, v in self._data.items()}

    def add_entry(self, category: str, key: str, value: str) -> bool:
        """Add an entry to a dict-based category (misspellings, technical_terms, names, products)."""
        if category not in ("misspellings", "technical_terms", "names", "products"):
            log.error("[VOCAB] Cannot add dict entry to list category %s", category)
            return False
        if len(key) > MAX_PATTERN_LENGTH:
            log.warning("[VOCAB] Pattern exceeds MAX_PATTERN_LENGTH (%d > %d), rejecting", len(key), MAX_PATTERN_LENGTH)
            return False
        if len(value) > MAX_REPLACEMENT_LENGTH:
            log.warning(
                "[VOCAB] Replacement exceeds MAX_REPLACEMENT_LENGTH (%d > %d), rejecting",
                len(value),
                MAX_REPLACEMENT_LENGTH,
            )
            return False
        with self._lock:
            if not isinstance(self._data.get(category), dict):
                self._data[category] = {}
            cat_data = self._data[category]
            if not isinstance(cat_data, dict):
                return False
            if len(cat_data) >= MAX_CORRECTIONS_ENTRIES:
                log.warning(
                    "[VOCAB] Category %s has reached MAX_CORRECTIONS_ENTRIES (%d), rejecting",
                    category,
                    MAX_CORRECTIONS_ENTRIES,
                )
                return False
            # snapshot for rollback.
            had_key = key in cat_data
            old_value = cat_data.get(key)
            cat_data[key] = value
            # Re-adding an entry clears its deletion tombstone so the
            self._deleted.setdefault(category, [])
            if key in self._deleted[category]:
                self._deleted[category].remove(key)
        try:
            self._save_user()
        except Exception:
            # roll back the in-memory mutation.
            with self._lock:
                if had_key:
                    cat_data[key] = old_value
                elif key in cat_data:
                    del cat_data[key]
            raise
        # invalidate pattern cache on mutation.
        self._invalidate_pattern_cache()
        return True

    def remove_entry(self, category: str, key: str) -> bool:
        """Remove an entry from a dict-based category."""
        removed = False
        old_value: Any = None
        cat_data: Any = None
        tombstoned = False
        with self._lock:
            cat_data = self._data.get(category)
            if isinstance(cat_data, dict) and key in cat_data:
                old_value = cat_data[key]
                del cat_data[key]
                # Record a deletion tombstone so removing a BUNDLED
                self._deleted.setdefault(category, [])
                if key not in self._deleted[category]:
                    self._deleted[category].append(key)
                    tombstoned = True
                removed = True
        if removed:
            try:
                self._save_user()
            except Exception:
                # roll back the in-memory mutation (and tombstone).
                with self._lock:
                    if isinstance(cat_data, dict):
                        cat_data[key] = old_value
                    if tombstoned and key in self._deleted.get(category, []):
                        self._deleted[category].remove(key)
                raise
            # invalidate pattern cache on mutation.
            self._invalidate_pattern_cache()
        return removed

    def add_phrase(self, category: str, wrong: str, correct: str) -> bool:
        """Add an entry to a list-based category (phrase_corrections, extra_word_patterns)."""
        if category not in ("phrase_corrections", "extra_word_patterns"):
            log.error("[VOCAB] Cannot add list entry to dict category %s", category)
            return False
        if len(wrong) > MAX_PATTERN_LENGTH:
            log.warning(
                "[VOCAB] Phrase pattern exceeds MAX_PATTERN_LENGTH (%d > %d), rejecting", len(wrong), MAX_PATTERN_LENGTH
            )
            return False
        if len(correct) > MAX_REPLACEMENT_LENGTH:
            log.warning(
                "[VOCAB] Phrase replacement exceeds MAX_REPLACEMENT_LENGTH (%d > %d), rejecting",
                len(correct),
                MAX_REPLACEMENT_LENGTH,
            )
            return False
        with self._lock:
            if not isinstance(self._data.get(category), list):
                self._data[category] = []
            cat_data = self._data[category]
            if not isinstance(cat_data, list):
                return False
            if len(cat_data) >= MAX_CORRECTIONS_ENTRIES:
                log.warning(
                    "[VOCAB] Category %s has reached MAX_CORRECTIONS_ENTRIES (%d), rejecting",
                    category,
                    MAX_CORRECTIONS_ENTRIES,
                )
                return False
            new_entry = [wrong, correct]
            cat_data.append(new_entry)
            # Re-adding a phrase clears its deletion tombstone (mirrors
            self._deleted.setdefault(category, [])
            with contextlib.suppress(ValueError):
                self._deleted[category].remove(list(new_entry))
        try:
            self._save_user()
        except Exception:
            # roll back the in-memory mutation (and un-tombstone).
            with self._lock, contextlib.suppress(ValueError):
                cat_data.remove(new_entry)
            raise
        # invalidate pattern cache on mutation.
        self._invalidate_pattern_cache()
        return True

    def remove_phrase(self, category: str, index: int) -> bool:
        """Remove a phrase entry by index."""
        removed = False
        old_entry: Any = None
        cat_data: Any = None
        tombstoned = False
        tombstone_pair: Any = None
        with self._lock:
            cat_data = self._data.get(category)
            if isinstance(cat_data, list) and 0 <= index < len(cat_data):
                old_entry = cat_data.pop(index)
                # Record a deletion tombstone so removing a BUNDLED
                self._deleted.setdefault(category, [])
                tombstone_pair = list(old_entry) if isinstance(old_entry, (list, tuple)) else old_entry
                if tombstone_pair not in self._deleted[category]:
                    self._deleted[category].append(tombstone_pair)
                    tombstoned = True
                removed = True
        if removed:
            try:
                self._save_user()
            except Exception:
                # roll back the in-memory mutation (and tombstone).
                with self._lock:
                    if isinstance(cat_data, list):
                        cat_data.insert(index, old_entry)
                    if tombstoned and tombstone_pair in self._deleted.get(category, []):
                        self._deleted[category].remove(tombstone_pair)
                raise
        # invalidate pattern cache on mutation.
        self._invalidate_pattern_cache()
        return removed

    def export_json(self) -> str:
        """Export all vocabulary as JSON string."""
        return json.dumps(self._data, indent=2, ensure_ascii=False)

    def import_json(self, json_str: str, *, merge: bool = True) -> tuple[int, int]:
        """Import vocabulary from a JSON string.

        Returns a tuple ``(categories_imported, dropped_entries)``.
        """
        try:
            data = json.loads(json_str)
            data = self._normalize_data(data)

            # within the SEC-011 resource limits even when the
            dropped = 0
            validated: dict[str, Any] = {}
            for cat in CATEGORIES:
                if cat not in data or data[cat] is None:
                    continue
                raw = data[cat]
                if cat in ("misspellings", "technical_terms", "names", "products"):
                    if not isinstance(raw, dict):
                        continue
                    filtered: dict[str, str] = {}
                    for k, v in raw.items():
                        k_str = k if isinstance(k, str) else str(k)
                        v_str = v if isinstance(v, str) else str(v)
                        if len(k_str) > MAX_PATTERN_LENGTH:
                            dropped += 1
                            continue
                        if len(v_str) > MAX_REPLACEMENT_LENGTH:
                            dropped += 1
                            continue
                        filtered[k_str] = v_str
                    existing_cat = self._data.get(cat)
                    existing_len = len(existing_cat) if isinstance(existing_cat, dict) else 0
                    new_len = len(filtered)
                    would_be = existing_len + new_len if merge else new_len
                    if would_be > MAX_CORRECTIONS_ENTRIES:
                        log.warning(
                            "[VOCAB] Category %s would exceed MAX_CORRECTIONS_ENTRIES "
                            "(existing=%d, new=%d, cap=%d) | dropping %d entries from import",
                            cat,
                            existing_len,
                            new_len,
                            MAX_CORRECTIONS_ENTRIES,
                            new_len,
                        )
                        dropped += new_len
                        continue
                    # only register non-empty categories so the
                    if filtered:
                        validated[cat] = filtered
                else:
                    # List-based category (phrase_corrections, extra_word_patterns).
                    if not isinstance(raw, list):
                        continue
                    filtered_list: list[list[str]] = []
                    for entry in raw:
                        if not isinstance(entry, list | tuple) or len(entry) < 2:
                            dropped += 1
                            continue
                        bad, good = entry[0], entry[1]
                        bad_str = bad if isinstance(bad, str) else str(bad)
                        good_str = good if isinstance(good, str) else str(good)
                        if len(bad_str) > MAX_PATTERN_LENGTH or len(good_str) > MAX_REPLACEMENT_LENGTH:
                            dropped += 1
                            continue
                        filtered_list.append([bad_str, good_str])
                    existing_cat = self._data.get(cat)
                    existing_len = len(existing_cat) if isinstance(existing_cat, list) else 0
                    new_len = len(filtered_list)
                    would_be = existing_len + new_len if merge else new_len
                    if would_be > MAX_CORRECTIONS_ENTRIES:
                        log.warning(
                            "[VOCAB] Category %s would exceed MAX_CORRECTIONS_ENTRIES "
                            "(existing=%d, new=%d, cap=%d) | dropping %d entries from import",
                            cat,
                            existing_len,
                            new_len,
                            MAX_CORRECTIONS_ENTRIES,
                            new_len,
                        )
                        dropped += new_len
                        continue
                    # only register non-empty categories so the
                    if filtered_list:
                        validated[cat] = filtered_list

            if dropped:
                log.warning(
                    "[VOCAB] Dropped %d entries from import (oversized or over-cap)",
                    dropped,
                )

            count = 0
            with self._lock:
                # snapshot for rollback. Shallow-copy the
                snapshot: dict[str, Any] = {}
                for cat, val in self._data.items():
                    if isinstance(val, dict):
                        snapshot[cat] = dict(val)
                    elif isinstance(val, list):
                        snapshot[cat] = list(val)
                    else:
                        snapshot[cat] = val
                for cat in CATEGORIES:
                    if cat not in validated:
                        continue
                    if merge:
                        if cat in ("misspellings", "technical_terms", "names", "products"):
                            if not isinstance(self._data.get(cat), dict):
                                self._data[cat] = {}
                            cat_dict = self._data[cat]
                            if isinstance(cat_dict, dict) and isinstance(validated[cat], dict):
                                cat_dict.update(validated[cat])
                        else:
                            if not isinstance(self._data.get(cat), list):
                                self._data[cat] = []
                            cat_list = self._data[cat]
                            if isinstance(cat_list, list) and isinstance(validated[cat], list):
                                cat_list.extend(validated[cat])
                    else:
                        self._data[cat] = validated[cat]
                    count += 1
            if count:
                try:
                    self._save_user()
                except Exception:
                    # roll back the in-memory mutation.
                    with self._lock:
                        self._data.clear()
                        self._data.update(snapshot)
                    raise
            # invalidate pattern cache on import.
            self._invalidate_pattern_cache()
            return count, dropped
        except Exception:
            log.exception("[VOCAB] Import failed")
            return 0, 0

    @property
    def usage_tracker(self) -> CorrectionUsageTracker:
        """The shared per-correction usage tracker (see correction_usage.py)."""
        if self._usage_tracker is None:
            from voice_typer.server.correction_usage import CorrectionUsageTracker

            self._usage_tracker = CorrectionUsageTracker(self._config_dir)
        return self._usage_tracker

    def apply_to_text(self, text: str, *, track_usage: bool = True) -> str:
        """Apply vocabulary corrections to transcribed text."""
        # Pre-compiled regex + the memoized token-key normalizer, shared
        from voice_typer.server.text_cleanup import _RE_MISSPELL_WRAP, _token_key

        # (category, original, count) hits for the usage tracker.
        hits: list[tuple[str, str, int]] = []

        # Phrase-level corrections. ONE combined-alternation pass per
        for cat in ("phrase_corrections", "extra_word_patterns"):
            combined = self._get_combined_phrase_pattern(cat)
            if combined is None:
                continue
            pattern, lookup = combined
            counts: dict[str, int] = {}

            def _phrase_repl(
                m: re.Match[str],
                _lookup: dict[str, tuple[str, str]] = lookup,
                _counts: dict[str, int] = counts,
            ) -> str:
                key = m.group(0).lower()
                _counts[key] = _counts.get(key, 0) + 1
                return _lookup[key][0]

            text, _total = pattern.subn(_phrase_repl, text)
            for key, count in counts.items():
                hits.append((cat, lookup[key][1], count))

        # Word-level corrections, single tokenization pass shared
        with self._lock:
            word_cats = [(cat, self._data.get(cat)) for cat in ("misspellings", "technical_terms", "names", "products")]

        tokens = text.split(" ")
        for cat, entries in word_cats:
            # Skip non-dicts and empty categories, avoids the per-token
            if not isinstance(entries, dict) or not entries:
                continue
            for i, token in enumerate(tokens):
                key = _token_key(token)
                correction = entries.get(key)
                if correction is not None:
                    match = _RE_MISSPELL_WRAP.match(token)
                    tokens[i] = f"{match.group(1)}{correction}{match.group(3)}" if match else correction
                    hits.append((cat, key, 1))
        text = " ".join(tokens)

        if track_usage and hits:
            try:
                self.usage_tracker.record_corrections(hits)
            except Exception:
                # Usage tracking must NEVER break the dictation path.
                log.warning("[VOCAB] Failed to record correction usage", exc_info=True)

        return text
