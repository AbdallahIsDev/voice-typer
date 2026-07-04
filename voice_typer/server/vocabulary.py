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
from pathlib import Path
from typing import Any, Optional

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

    def __init__(self, config_dir: Optional[Path] = None, bundled_path: Optional[Path] = None):
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
            return {cat: ({} if cat in ("misspellings", "technical_terms", "names", "products") else [])
                    for cat in CATEGORIES}
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
        """
        import os
        import time as _time
        from voice_typer.server.config import _secure_atomic_write

        max_retries = 3
        last_exc: Exception | None = None
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
                    last_exc = exc
                    if attempt < max_retries - 1:
                        backoff = 0.05 * (2 ** attempt)  # 50ms, 100ms, 200ms
                        log.warning(
                            "[VOCAB] PermissionError on save (attempt %d/%d), "
                            "retrying in %.0fms: %s",
                            attempt + 1, max_retries, backoff * 1000, exc,
                        )
                        _time.sleep(backoff)
                    else:
                        log.error(
                            "[VOCAB] Failed to save user vocabulary after %d "
                            "attempts: %s",
                            max_retries, exc,
                        )
                except OSError as exc:
                    last_exc = exc
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
        """
        if category not in ("misspellings", "technical_terms", "names", "products"):
            log.error("[VOCAB] Cannot add dict entry to list category %s", category)
            return False
        if len(key) > MAX_PATTERN_LENGTH:
            log.warning("[VOCAB] Pattern exceeds MAX_PATTERN_LENGTH (%d > %d), rejecting",
                        len(key), MAX_PATTERN_LENGTH)
            return False
        if len(value) > MAX_REPLACEMENT_LENGTH:
            log.warning("[VOCAB] Replacement exceeds MAX_REPLACEMENT_LENGTH (%d > %d), rejecting",
                        len(value), MAX_REPLACEMENT_LENGTH)
            return False
        if not isinstance(self._data.get(category), dict):
            self._data[category] = {}
        cat_data = self._data[category]
        if not isinstance(cat_data, dict):
            return False
        if len(cat_data) >= MAX_CORRECTIONS_ENTRIES:
            log.warning("[VOCAB] Category %s has reached MAX_CORRECTIONS_ENTRIES (%d), rejecting",
                        category, MAX_CORRECTIONS_ENTRIES)
            return False
        cat_data[key] = value
        self._save_user()
        return True

    def remove_entry(self, category: str, key: str) -> bool:
        """Remove an entry from a dict-based category."""
        cat_data = self._data.get(category)
        if isinstance(cat_data, dict) and key in cat_data:
            del cat_data[key]
            self._save_user()
            return True
        return False

    # ── CRUD for list-based categories ───────────────────────────────

    def add_phrase(self, category: str, wrong: str, correct: str) -> bool:
        """Add an entry to a list-based category (phrase_corrections, extra_word_patterns).

        SEC-011: Enforces MAX_CORRECTIONS_ENTRIES, MAX_PATTERN_LENGTH, and
        MAX_REPLACEMENT_LENGTH limits.  Returns False if limits are exceeded.
        """
        if category not in ("phrase_corrections", "extra_word_patterns"):
            log.error("[VOCAB] Cannot add list entry to dict category %s", category)
            return False
        if len(wrong) > MAX_PATTERN_LENGTH:
            log.warning("[VOCAB] Phrase pattern exceeds MAX_PATTERN_LENGTH (%d > %d), rejecting",
                        len(wrong), MAX_PATTERN_LENGTH)
            return False
        if len(correct) > MAX_REPLACEMENT_LENGTH:
            log.warning("[VOCAB] Phrase replacement exceeds MAX_REPLACEMENT_LENGTH (%d > %d), rejecting",
                        len(correct), MAX_REPLACEMENT_LENGTH)
            return False
        if not isinstance(self._data.get(category), list):
            self._data[category] = []
        cat_data = self._data[category]
        if not isinstance(cat_data, list):
            return False
        if len(cat_data) >= MAX_CORRECTIONS_ENTRIES:
            log.warning("[VOCAB] Category %s has reached MAX_CORRECTIONS_ENTRIES (%d), rejecting",
                        category, MAX_CORRECTIONS_ENTRIES)
            return False
        cat_data.append([wrong, correct])
        self._save_user()
        return True

    def remove_phrase(self, category: str, index: int) -> bool:
        """Remove a phrase entry by index."""
        cat_data = self._data.get(category)
        if isinstance(cat_data, list) and 0 <= index < len(cat_data):
            del cat_data[index]
            self._save_user()
            return True
        return False

    # ── Import / Export ───────────────────────────────────────────────

    def export_json(self) -> str:
        """Export all vocabulary as JSON string."""
        return json.dumps(self._data, indent=2, ensure_ascii=False)

    def import_json(self, json_str: str, *, merge: bool = True) -> int:
        """Import vocabulary from a JSON string.

        If merge=True, extends existing entries. If False, replaces.
        Returns number of categories imported.
        """
        try:
            data = json.loads(json_str)
            data = self._normalize_data(data)
            count = 0
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
                self._save_user()
            return count
        except Exception as exc:
            log.error("[VOCAB] Import failed: %s", exc)
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

        # Phrase-level corrections first (longer matches first)
        for cat in ("phrase_corrections", "extra_word_patterns"):
            entries = self._data.get(cat, [])
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
                text = pattern.sub(lambda _m: good, text)

        # Word-level corrections
        for cat in ("misspellings", "technical_terms", "names", "products"):
            entries = self._data.get(cat, {})
            if not isinstance(entries, dict):
                continue
            tokens = text.split(" ")
            output = []
            for token in tokens:
                key = _re.sub(r"^\W+|\W+$", "", token).lower()
                if key in entries:
                    correction = entries[key]
                    match = _re.match(r"^(\W*)(\w+)(\W*)$", token)
                    if match:
                        token = f"{match.group(1)}{correction}{match.group(3)}"
                    else:
                        token = correction
                output.append(token)
            text = " ".join(output)

        return text
