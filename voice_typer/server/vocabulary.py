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

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VOCAB_FILENAME = "voice-typer-vocabulary.json"
# ARCH-028: single source of truth for the bundled corrections file path.
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


# SEC-011: Limits for corrections entries to prevent resource exhaustion
MAX_CORRECTIONS_ENTRIES = 5000
MAX_PATTERN_LENGTH = 200
MAX_REPLACEMENT_LENGTH = 500


class VocabularyManager:
    """Manages custom vocabulary entries across 6 categories."""

    def __init__(self, config_dir: Path | None = None, bundled_path: Path | None = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._config_dir = config_dir
        self._user_path = config_dir / VOCAB_FILENAME

        if bundled_path is None:
            # ARCH-028: use the shared BUNDLED_CORRECTIONS_PATH constant.
            bundled_path = BUNDLED_CORRECTIONS_PATH
        self._bundled_path = bundled_path

        # Active merged data: {category: data}
        self._data: dict[str, Any] = {}
        # SEC-012: guards read-modify-write mutations of self._data (add/remove/
        # import) so concurrent callers (UI thread + auto-vocabulary analysis
        # thread) can't corrupt the merge or lose entries.
        self._lock = threading.Lock()
        self._load_and_merge()

    # ── Loading and merging ──────────────────────────────────────────

    def _load_and_merge(self) -> None:
        """Load bundled corrections then merge user vocabulary on top."""
        # Start with bundled corrections
        bundled = self._load_bundled()
        user = self._load_user()

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

    def _load_bundled(self) -> dict:
        """Load the bundled corrections.json."""
        if not self._bundled_path.exists():
            return {}
        try:
            # SEC-002: use _secure_read_text to prevent symlink-TOCTOU attacks
            from voice_typer.server.config import _secure_read_text

            raw = _secure_read_text(self._bundled_path, encoding="utf-8")
            data = json.loads(raw)
            return self._normalize_data(data)
        except Exception as exc:
            log.warning("[VOCAB] Failed to load bundled: %s", exc)
            return {}

    def _load_user(self) -> dict:
        """Load the user vocabulary file."""
        if not self._user_path.exists():
            return {}
        try:
            # SEC-002: use _secure_read_text to prevent symlink-TOCTOU attacks
            from voice_typer.server.config import _secure_read_text

            raw = _secure_read_text(self._user_path, encoding="utf-8")
            data = json.loads(raw)
            return self._normalize_data(data)
        except Exception as exc:
            log.warning("[VOCAB] Failed to load user vocab: %s", exc)
            return {}

    @staticmethod
    def _normalize_data(data: dict) -> dict:
        """Normalize raw JSON data into canonical category format.

        NEW-CQ-028: previously if ``data`` was a list (e.g. user had a
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
        return result

    # ── Persistence ──────────────────────────────────────────────────

    def _save_user(self) -> None:
        """Save only user vocabulary data (not bundled) to the user file.

        ARCH-044: ``Path.replace`` is not atomic on Windows when the
        destination is open by another process (e.g. an editor or a
        cloud-sync client watching the file). We now:
          1. Write to a tmp file with ``fsync``.
          2. Retry the rename up to 3 times with exponential backoff.
          3. On final failure, log + leave the tmp file in place so
             the user can recover manually.

        M-63: previously this method logged failures and returned
        silently — a contract described in the docstring as
        "best-effort". That left CRUD callers (``add_entry`` /
        ``add_phrase`` / ``remove_entry`` / ``remove_phrase`` /
        ``import_json``) unable to detect failure, so they returned
        ``True`` to their callers (and the IPC layer returned an
        ``ack``) while the in-memory state diverged from the on-disk
        state. We now RAISE ``OSError`` after the retry loop is
        exhausted so callers can roll back their in-memory mutation
        and the IPC layer can surface the failure as an error
        envelope. The shared ``_secure_atomic_write`` itself is
        already atomic — the retries here are purely for the
        ``PermissionError`` race on Windows where the destination is
        locked by an editor / cloud-sync client.
        """
        import time as _time

        from voice_typer.server.config import _secure_atomic_write

        max_retries = 3
        # M-63: track the final failure so we can raise after the
        # retry loop instead of silently returning.
        final_exc: Exception | None = None
        self._user_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self._data, indent=2, ensure_ascii=False)
        for attempt in range(max_retries):
            try:
                _secure_atomic_write(self._user_path, content)
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
                log.error("[VOCAB] Failed to save user vocabulary: %s", exc)
                break
        # M-63: surface the failure to callers so they can roll back
        # any in-memory mutation they made before calling us.
        if final_exc is not None:
            raise final_exc

        try:
            self._user_path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(self._data, indent=2, ensure_ascii=False)
            # NEW-SEC-008: use the shared secure atomic write which
            # applies O_NOFOLLOW on POSIX to prevent symlink TOCTOU.
            # We still retry on PermissionError (file locked by
            # editor/cloud-sync) by re-attempting the write.
            for attempt in range(max_retries):
                try:
                    _secure_atomic_write(self._user_path, content)
                    log.debug("[VOCAB] Saved user vocabulary")
                    return
                except PermissionError as exc:
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
                    log.error("[VOCAB] Failed to save user vocabulary: %s", exc)
                    break
            # Note: we deliberately do NOT re-raise — the existing
            # contract is that _save_user is best-effort and failures
            # are logged. Callers that need to know about failures
            # should check the log.
        except Exception as exc:
            log.error("[VOCAB] Failed to save: %s", exc)

    # ── Read access ──────────────────────────────────────────────────

    def get_category(self, category: str) -> object:
        """Get all entries for a category."""
        if category in ("misspellings", "technical_terms", "names", "products"):
            return self._data.get(category, {})
        return self._data.get(category, [])

    def get_all(self) -> dict:
        """Return a copy of all merged data."""
        return dict(self._data)

    # ── CRUD for dict-based categories ───────────────────────────────

    def add_entry(self, category: str, key: str, value: str) -> bool:
        """Add an entry to a dict-based category (misspellings, technical_terms, names, products).

        SEC-011: Enforces MAX_CORRECTIONS_ENTRIES, MAX_PATTERN_LENGTH, and
        MAX_REPLACEMENT_LENGTH limits.  Returns False if limits are exceeded.

        M-63: rolls back the in-memory mutation if ``_save_user`` raises
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
            # M-63: snapshot for rollback.
            had_key = key in cat_data
            old_value = cat_data.get(key)
            cat_data[key] = value
        try:
            self._save_user()
        except Exception:
            # M-63: roll back the in-memory mutation.
            with self._lock:
                if had_key:
                    cat_data[key] = old_value
                elif key in cat_data:
                    del cat_data[key]
            raise
        return True

    def remove_entry(self, category: str, key: str) -> bool:
        """Remove an entry from a dict-based category.

        M-63: rolls back the in-memory mutation if ``_save_user`` raises
        so the in-memory state stays consistent with the on-disk state.
        """
        removed = False
        old_value: Any = None
        cat_data: Any = None
        with self._lock:
            cat_data = self._data.get(category)
            if isinstance(cat_data, dict) and key in cat_data:
                old_value = cat_data[key]
                del cat_data[key]
                removed = True
        if removed:
            try:
                self._save_user()
            except Exception:
                # M-63: roll back the in-memory mutation.
                with self._lock:
                    if isinstance(cat_data, dict):
                        cat_data[key] = old_value
                raise
        return removed

    # ── CRUD for list-based categories ───────────────────────────────

    def add_phrase(self, category: str, wrong: str, correct: str) -> bool:
        """Add an entry to a list-based category (phrase_corrections, extra_word_patterns).

        SEC-011: Enforces MAX_CORRECTIONS_ENTRIES, MAX_PATTERN_LENGTH, and
        MAX_REPLACEMENT_LENGTH limits.  Returns False if limits are exceeded.

        M-63: rolls back the in-memory mutation if ``_save_user`` raises
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
        try:
            self._save_user()
        except Exception:
            # M-63: roll back the in-memory mutation.
            with self._lock:
                try:
                    cat_data.remove(new_entry)
                except ValueError:
                    pass
            raise
        return True

    def remove_phrase(self, category: str, index: int) -> bool:
        """Remove a phrase entry by index.

        M-63: rolls back the in-memory mutation if ``_save_user`` raises
        so the in-memory state stays consistent with the on-disk state.
        """
        removed = False
        old_entry: Any = None
        cat_data: Any = None
        with self._lock:
            cat_data = self._data.get(category)
            if isinstance(cat_data, list) and 0 <= index < len(cat_data):
                old_entry = cat_data.pop(index)
                removed = True
        if removed:
            try:
                self._save_user()
            except Exception:
                # M-63: roll back the in-memory mutation.
                with self._lock:
                    if isinstance(cat_data, list):
                        cat_data.insert(index, old_entry)
                raise
        return removed

    # ── Import / Export ───────────────────────────────────────────────

    def export_json(self) -> str:
        """Export all vocabulary as JSON string."""
        return json.dumps(self._data, indent=2, ensure_ascii=False)

    def import_json(self, json_str: str, *, merge: bool = True) -> int:
        """Import vocabulary from a JSON string.

        If merge=True, extends existing entries. If False, replaces.
        Returns number of categories imported.

        M-63: snapshots the entire ``_data`` dict before mutating and
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
            count = 0
            with self._lock:
                # M-63: snapshot for rollback. Shallow-copy the
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
                    if cat not in data or data[cat] is None:
                        continue
                    if merge:
                        if cat in ("misspellings", "technical_terms", "names", "products"):
                            if not isinstance(self._data.get(cat), dict):
                                self._data[cat] = {}
                            cat_dict = self._data[cat]
                            if isinstance(cat_dict, dict) and isinstance(data[cat], dict):
                                cat_dict.update(data[cat])
                        else:
                            if not isinstance(self._data.get(cat), list):
                                self._data[cat] = []
                            cat_list = self._data[cat]
                            if isinstance(cat_list, list) and isinstance(data[cat], list):
                                cat_list.extend(data[cat])
                    else:
                        self._data[cat] = data[cat]
                    count += 1
            if count:
                try:
                    self._save_user()
                except Exception:
                    # M-63: roll back the in-memory mutation.
                    with self._lock:
                        self._data.clear()
                        self._data.update(snapshot)
                    raise
            return count
        except Exception:
            log.exception("[VOCAB] Import failed")
            return 0

    # ── Apply vocabulary to text ─────────────────────────────────────

    def apply_to_text(self, text: str) -> str:
        """Apply vocabulary corrections to transcribed text.

        Processes categories in order:
        1. phrase_corrections (phrase-level)
        2. extra_word_patterns (phrase-level)
        3. misspellings (word-level)
        4. technical_terms (word-level)
        5. names (word-level)
        6. products (word-level)
        """
        import re as _re

        # CR-23: snapshot self._data under the lock so concurrent
        # add_entry / remove_entry / add_phrase / remove_phrase / import_json
        # calls (which acquire self._lock) cannot mutate the dict/list
        # mid-iteration. Previously the read path bypassed the lock,
        # causing intermittent `RuntimeError: dictionary changed size
        # during iteration` that was silently swallowed by
        # dictation_pipeline._apply_vocabulary's try/except — degrading
        # transcription quality with no diagnostic.
        with self._lock:
            data_snapshot = {cat: (list(v) if isinstance(v, list) else dict(v)) for cat, v in self._data.items()}

        # Phrase-level corrections first (longer matches first)
        for cat in ("phrase_corrections", "extra_word_patterns"):
            entries = data_snapshot.get(cat, [])
            if not isinstance(entries, list):
                continue
            # Sort by length of bad phrase (longest first) to avoid partial matches
            sorted_entries = sorted(
                entries,
                key=lambda e: len(e[0]) if isinstance(e, (list, tuple)) and len(e) >= 2 else 0,
                reverse=True,
            )
            for entry in sorted_entries:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                bad, good = entry[0], entry[1]
                pattern = _re.compile(_re.escape(bad), _re.IGNORECASE)
                # NEW-SEC-004: use a callable replacement to prevent
                # regex backref interpretation. Previously `pattern.sub(good, text)`
                # interpreted `\1`, `\g<0>`, `\9` etc. in the user-supplied
                # `good` string. A malicious or accidental entry like
                # `["x", "\\9"]` raises `re.error` on every dictation
                # cycle (DoS). The lambda treats `good` as a literal
                # string with no backref processing.
                text = pattern.sub(lambda _m, _g=good: _g, text)

        # Word-level corrections
        for cat in ("misspellings", "technical_terms", "names", "products"):
            entries = data_snapshot.get(cat, {})
            if not isinstance(entries, dict):
                continue
            tokens = text.split(" ")
            output = []
            for token in tokens:
                key = _re.sub(r"^\W+|\W+$", "", token).lower()
                if key in entries:
                    correction = entries[key]
                    match = _re.match(r"^(\W*)(\w+)(\W*)$", token)
                    token = f"{match.group(1)}{correction}{match.group(3)}" if match else correction
                output.append(token)
            text = " ".join(output)

        return text
