"""Custom vocabulary manager: 6 categories, merge bundled+user, CRUD, import/export.

Categories:
    misspellings        — word → corrected word
    phrase_corrections  — phrase → corrected phrase
    extra_word_patterns — extra word pattern → removal/replacement
    technical_terms     — common misrecognition → correct technical term
    names               — misrecognized name → correct name
    products            — misrecognized product → correct product name

Merges bundled corrections.json with user voice-typer-corrections.json.
User entries extend (not replace) the bundled defaults.

The vocabulary is applied after text cleanup and before template matching
in the pipeline: transcribe → text cleanup → vocabulary → templates → auto-punctuate → paste
"""

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

VOCAB_FILENAME = "voice-typer-vocabulary.json"
# single source of truth for the bundled corrections file path.
# text_cleanup.py imports this constant instead of re-declaring it.
BUNDLED_CORRECTIONS_PATH = Path(__file__).parent / "corrections.json"

# Category names in canonical order
CATEGORIES = [
    "misspellings",
    "phrase_corrections",
    "extra_word_patterns",
    "technical_terms",
    "names",
    "products",
]

# ─── Vocabulary Manager ─────────────────────────────────────────────────


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
        """
        ``usage_tracker`` — optional shared
        :class:`~voice_typer.server.correction_usage.CorrectionUsageTracker`
        injected by the app so dictation records hits into ONE
        app-wide counter. When None (standalone / tests / cold-start
        fallback), a tracker is lazily created over the same
        config dir on first use.
        """
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._config_dir = config_dir
        self._usage_tracker: CorrectionUsageTracker | None = usage_tracker
        self._user_path = config_dir / VOCAB_FILENAME

        if bundled_path is None:
            # use the shared BUNDLED_CORRECTIONS_PATH constant.
            bundled_path = BUNDLED_CORRECTIONS_PATH
        self._bundled_path = bundled_path

        # Route user-vocabulary persistence through PersistedJSON
        # so it gets single-slot .bak before overwrite + corrupt-file
        # quarantine + 0o600 perms (parity with config.py). The bundled
        # file is read-only and not routed through this helper.
        from voice_typer.server.secure_file_io import PersistedJSON

        self._user_store = PersistedJSON(self._user_path, default={})

        # Active merged data: {category: data}
        self._data: dict[str, Any] = {}
        # Deletion tombstones: {category: [key | [wrong, correct], ...]}.
        # Persisted under a reserved ``_deleted`` key in the user file so
        # removing a BUNDLED default correction actually sticks — the
        # diff-style user file alone can't express "remove a bundled
        # entry" and the entry would resurrect on the next merge. Loaded
        # in ``_load_and_merge``, applied to the merged ``_data``, kept in
        # sync by the CRUD methods, and written by ``_save_user``.
        self._deleted: dict[str, list] = {}
        # guards read-modify-write mutations of self._data (add/remove/
        # import) so concurrent callers (UI thread + auto-vocabulary analysis
        # thread) can't corrupt the merge or lose entries.
        self._lock = threading.Lock()
        # Raw bundled defaults (as loaded by ``_load_bundled``, before any
        # user merge). Stored so ``save_vocabulary_with_diff`` can diff
        # incoming payloads against the TRUE defaults — comparing against
        # the merged ``_data`` drops unchanged user entries on every
        # subsequent save (the wholesale user-file replace would lose them).
        self._bundled_raw: dict[str, Any] = {}
        # lazy cache of compiled regex patterns for phrase_corrections
        # and extra_word_patterns. Invalidated (set to None) on any mutation
        # (add_entry/remove_entry/import_json/_load_and_merge). Rebuilt on
        # first apply_to_text() call after invalidation. At 5000 entries this
        # saves ~50ms per dictation cycle (was recompiling per phrase per call).
        self._compiled_patterns: dict[str, list[tuple[re.Pattern[str], str, str]]] | None = None
        self._load_and_merge()

    def _invalidate_pattern_cache(self) -> None:
        """invalidate the compiled-pattern cache. Called on any mutation.

        Does NOT acquire ``self._lock``: the assignment is a single
        GIL-atomic attribute write, and callers may already hold the
        lock when calling us. In particular ``_load_and_merge`` is
        invoked under ``live_vm._lock`` by
        ``save_vocabulary_with_diff`` (``service/vocabulary.py``), so
        acquiring the non-reentrant ``threading.Lock`` here would
        self-deadlock. The rebuild path in ``_get_compiled_patterns``
        reads ``self._data`` under the lock, so a concurrent rebuild
        cannot observe a half-merged data state.
        """
        self._compiled_patterns = None

    def _get_compiled_patterns(self, category: str) -> list[tuple[re.Pattern[str], str, str]]:
        """return cached compiled patterns for a phrase category, rebuilding if needed.

        Each cached entry is ``(compiled_pattern, good, original)`` —
        ``original`` is kept so ``apply_to_text`` can report WHICH
        correction fired to the usage tracker.
        """
        if self._compiled_patterns is None:
            self._compiled_patterns = {}
        if category not in self._compiled_patterns:
            with self._lock:
                entries = self._data.get(category, [])
                if not isinstance(entries, list):
                    self._compiled_patterns[category] = []
                    return []
                sorted_entries = sorted(
                    entries,
                    key=lambda e: len(e[0]) if isinstance(e, list | tuple) and len(e) >= 2 else 0,
                    reverse=True,
                )
            self._compiled_patterns[category] = [
                (re.compile(re.escape(entry[0]), re.IGNORECASE), entry[1], entry[0])
                for entry in sorted_entries
                if isinstance(entry, list | tuple) and len(entry) >= 2
            ]
        return self._compiled_patterns[category]

    # ── Loading and merging ──────────────────────────────────────────

    def _load_and_merge(self) -> None:
        """Load bundled corrections then merge user vocabulary on top."""
        # Start with bundled corrections
        bundled = self._load_bundled()
        # Keep the RAW defaults around so the diff-style save path can
        # distinguish "user customization" from "unchanged bundled" (see
        # ``service/vocabulary.py`` — diffing against the merged state
        # drops unchanged user entries on the next save).
        self._bundled_raw = bundled
        user = self._load_user()

        # Deletion tombstones (reserved ``_deleted`` key in the user file)
        # — entries the user removed from the BUNDLED defaults. Kept out
        # of ``self._data`` and applied to the merged result below so
        # ``get_all`` / dictation never see the pseudo-category.
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
        # entries the user removed stay removed after reload.
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
        """Load the user vocabulary file.

        Persistence is routed through :class:`PersistedJSON`
        (``self._user_store``). On parse failure (corrupt JSON, OSError,
        symlink-TOCTOU raise), the helper quarantines the corrupt file
        to ``<path>.corrupt-<ts>`` for forensic recovery and returns
        the configured default (``{}``). The previous implementation
        silently fell back to defaults with a single WARNING log line
        — no quarantine — so the next ``_save_user`` would atomically
        overwrite the corrupt file with defaults, destroying any chance
        of forensic recovery. Mirrors ``config.py:1744-1763``.
        """
        data = self._user_store.load()
        if not isinstance(data, dict):
            return {}
        return self._normalize_data(data)

    @staticmethod
    def _normalize_data(data: dict) -> dict:
        """Normalize raw JSON data into canonical category format.

        previously if ``data`` was a list (e.g. user had a
        malformed vocabulary.json that was a JSON array instead of an
        object), ``data.get(cat)`` would raise ``AttributeError`` and
        the entire normalization would crash, losing all bundled
        corrections. We now validate the type at the top and return
        an empty dict for non-dict input.
        """
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
        # normalization (it is not a vocabulary category — merge applies
        # it, CRUD keeps it in sync, and it is never exposed via
        # ``get_all``).
        if isinstance(data.get("_deleted"), dict):
            result["_deleted"] = {
                cat: (list(v) if isinstance(v, list) else [])
                for cat, v in data["_deleted"].items()
                if cat in CATEGORIES
            }
        return result

    # ── Persistence ──────────────────────────────────────────────────

    def _save_user(self) -> None:
        """Save only user vocabulary data (not bundled) to the user file.

        Persistence is routed through :class:`PersistedJSON`
        (``self._user_store``), which provides atomic write + single-slot
        ``.bak`` before overwrite + 0o600 perms (parity with
        ``config.py:1163-1182``). The Windows ``PermissionError`` retry
        loop is preserved: ``Path.replace`` is not atomic on Windows
        when the destination is open by another process (e.g. an
        editor or a cloud-sync client watching the file). The shared
        ``_secure_atomic_write`` itself is already atomic — the retries
        here are purely for the ``PermissionError`` race on Windows
        where the destination is locked by an editor / cloud-sync
        client.

        previously this method logged failures and returned
        silently — a contract described in the docstring as
        "best-effort". That left CRUD callers (``add_entry`` /
        ``add_phrase`` / ``remove_entry`` / ``remove_phrase`` /
        ``import_json``) unable to detect failure, so they returned
        ``True`` to their callers (and the IPC layer returned an
        ``ack``) while the in-memory state diverged from the on-disk
        state. We now RAISE ``OSError`` after the retry loop is
        exhausted so callers can roll back their in-memory mutation
        and the IPC layer can surface the failure as an error
        envelope.
        """
        import time as _time

        max_retries = 3
        # track the final failure so we can raise after the
        # retry loop instead of silently returning.
        final_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                # PersistedJSON.save handles atomic write + .bak
                # + 0o600 perms + parent-dir creation in one call.
                # durability=False — the atomic os.replace still
                # guarantees consistency (no half-written files); only
                # the per-save fsync is dropped. User-vocabulary edits
                # are frequent (typing in the settings panel) and a
                # power-loss window of a few seconds is acceptable.
                # The reserved ``_deleted`` tombstone key rides along so
                # a CRUD remove of a bundled entry survives reload (see
                # ``_load_and_merge``).
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
                    log.error(
                        "[VOCAB] Failed to save user vocabulary after %d attempts: %s",
                        max_retries,
                        exc,
                    )
            except OSError as exc:
                final_exc = exc
                # use log.exception so the traceback is
                # captured automatically via sys.exc_info().
                log.exception("[VOCAB] Failed to save user vocabulary")
                break
        # surface the failure to callers so they can roll back
        # any in-memory mutation they made before calling us.
        if final_exc is not None:
            raise final_exc

    # ── Read access ──────────────────────────────────────────────────

    def get_category(self, category: str) -> object:
        """Get all entries for a category.

        acquire ``self._lock`` and return a SHALLOW COPY of the
        underlying container. Pre-fix, this method bypassed the lock
        and returned the live internal ``self._data[category]``
        dict/list. Callers like
        ``vocabulary_automation._collect_vocabulary_words`` iterate
        the returned container — and a concurrent ``add_entry`` /
        ``remove_entry`` / ``import_json`` mutation (which acquires
        ``self._lock``) could mutate the dict mid-iteration, raising
        ``RuntimeError: dictionary changed size during iteration``
        that was silently swallowed by
        ``dictation_pipeline._apply_vocabulary``'s try/except —
        degrading transcription quality with no diagnostic.

        The snapshot pattern mirrors ``apply_to_text`` (line ~664)
        and ``get_all`` (line ~300), both of which already copy
        under the lock. The individual values inside dict categories
        are NOT deep-copied (shallow copy) — callers that need to
        mutate a value should use ``add_entry`` so the change
        persists to disk.
        """
        with self._lock:
            if category in ("misspellings", "technical_terms", "names", "products"):
                return dict(self._data.get(category, {}))
            return list(self._data.get(category, []))

    def get_all(self) -> dict:
        """Return a shallow copy of all merged data.

        Acquires ``self._lock`` and returns shallow copies of each
        category container (``dict(v)`` for dict categories, ``list(v)``
        for list categories) so a concurrent ``add_entry`` /
        ``remove_entry`` / ``import_json`` mutation can't mutate the
        returned containers mid-iteration by the caller. Mirrors the
        snapshot pattern in ``get_category`` (line ~286) and
        ``apply_to_text``.
        """
        with self._lock:
            return {cat: (dict(v) if isinstance(v, dict) else list(v)) for cat, v in self._data.items()}

    # ── CRUD for dict-based categories ───────────────────────────────

    def add_entry(self, category: str, key: str, value: str) -> bool:
        """Add an entry to a dict-based category (misspellings, technical_terms, names, products).

        Enforces MAX_CORRECTIONS_ENTRIES, MAX_PATTERN_LENGTH, and
        MAX_REPLACEMENT_LENGTH limits.  Returns False if limits are exceeded.

        rolls back the in-memory mutation if ``_save_user`` raises
        so the in-memory state stays consistent with the on-disk state.
        """
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
            # (bundled or previously-user) entry shows again.
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
        """Remove an entry from a dict-based category.

        rolls back the in-memory mutation if ``_save_user`` raises
        so the in-memory state stays consistent with the on-disk state.
        """
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
                # default entry persists across reload (the diff-style
                # user file can't express "remove a bundled entry").
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

    # ── CRUD for list-based categories ───────────────────────────────

    def add_phrase(self, category: str, wrong: str, correct: str) -> bool:
        """Add an entry to a list-based category (phrase_corrections, extra_word_patterns).

        Enforces MAX_CORRECTIONS_ENTRIES, MAX_PATTERN_LENGTH, and
        MAX_REPLACEMENT_LENGTH limits.  Returns False if limits are exceeded.

        rolls back the in-memory mutation if ``_save_user`` raises
        so the in-memory state stays consistent with the on-disk state.
        """
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
            # ``add_entry`` un-tombstoning).
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
        """Remove a phrase entry by index.

        rolls back the in-memory mutation if ``_save_user`` raises
        so the in-memory state stays consistent with the on-disk state.
        """
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
                # phrase persists across reload.
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

    # ── Import / Export ───────────────────────────────────────────────

    def export_json(self) -> str:
        """Export all vocabulary as JSON string."""
        return json.dumps(self._data, indent=2, ensure_ascii=False)

    def import_json(self, json_str: str, *, merge: bool = True) -> tuple[int, int]:
        """Import vocabulary from a JSON string.

        If merge=True, extends existing entries. If False, replaces.
        Returns a tuple ``(categories_imported, dropped_entries)``.

        validates each category BEFORE mutating ``self._data``:
          - Drops entries whose key/pattern exceeds
            ``MAX_PATTERN_LENGTH`` or whose value/replacement exceeds
            ``MAX_REPLACEMENT_LENGTH`` (mirrors
            ``text_cleanup._load_external_corrections``).
          - Drops the entire category import if
            ``len(existing) + len(new)`` (merge=True) or ``len(new)``
            (merge=False) would exceed ``MAX_CORRECTIONS_ENTRIES``.
          - Logs a single warning summarising the dropped count.

        snapshots the entire ``_data`` dict before mutating and
        restores it on save failure so the in-memory state stays
        consistent with the on-disk state. The snapshot is a shallow
        copy of the top-level dict plus shallow copies of each
        category container (dict.copy() / list.copy()) — sufficient
        because we only either ``update`` dicts in place, ``extend``
        lists in place, or replace the container reference outright.
        """
        try:
            data = json.loads(json_str)
            data = self._normalize_data(data)

            # pre-validate every category before mutating
            # ``self._data``. We drop oversized entries and reject
            # entire categories that would exceed
            # MAX_CORRECTIONS_ENTRIES so the in-memory state stays
            # within the SEC-011 resource limits even when the
            # imported JSON is hostile.
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
                            "(existing=%d, new=%d, cap=%d), dropping %d entries from import",
                            cat,
                            existing_len,
                            new_len,
                            MAX_CORRECTIONS_ENTRIES,
                            new_len,
                        )
                        dropped += new_len
                        continue
                    # only register non-empty categories so the
                    # returned ``count`` reflects categories that actually
                    # received new entries (empty placeholders from
                    # _normalize_data defaults would otherwise inflate it).
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
                            "(existing=%d, new=%d, cap=%d), dropping %d entries from import",
                            cat,
                            existing_len,
                            new_len,
                            MAX_CORRECTIONS_ENTRIES,
                            new_len,
                        )
                        dropped += new_len
                        continue
                    # only register non-empty categories so the
                    # returned ``count`` reflects categories that actually
                    # received new entries.
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
                # top-level dict AND each category container so
                # in-place mutations (dict.update / list.extend) on
                # the live containers don't pollute the snapshot.
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

    # ── Apply vocabulary to text ─────────────────────────────────────

    @property
    def usage_tracker(self) -> CorrectionUsageTracker:
        """The shared per-correction usage tracker (see correction_usage.py).

        Lazily created over the same config dir when the app didn't
        inject one (standalone / tests / cold-start fallback), so
        ``apply_to_text`` can always report firings.
        """
        if self._usage_tracker is None:
            from voice_typer.server.correction_usage import CorrectionUsageTracker

            self._usage_tracker = CorrectionUsageTracker(self._config_dir)
        return self._usage_tracker

    def apply_to_text(self, text: str, *, track_usage: bool = True) -> str:
        """Apply vocabulary corrections to transcribed text.

        Processes categories in order:
        1. phrase_corrections (phrase-level)
        2. extra_word_patterns (phrase-level)
        3. misspellings (word-level)
        4. technical_terms (word-level)
        5. names (word-level)
        6. products (word-level)

        When ``track_usage`` is True (the dictation path), every
        correction that fires is reported to the usage tracker
        (per-correction counts + per-day totals for the Analytics
        rate). The "Test corrections" panel passes False so previewing
        a phrase never inflates real usage numbers.
        """
        # Pre-compiled regexes shared with text_cleanup — avoids the
        # 200 re-cache lookups per dictation (50 words × 4 categories)
        # that the inline ``re.sub``/``re.match`` calls previously
        # incurred.
        from voice_typer.server.text_cleanup import _RE_MISSPELL_WRAP, _RE_TOKEN_KEY

        # (category, original, count) hits for the usage tracker.
        hits: list[tuple[str, str, int]] = []

        # Phrase-level corrections first (longer matches first)
        # use cached compiled patterns instead of recompiling per
        # phrase per call. Cache is invalidated on any mutation.
        for cat in ("phrase_corrections", "extra_word_patterns"):
            compiled = self._get_compiled_patterns(cat)
            for pattern, good, original in compiled:
                # use a callable replacement to prevent
                # regex backref interpretation. Previously `pattern.sub(good, text)`
                # interpreted `\1`, `\g<0>`, `\9` etc. in the user-supplied
                # `good` string. A malicious or accidental entry like
                # `["x", "\\9"]` raises `re.error` on every dictation
                # cycle (DoS). The lambda treats `good` as a literal
                # string with no backref processing. ``subn`` also
                # returns the substitution count, which drives the
                # usage tracker.
                text, count = pattern.subn(lambda _m, _g=good: _g, text)
                if count:
                    hits.append((cat, original, count))

        # Word-level corrections — single tokenization pass shared
        # across all 4 dict-based categories (previously the loop
        # re-tokenized + re-joined 4 times per dictation). Categories
        # are applied in order so a misspelling corrected to a term
        # that's then in technical_terms is further corrected (matches
        # the original sequential semantics).
        #
        # Snapshot only the dict REFERENCES under the lock — not the
        # dict contents. Per-token lookups (``dict.get(key)``) are
        # GIL-atomic and safe to call on the live ``self._data`` dicts
        # without copying each entry. Pre-fix the snapshot allocated
        # 6 new containers with up to 5000 reference-copies each per
        # dictation cycle (~30K ref-copies); post-fix only the 4 dict
        # references are captured (no entry copies).
        with self._lock:
            word_cats = [(cat, self._data.get(cat)) for cat in ("misspellings", "technical_terms", "names", "products")]

        tokens = text.split(" ")
        for cat, entries in word_cats:
            # Skip non-dicts and empty categories — avoids the per-token
            # ``key in entries`` lookup cost when the category has no
            # entries (common for the bundled defaults where names and
            # products are typically empty until the user adds entries).
            if not isinstance(entries, dict) or not entries:
                continue
            for i, token in enumerate(tokens):
                key = _RE_TOKEN_KEY.sub("", token).lower()
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
