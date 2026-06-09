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
from typing import Optional

log = logging.getLogger(__name__)

VOCAB_FILENAME = "voice-typer-vocabulary.json"

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


class VocabularyManager:
    """Manages custom vocabulary entries across 6 categories."""

    def __init__(self, config_dir: Optional[Path] = None, bundled_path: Optional[Path] = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir
            config_dir = _config_dir()
        self._config_dir = config_dir
        self._user_path = config_dir / VOCAB_FILENAME

        if bundled_path is None:
            bundled_path = Path(__file__).parent / "corrections.json"
        self._bundled_path = bundled_path

        # Active merged data: {category: data}
        self._data: dict[str, object] = {}
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
            data = json.loads(self._bundled_path.read_text(encoding="utf-8"))
            return self._normalize_data(data)
        except Exception as exc:
            log.warning("[VOCAB] Failed to load bundled: %s", exc)
            return {}

    def _load_user(self) -> dict:
        """Load the user vocabulary file."""
        if not self._user_path.exists():
            return {}
        try:
            data = json.loads(self._user_path.read_text(encoding="utf-8"))
            return self._normalize_data(data)
        except Exception as exc:
            log.warning("[VOCAB] Failed to load user vocab: %s", exc)
            return {}

    @staticmethod
    def _normalize_data(data: dict) -> dict:
        """Normalize raw JSON data into canonical category format."""
        result = {}
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
        """Save only user vocabulary data (not bundled) to the user file."""
        try:
            self._user_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._user_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._user_path)
            log.debug("[VOCAB] Saved user vocabulary")
        except Exception as exc:
            log.error("[VOCAB] Failed to save: %s", exc)

    # ── Read access ──────────────────────────────────────────────────

    def get_category(self, category: str) -> object:
        """Get all entries for a category."""
        return self._data.get(category, {} if category in ("misspellings", "technical_terms", "names", "products") else [])

    def get_all(self) -> dict:
        """Return a copy of all merged data."""
        return dict(self._data)

    # ── CRUD for dict-based categories ───────────────────────────────

    def add_entry(self, category: str, key: str, value: str) -> bool:
        """Add an entry to a dict-based category (misspellings, technical_terms, names, products)."""
        if category not in ("misspellings", "technical_terms", "names", "products"):
            log.error("[VOCAB] Cannot add dict entry to list category %s", category)
            return False
        if not isinstance(self._data.get(category), dict):
            self._data[category] = {}
        self._data[category][key] = value
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
        """Add an entry to a list-based category (phrase_corrections, extra_word_patterns)."""
        if category not in ("phrase_corrections", "extra_word_patterns"):
            log.error("[VOCAB] Cannot add list entry to dict category %s", category)
            return False
        if not isinstance(self._data.get(category), list):
            self._data[category] = []
        self._data[category].append([wrong, correct])
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
                        self._data[cat].update(data[cat])
                    else:
                        if not isinstance(self._data.get(cat), list):
                            self._data[cat] = []
                        self._data[cat].extend(data[cat])
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
            sorted_entries = sorted(entries, key=lambda e: len(e[0]) if isinstance(e, (list, tuple)) and len(e) >= 2 else 0, reverse=True)
            for entry in sorted_entries:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                bad, good = entry[0], entry[1]
                pattern = _re.compile(_re.escape(bad), _re.IGNORECASE)
                text = pattern.sub(good, text)

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
