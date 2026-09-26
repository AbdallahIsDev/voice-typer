"""Bundled correction data load helpers."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from voice_typer.server.vocabulary import BUNDLED_CORRECTIONS_PATH as _BUNDLED_CORRECTIONS_PATH

log = logging.getLogger("voice_typer.server.text_cleanup")

# Question openers used by _looks_like_question().
_QUESTION_OPENERS = {
    "am",
    "are",
    "can",
    "could",
    "did",
    "do",
    "does",
    "has",
    "have",
    "is",
    "may",
    "should",
    "was",
    "were",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
    "why",
    "will",
    "would",
}

_INTENTIONAL_REPEAT_WORDS = {
    "no",
    "test",
    "very",
}

# The bundled corrections.json is the canonical source of corrections.

# When a corrections.json exists in the config directory (or at the path


class CorrectionsLoadError(RuntimeError):
    """Raised when external corrections could not be loaded."""


def _load_bundled_corrections():
    """Load the bundled ``corrections.json`` shipped with the app."""
    if not _BUNDLED_CORRECTIONS_PATH.exists():
        return {}, [], [], False, []
    try:
        # SEC-002: use _secure_read_text to prevent symlink-TOCTOU attacks
        from voice_typer.server.config import _secure_read_text

        raw = _secure_read_text(_BUNDLED_CORRECTIONS_PATH, encoding="utf-8")
        data = json.loads(raw)
        misspellings = dict(data.get("misspellings", {}))
        phrase_corrections = [
            tuple(item)
            for item in data.get("phrase_corrections", [])
            if isinstance(item, list | tuple) and len(item) == 2
        ]
        extra_word_patterns = [
            tuple(item)
            for item in data.get("extra_word_patterns", [])
            if isinstance(item, list | tuple) and len(item) == 2
        ]
        return misspellings, phrase_corrections, extra_word_patterns, True, []
    except Exception as exc:
        log.warning("[CLEANUP] Failed to load bundled corrections: %s", exc)
        return {}, [], [], False, [f"bundled: {exc}"]


def _load_user_corrections(
    config_dir: Path | None = None,
    corrections_path: str | None = None,
):
    """Load the user-provided corrections file (if present).

    Returns ``(path, misspellings, phrase_corrections, extra_word_patterns,
    """
    path: Path | None = None
    if corrections_path:
        path = Path(corrections_path)
    elif config_dir is not None:
        path = config_dir / "lausu-corrections.json"

    if path is None or not path.exists():
        return None, {}, [], [], set(), set(), False, []

    try:
        # use _secure_read_text to prevent symlink-TOCTOU attacks
        from voice_typer.server.config import _secure_read_text

        raw = _secure_read_text(path, encoding="utf-8")
        data = json.loads(raw)
        misspellings: dict[str, str] = {}
        if "misspellings" in data and isinstance(data["misspellings"], dict):
            misspellings = dict(data["misspellings"])
        phrase_corrections: list[tuple[str, str]] = []
        if "phrase_corrections" in data and isinstance(data["phrase_corrections"], list):
            phrase_corrections = [
                tuple(item) for item in data["phrase_corrections"] if isinstance(item, list | tuple) and len(item) == 2
            ]
        extra_word_patterns: list[tuple[str, str]] = []
        if "extra_word_patterns" in data and isinstance(data["extra_word_patterns"], list):
            extra_word_patterns = [
                tuple(item) for item in data["extra_word_patterns"] if isinstance(item, list | tuple) and len(item) == 2
            ]
        # extract optional Roman-numeral word-set extensions.
        roman_context_ext: set[str] = set()
        if "roman_numeral_context_words" in data and isinstance(data["roman_numeral_context_words"], list):
            roman_context_ext = {str(w).lower() for w in data["roman_numeral_context_words"] if isinstance(w, str)}
        roman_following_ext: set[str] = set()
        if "roman_numeral_following_words" in data and isinstance(data["roman_numeral_following_words"], list):
            roman_following_ext = {str(w).lower() for w in data["roman_numeral_following_words"] if isinstance(w, str)}
        return (
            path,
            misspellings,
            phrase_corrections,
            extra_word_patterns,
            roman_context_ext,
            roman_following_ext,
            True,
            [],
        )
    except Exception as e:
        log.warning("[CLEANUP] Failed to load corrections from %s: %s", path, e)
        return path, {}, [], [], set(), set(), False, [f"{path.name}: {e}"]


def _load_external_corrections(
    config_dir: Path | None = None,
    corrections_path: str | None = None,
):
    """Load corrections from an external JSON file."""
    bundled = _load_bundled_corrections()
    user = _load_user_corrections(config_dir, corrections_path)

    bundled_m, bundled_p, bundled_e, bundled_loaded, bundled_errors = bundled
    (
        user_path,
        user_m,
        user_p,
        user_e,
        user_roman_ctx,
        user_roman_fol,
        user_loaded,
        user_errors,
    ) = user

    load_errors: list[str] = list(bundled_errors) + list(user_errors)
    loaded_any = bool(bundled_loaded or user_loaded)

    # Merge: bundled creates fresh containers; user extends them.
    misspellings: dict[str, str] = dict(bundled_m)
    misspellings.update(user_m)
    phrase_corrections: list[tuple[str, str]] = list(bundled_p) + list(user_p)
    extra_word_patterns: list[tuple[str, str]] = list(bundled_e) + list(user_e)

    # refresh the user-extension state for the Roman-numeral word
    global _user_roman_numeral_context_extensions
    global _user_roman_numeral_following_extensions
    _user_roman_numeral_context_extensions = set(user_roman_ctx)
    _user_roman_numeral_following_extensions = set(user_roman_fol)

    if user_loaded and user_path is not None:
        log.info(
            "[CLEANUP] Loaded user corrections from %s (%d misspellings, %d phrases, %d extra-word patterns)",
            user_path,
            len(misspellings),
            len(phrase_corrections),
            len(extra_word_patterns),
        )

    # raise whenever ANY load error occurred, previously the
    if load_errors:
        raise CorrectionsLoadError(
            "Corrections files existed but were malformed or could not be loaded: " + "; ".join(load_errors)
        )
    if not loaded_any:
        return None

    # SEC-010/SEC-011: Cap corrections to prevent ReDoS and resource exhaustion
    max_corrections_entries = 5000
    max_pattern_length = 200
    max_replacement_length = 500

    # the 3 per-correction-type truncation blocks + 3 per-correction-
    misspellings = _truncate_corrections(
        list(misspellings.items()),
        max_corrections_entries,
        "misspellings",
    )
    phrase_corrections = _truncate_corrections(
        phrase_corrections,
        max_corrections_entries,
        "phrase corrections",
    )
    extra_word_patterns = _truncate_corrections(
        extra_word_patterns,
        max_corrections_entries,
        "extra word patterns",
    )

    misspellings = dict(
        _filter_corrections_by_length(
            misspellings,
            max_pattern_length,
            max_replacement_length,
            "misspellings",
        )
    )
    phrase_corrections = _filter_corrections_by_length(
        phrase_corrections,
        max_pattern_length,
        max_replacement_length,
        "phrase corrections",
    )
    extra_word_patterns = _filter_corrections_by_length(
        extra_word_patterns,
        max_pattern_length,
        max_replacement_length,
        "extra word patterns",
    )

    return misspellings, phrase_corrections, extra_word_patterns


def _truncate_corrections(
    items: list[tuple[str, str]],
    max_count: int,
    label: str,
) -> list[tuple[str, str]]:
    """cap a corrections list at ``max_count`` entries."""
    if len(items) <= max_count:
        return items
    log.warning(
        "[CLEANUP] Too many %s (%d > %d), truncating",
        label,
        len(items),
        max_count,
    )
    return items[:max_count]


def _filter_corrections_by_length(
    items: Iterable[tuple[str, str]],
    max_pattern_length: int,
    max_replacement_length: int,
    label: str,
) -> list[tuple[str, str]]:
    """drop corrections whose pattern OR replacement exceeds the"""
    dropped = 0
    kept: list[tuple[str, str]] = []
    for pattern, replacement in items:
        if len(pattern) > max_pattern_length or len(replacement) > max_replacement_length:
            dropped += 1
            continue
        kept.append((pattern, replacement))
    if dropped:
        log.warning(
            "[CLEANUP] Dropped %d %s exceeding length limits (pattern > %d or replacement > %d)",
            dropped,
            label,
            max_pattern_length,
            max_replacement_length,
        )
    return kept


def _active_corrections(
    config_dir: Path | None = None,
    corrections_path: str | None = None,
):
    """Return the active corrections, loading from bundled corrections.json."""
    external = _load_external_corrections(config_dir, corrections_path)
    if external is not None:
        return external
    # Minimal fallback if corrections.json is somehow missing
    return {}, [], []


# Roman numeral context words that precede lowercase 'i'.
_ROMAN_NUMERAL_CONTEXT_WORDS = {
    "section",
    "chapter",
    "part",
    "book",
    "henry",
    "louis",
    "richard",
    "king",
    "pope",
    "volume",
    "page",
    "act",
    "scene",
    "title",
    "appendix",
    "amendment",
    "article",
    "rule",
    "step",
    "phase",
    "stage",
}

_ROMAN_NUMERAL_FOLLOWING_WORDS = {
    "through",
    "to",
    "and",
    "or",
    "thru",
    "vs",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
}

# user-provided extensions to the bundled Roman-numeral word
_user_roman_numeral_context_extensions: set[str] = set()
_user_roman_numeral_following_extensions: set[str] = set()

# precompiled regex for standalone 'i' (not preceded or followed by
_PRONOUN_I_RE = re.compile(r"(?<![a-zA-Z])i(?![a-zA-Z])")


def _prev_word_ending_at(text: str, end_idx: int) -> str:
    """Return the lowercased word immediately preceding ``end_idx``."""
    i = end_idx - 1
    # Skip trailing whitespace (mirrors .rstrip()).
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0:
        return ""
    # Original guard: preceding[-1].isalpha(), a digit/punctuation
    if not text[i].isalpha():
        return ""
    word_end = i + 1  # exclusive
    # Walk back to the start of the word (mirrors rsplit(None, 1)[-1]).
    while i >= 0 and not text[i].isspace():
        i -= 1
    return text[i + 1 : word_end].lower()


def _next_word_starting_at(text: str, start_idx: int) -> str:
    """Return the lowercased word immediately following ``start_idx``."""
    n = len(text)
    i = start_idx
    # Skip leading whitespace (mirrors .lstrip()).
    while i < n and text[i].isspace():
        i += 1
    if i >= n:
        return ""
    # Original loop: stop at the first non-alpha char.
    if not text[i].isalpha():
        return ""
    word_start = i
    while i < n and text[i].isalpha():
        i += 1
    return text[word_start:i].lower()


def _capitalize_pronoun_i(text: str) -> str:
    """Capitalize the pronoun 'i' but not Roman numeral 'i'."""
    # Fast path: no standalone-'i' candidates at all.
    if "i" not in text:
        return text
    matches = list(_PRONOUN_I_RE.finditer(text))
    if not matches:
        return text
    # Mutate a mutable buffer in place, no per-match string allocation
    chars = list(text)
    # check both the bundled defaults AND the user-provided
    context_words = _ROMAN_NUMERAL_CONTEXT_WORDS | _user_roman_numeral_context_extensions
    following_words = _ROMAN_NUMERAL_FOLLOWING_WORDS | _user_roman_numeral_following_extensions
    for match in matches:
        start = match.start()
        prev_word = _prev_word_ending_at(text, start)
        if prev_word and prev_word in context_words:
            continue  # keep lowercase
        next_word = _next_word_starting_at(text, match.end())
        if next_word and next_word in following_words:
            continue  # keep lowercase
        chars[start] = "I"
    return "".join(chars)
