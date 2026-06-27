"""Lightweight cleanup for raw speech-to-text output."""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)
# Question openers used by _looks_like_question().
# "how" and "what" are EXCLUDED because they frequently start
# declarative sentences (e.g. "How to install Python",
# "What I did yesterday") where a question mark would be wrong.
_QUESTION_OPENERS = {
    "am", "are", "can", "could", "did", "do", "does", "has", "have",
    "is", "may", "should", "was", "were", "when",
    "where", "which", "who", "whom", "whose", "why", "will", "would",
}

_INTENTIONAL_REPEAT_WORDS = {
    "no",
    "test",
    "very",
}

# ─── Corrections loaded from corrections.json ──────────────────────────
#
# The bundled corrections.json is the canonical source of corrections.
# The built-in dicts below have been removed to avoid duplication (P4 #29).
# A minimal fallback is provided in _active_corrections() if corrections.json
# is somehow missing.

_WHISPER_MISSPELLINGS: dict[str, str] = {}
_WHISPER_PHRASE_CORRECTIONS: list[tuple[str, str]] = []
_COMMON_EXTRA_WORD_PATTERNS: list[tuple[str, str]] = []

# ─── External corrections loader ─────────────────────────────────────────
# When a corrections.json exists in the config directory (or at the path
# specified by config.corrections_path), its entries OVERRIDE the built-in
# defaults.  This allows users to tailor corrections for their model/version
# without editing source code.

# ARCH-028: import the shared constant from vocabulary.py instead of
# re-declaring it. If one file moves, the other stays in sync.
from voice_typer.server.vocabulary import BUNDLED_CORRECTIONS_PATH as _BUNDLED_CORRECTIONS_PATH


class CorrectionsLoadError(RuntimeError):
    """Raised when external corrections could not be loaded.

    ARCH-029: previously ``_load_external_corrections`` returned ``None``
    for both "no file" and "load failed", and the caller couldn't
    distinguish them. We now raise this typed exception on load
    failure; "no file" still returns ``None``.
    """


def _load_external_corrections(
    config_dir: Path | None = None,
    corrections_path: str | None = None,
):
    """Load corrections from an external JSON file.

    Loads bundled corrections.json first, then merges user-provided
    file on top.  Returns (misspellings, phrase_corrections, extra_word_patterns)
    from the external file, or None if no corrections file exists.

    ARCH-029: raises ``CorrectionsLoadError`` when a file exists but
    could not be parsed (was previously a silent ``None`` return).
    """
    loaded_any = False
    # ARCH-029: track the last load error so callers can distinguish
    # "no file" (None, no error) from "file failed to load" (raise).
    load_errors: list[str] = []

    # Start with bundled corrections
    misspellings: dict[str, str] = {}
    phrase_corrections: list[tuple[str, str]] = []
    extra_word_patterns: list[tuple[str, str]] = []

    if _BUNDLED_CORRECTIONS_PATH.exists():
        try:
            # SEC-002: use _secure_read_text to prevent symlink-TOCTOU attacks
            from voice_typer.server.config import _secure_read_text
            raw = _secure_read_text(_BUNDLED_CORRECTIONS_PATH, encoding="utf-8")
            data = json.loads(raw)
            misspellings = dict(data.get("misspellings", {}))
            phrase_corrections = [
                tuple(item) for item in data.get("phrase_corrections", [])
                if isinstance(item, (list, tuple)) and len(item) == 2
            ]
            extra_word_patterns = [
                tuple(item) for item in data.get("extra_word_patterns", [])
                if isinstance(item, (list, tuple)) and len(item) == 2
            ]
            loaded_any = True
        except Exception as exc:
            log.warning("[CLEANUP] Failed to load bundled corrections: %s", exc)
            load_errors.append(f"bundled: {exc}")

    # Merge user-provided corrections on top
    path = None
    if corrections_path:
        path = Path(corrections_path)
    elif config_dir is not None:
        path = config_dir / "voice-typer-corrections.json"

    if path is not None and path.exists():
        try:
            # SEC-002: use _secure_read_text to prevent symlink-TOCTOU attacks
            from voice_typer.server.config import _secure_read_text
            raw = _secure_read_text(path, encoding="utf-8")
            data = json.loads(raw)
            if "misspellings" in data and isinstance(data["misspellings"], dict):
                misspellings.update(data["misspellings"])
            if "phrase_corrections" in data and isinstance(data["phrase_corrections"], list):
                phrase_corrections.extend(
                    tuple(item) for item in data["phrase_corrections"]
                    if isinstance(item, (list, tuple)) and len(item) == 2
                )
            if "extra_word_patterns" in data and isinstance(data["extra_word_patterns"], list):
                extra_word_patterns.extend(
                    tuple(item) for item in data["extra_word_patterns"]
                    if isinstance(item, (list, tuple)) and len(item) == 2
                )
            log.info("[CLEANUP] Loaded user corrections from %s (%d misspellings, "
                     "%d phrases, %d extra-word patterns)",
                     path, len(misspellings), len(phrase_corrections), len(extra_word_patterns))
            loaded_any = True
        except Exception as e:
            log.warning("[CLEANUP] Failed to load corrections from %s: %s", path, e)
            load_errors.append(f"{path.name}: {e}")

    if not loaded_any:
        # ARCH-029: if we tried to load files and they all failed,
        # raise so the caller can surface the error. If no files
        # existed in the first place, return None (silent fallback).
        if load_errors:
            raise CorrectionsLoadError(
                "Corrections file(s) existed but could not be loaded: "
                + "; ".join(load_errors)
            )
        return None

    # SEC-010/SEC-011: Cap corrections to prevent ReDoS and resource exhaustion
    MAX_CORRECTIONS_ENTRIES = 5000
    MAX_PATTERN_LENGTH = 200
    MAX_REPLACEMENT_LENGTH = 500

    MAX_MISSPELLINGS = MAX_CORRECTIONS_ENTRIES
    MAX_PHRASE_CORRECTIONS = MAX_CORRECTIONS_ENTRIES
    MAX_EXTRA_WORD_PATTERNS = MAX_CORRECTIONS_ENTRIES

    if len(misspellings) > MAX_MISSPELLINGS:
        log.warning("[CLEANUP] Too many misspellings (%d > %d), truncating", len(misspellings), MAX_MISSPELLINGS)
        misspellings = dict(list(misspellings.items())[:MAX_MISSPELLINGS])
    if len(phrase_corrections) > MAX_PHRASE_CORRECTIONS:
        log.warning("[CLEANUP] Too many phrase corrections, truncating")
        phrase_corrections = phrase_corrections[:MAX_PHRASE_CORRECTIONS]
    if len(extra_word_patterns) > MAX_EXTRA_WORD_PATTERNS:
        log.warning("[CLEANUP] Too many extra word patterns, truncating")
        extra_word_patterns = extra_word_patterns[:MAX_EXTRA_WORD_PATTERNS]

    # SEC-011: Validate string lengths — patterns and replacements have
    # separate limits to prevent ReDoS (long patterns cause expensive
    # regex backtracking) and resource exhaustion (long replacements
    # cause excessive memory/CPU during substitution).
    _dropped_pattern = 0
    _dropped_replacement = 0
    misspellings_filtered = {}
    for k, v in misspellings.items():
        if len(k) > MAX_PATTERN_LENGTH:
            _dropped_pattern += 1
            continue
        if len(v) > MAX_REPLACEMENT_LENGTH:
            _dropped_replacement += 1
            continue
        misspellings_filtered[k] = v
    misspellings = misspellings_filtered
    if _dropped_pattern:
        log.warning("[CLEANUP] Dropped %d misspellings with pattern > %d chars", _dropped_pattern, MAX_PATTERN_LENGTH)
    if _dropped_replacement:
        log.warning("[CLEANUP] Dropped %d misspellings with replacement > %d chars", _dropped_replacement, MAX_REPLACEMENT_LENGTH)

    _dropped_phrase = 0
    filtered_phrases = []
    for b, g in phrase_corrections:
        if len(b) > MAX_PATTERN_LENGTH or len(g) > MAX_REPLACEMENT_LENGTH:
            _dropped_phrase += 1
            continue
        filtered_phrases.append((b, g))
    phrase_corrections = filtered_phrases
    if _dropped_phrase:
        log.warning("[CLEANUP] Dropped %d phrase corrections exceeding length limits", _dropped_phrase)

    _dropped_extra = 0
    filtered_extra = []
    for b, g in extra_word_patterns:
        if len(b) > MAX_PATTERN_LENGTH:
            _dropped_extra += 1
            continue
        if len(g) > MAX_REPLACEMENT_LENGTH:
            _dropped_extra += 1
            continue
        filtered_extra.append((b, g))
    extra_word_patterns = filtered_extra
    if _dropped_extra:
        log.warning("[CLEANUP] Dropped %d extra word patterns exceeding length limits", _dropped_extra)

    return misspellings, phrase_corrections, extra_word_patterns


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


# ─── Active corrections (initialized to built-in defaults) ──────────────
# Updated by configure_corrections() when an external corrections file
# is available.  The cleanup functions below read from these instead of
# the built-in dicts directly.

_active_misspellings: dict[str, str] = {}
_active_phrases: list[tuple[str, str]] = []
_active_extra_words: list[tuple[str, str]] = []
# ARCH-027: guard the three module-level mutables with a lock so
# concurrent dictations don't clobber each other. The proper fix is
# to move these into a TextCleanupService instance; for now the lock
# prevents the worst race (two threads each replacing the dict mid-
# cleanup of the other). The instance refactor is deferred because
# it touches ~20 call sites.
_active_state_lock = __import__("threading").Lock()

# ARCH-031: cache of compiled regex patterns for phrase corrections.
# Keyed on the (lowercased) phrase string; value is a compiled regex
# with re.IGNORECASE.
# SEC-011 (revised): The cache now uses collections.OrderedDict with
# TRUE LRU eviction (move_to_end on cache hit) at a max size of 5000
# to prevent unbounded memory growth if many distinct phrases are
# processed over the lifetime of the process.
#
# The previous implementation used a plain dict and evicted the
# oldest INSERTED entry on overflow (next(iter(dict))). That was
# FIFO eviction, not LRU — a hot phrase inserted early would be
# evicted even if it was accessed frequently. True LRU requires
# moving the entry to the end of the insertion order on every cache
# hit, which OrderedDict.move_to_end does in O(1).
# See FORENSIC_REVIEW_COMPLETE.md → SEC-011.
import collections as _collections
_PHRASE_PATTERN_CACHE_MAXSIZE = 5000
_phrase_pattern_cache: "_collections.OrderedDict[str, re.Pattern[str]]" = _collections.OrderedDict()


def _get_compiled_phrase_pattern(phrase: str) -> "re.Pattern[str]":
    """Return a compiled, case-insensitive regex for matching ``phrase``.

    Compiled once per phrase and cached. SEC-011: implements TRUE LRU
    eviction (via OrderedDict.move_to_end on cache hit) when the cache
    exceeds _PHRASE_PATTERN_CACHE_MAXSIZE entries, preventing unbounded
    memory growth from a large or frequently-changing corrections file.
    """
    cached = _phrase_pattern_cache.get(phrase)
    if cached is not None:
        # SEC-011: mark as recently used so LRU eviction keeps it.
        _phrase_pattern_cache.move_to_end(phrase)
        return cached
    compiled = re.compile(re.escape(phrase), re.IGNORECASE)
    # SEC-011: Evict least-recently-used entry when cache exceeds max size.
    # OrderedDict.popitem(last=False) removes the oldest entry (the one
    # at the front of the insertion order, which is the LRU entry
    # because every cache hit moves the entry to the back).
    if len(_phrase_pattern_cache) >= _PHRASE_PATTERN_CACHE_MAXSIZE:
        try:
            _phrase_pattern_cache.popitem(last=False)
        except KeyError:
            pass
    _phrase_pattern_cache[phrase] = compiled
    return compiled


def configure_corrections(
    config_dir: Path | None = None,
    corrections_path: str | None = None,
) -> str | None:
    """Load corrections from external JSON (if present).

    When an external file exists and is valid, its entries replace the
    built-in defaults.  Otherwise the built-in dicts are restored.
    Call this once at startup so that ``clean_transcribed_text`` uses
    the user-provided corrections.

    ARCH-004: returns an error message string if the external file
    exists but could not be loaded (malformed JSON, permission error,
    etc.), or None on success / no-file.  Callers can surface this
    to the user via a tray notification so they know why their
    corrections aren't taking effect.
    """
    global _active_misspellings, _active_phrases, _active_extra_words

    # Determine the user corrections path (mirrors _load_external_corrections)
    user_path = None
    if corrections_path:
        user_path = Path(corrections_path)
    elif config_dir is not None:
        user_path = config_dir / "voice-typer-corrections.json"

    # Check if the user file exists but is unloadable
    error_msg: str | None = None
    if user_path is not None and user_path.exists():
        try:
            # SEC-002: use _secure_read_text to prevent symlink-TOCTOU attacks
            from voice_typer.server.config import _secure_read_text
            raw = _secure_read_text(user_path, encoding="utf-8")
            json.loads(raw)
        except Exception as e:
            error_msg = f"Corrections file {user_path.name} is malformed: {e}"
            log.warning("[CLEANUP] %s", error_msg)

    result = _active_corrections(config_dir, corrections_path)
    # ARCH-027: take the lock when replacing the three module-level
    # mutables so a concurrent cleanup() call doesn't see a half-
    # replaced state.
    with _active_state_lock:
        _active_misspellings, _active_phrases, _active_extra_words = result
    return error_msg


def clean_transcribed_text(
    text: str,
    *,
    auto_punctuation: bool = False,
    skip_corrections: bool = False,
) -> str:
    """Apply conservative cleanup without changing the user's meaning.

    ARCH-009: when ``skip_corrections=True``, the misspelling, phrase,
    and extra-word corrections are skipped.  This is used when
    ``VocabularyManager`` will apply the same corrections later in the
    pipeline (avoiding double-application).  The structural cleanup
    (spacing, self-corrections, capitalization) always runs.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""
    cleaned = _normalize_spacing(cleaned)
    cleaned = _clean_self_corrections(cleaned)
    cleaned = _remove_adjacent_duplicate_phrases(cleaned)
    cleaned = _remove_near_duplicate_words(cleaned)
    if not skip_corrections:
        cleaned = _fix_common_misspellings(cleaned)
        cleaned = _correct_whisper_phrases(cleaned)
        cleaned = _remove_extra_words(cleaned)
    cleaned = _capitalize_sentences(cleaned)
    # M2: Fix file extensions AFTER capitalization to prevent "features.Md" bug
    cleaned = _fix_file_extensions(cleaned)
    cleaned = _capitalize_pronoun_i(cleaned)
    # NOTE: Auto-punctuation is OFF by default. Enable via config.
    # It runs AFTER template matching in the pipeline.
    if auto_punctuation:
        cleaned = _add_safe_terminal_punctuation(cleaned)
    return cleaned


# PERF-004: precompile all regex patterns at module level to avoid
# recompilation on every call. Each regex was previously compiled
# inline inside the function body — this is wasteful for functions
# called per-chunk in the transcription pipeline.
_RE_SPACING_WS = re.compile(r"\s+")
_RE_SPACING_PUNCT_BEFORE = re.compile(r"\s+([,.;:!?])")
_RE_SPACING_PUNCT_AFTER = re.compile(r"([,.;:!?])(?=[^\s,.;:!?])")
# PERF-PIPE: precompile the regex used in _token_key at module level.
# This is called thousands of times per cleanup pass.
_RE_TOKEN_KEY = re.compile(r"^\W+|\W+$")
# _fix_file_extensions compiled pattern
_RE_FILE_EXT = re.compile(r'(\w+)\.\s+([a-zA-Z]{2,4})\b')


def _normalize_spacing(text: str) -> str:
    # PERF-004: use precompiled patterns
    text = _RE_SPACING_WS.sub(" ", text).strip()
    text = _RE_SPACING_PUNCT_BEFORE.sub(r"\1", text)
    text = _RE_SPACING_PUNCT_AFTER.sub(r"\1 ", text)
    return text.strip()


def _clean_self_corrections(text: str) -> str:
    """Remove self-correction patterns like 'talk talking' → 'talking'."""
    tokens = text.split(" ")
    output = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            key1 = _token_key(tokens[i])
            key2 = _token_key(tokens[i + 1])
            if key1 and key2 and key1 != key2:
                # Direct prefix/suffix match (e.g., "talk" → "talking")
                if key2.startswith(key1) or key1.startswith(key2):
                    output.append(tokens[i + 1])
                    i += 2
                    continue
                # Shared root with common prefix of 4+ chars
                if len(key1) >= 4 and len(key2) >= 4:
                    common = 0
                    for a, b in zip(key1, key2):
                        if a == b:
                            common += 1
                        else:
                            break
                    if common >= 4:
                        output.append(tokens[i + 1])
                        i += 2
                        continue
        output.append(tokens[i])
        i += 1
    return " ".join(output)


def _remove_adjacent_duplicate_phrases(text: str) -> str:
    tokens = text.split(" ")
    output = []
    i = 0
    while i < len(tokens):
        duplicate_len = _duplicate_phrase_length(tokens, i)
        if duplicate_len:
            output.extend(tokens[i:i + duplicate_len])
            i += duplicate_len * 2
        else:
            output.append(tokens[i])
            i += 1
    return " ".join(output)


def _duplicate_phrase_length(tokens: list[str], index: int) -> int:
    max_len = min(4, (len(tokens) - index) // 2)
    for size in range(max_len, 0, -1):
        left = [_token_key(token) for token in tokens[index:index + size]]
        right = [
            _token_key(token)
            for token in tokens[index + size:index + (size * 2)]
        ]
        if left == right and any(left):
            if size == 1 and left[0] in _INTENTIONAL_REPEAT_WORDS:
                continue
            return size
    return 0


def _remove_near_duplicate_words(text: str) -> str:
    """Remove adjacent words where one is a substring of the other."""
    tokens = text.split(" ")
    output = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            key1 = _token_key(tokens[i])
            key2 = _token_key(tokens[i + 1])
            if key1 and key2 and key1 != key2:
                if len(key1) < 4 or len(key2) < 4:
                    output.append(tokens[i])
                    i += 1
                    continue
                if key1 in _INTENTIONAL_REPEAT_WORDS or key2 in _INTENTIONAL_REPEAT_WORDS:
                    output.append(tokens[i])
                    i += 1
                    continue
                if abs(len(key1) - len(key2)) <= 2:
                    if key1 in key2 or key2 in key1:
                        longer = tokens[i] if len(key1) >= len(key2) else tokens[i + 1]
                        output.append(longer)
                        i += 2
                        continue
        output.append(tokens[i])
        i += 1
    return " ".join(output)


def _fix_common_misspellings(text: str) -> str:
    """Fix common Whisper small-model misspellings."""
    tokens = text.split(" ")
    output = []
    for token in tokens:
        key = _token_key(token)
        if key in _active_misspellings:
            correction = _active_misspellings[key]
            match = re.match(r"^(\W*)(\w+)(\W*)$", token)
            if match:
                token = f"{match.group(1)}{correction}{match.group(3)}"
            else:
                token = correction
        output.append(token)
    return " ".join(output)


def _correct_whisper_phrases(text: str) -> str:
    """Fix known Whisper small-model phrase misrecognitions.

    ARCH-031: previously ``re.compile(re.escape(bad), re.IGNORECASE)``
    was called inside the loop, recompiling the same pattern on every
    dictation. With hundreds of phrases × thousands of dictations,
    that's significant CPU. We now use a module-level cache keyed on
    the phrase string so each pattern is compiled at most once.
    """
    lower = text.lower()
    for bad, good in _active_phrases:
        pattern = _get_compiled_phrase_pattern(bad)
        if pattern.search(lower):
            # L19: Preserve original casing pattern
            def _apply_case_preserving_replacement(match):
                original = match.group(0)
                replacement = good
                # Apply same casing pattern as original
                if original.isupper():
                    return replacement.upper()
                elif original[0].isupper() and not original[1:].isupper():
                    # Title case: first letter upper, rest lower
                    return replacement[0].upper() + replacement[1:]
                elif any(c.isupper() for c in original[1:]):
                    # Mixed case: try to map uppercase positions from original to replacement
                    result = list(replacement.lower())
                    for i, c in enumerate(original):
                        if i < len(result) and c.isupper():
                            result[i] = result[i].upper()
                    return "".join(result)
                else:
                    return replacement
            text = pattern.sub(_apply_case_preserving_replacement, text)
    return text


def _remove_extra_words(text: str) -> str:
    """Remove common extra word insertions from Whisper.

    ARCH-031: previously ``re.compile(re.escape(bad), re.IGNORECASE)`` was
    called inside the loop, recompiling the same pattern on every dictation.
    We now reuse the same module-level cache as ``_correct_whisper_phrases``.
    """
    lower = text.lower()
    for bad, good in _active_extra_words:
        pattern = _get_compiled_phrase_pattern(bad)
        if pattern.search(lower):
            text = pattern.sub(good, text)
    return text


def _token_key(token: str) -> str:
    # PERF-PIPE: use precompiled regex instead of re.sub(pattern, ...)
    return _RE_TOKEN_KEY.sub("", token).lower()


def _capitalize_sentences(text: str) -> str:
    chars = list(text)
    capitalize_next = True
    for index, char in enumerate(chars):
        if char.isalpha():
            if capitalize_next:
                chars[index] = char.upper()
            capitalize_next = False
        elif char in ".!?":
            capitalize_next = True
    return "".join(chars)


# Roman numeral context words that precede lowercase 'i'
_ROMAN_NUMERAL_CONTEXT_WORDS = {
    "section", "chapter", "part", "book", "henry", "louis",
    "richard", "king", "pope", "volume", "page",
    "act", "scene", "title", "appendix", "amendment",
    "article", "rule", "step", "phase", "stage",
}

_ROMAN_NUMERAL_FOLLOWING_WORDS = {
    "through", "to", "and", "or", "through", "thru", "vs",
    "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
}


def _capitalize_pronoun_i(text: str) -> str:
    """Capitalize the pronoun 'i' but not Roman numeral 'i'."""
    result = []
    i = 0
    while i < len(text):
        if (
            text[i] == 'i'
            and (i == 0 or not text[i - 1].isalpha())
            and (i + 1 >= len(text) or not text[i + 1].isalpha())
        ):
            preceding = text[:i].rstrip()
            last_word = preceding.rsplit(None, 1)[-1] if preceding and preceding[-1].isalpha() else ""
            if last_word.lower() in _ROMAN_NUMERAL_CONTEXT_WORDS:
                result.append('i')
            else:
                following = text[i + 1:].lstrip()
                next_word = ""
                for ch in following:
                    if ch.isalpha():
                        next_word += ch
                    else:
                        break
                if next_word.lower() in _ROMAN_NUMERAL_FOLLOWING_WORDS:
                    result.append('i')
                else:
                    result.append('I')
        else:
            result.append(text[i])
        i += 1
    return ''.join(result)


# NEW-CQ-007: _add_terminal_punctuation deleted. The safe variant
# (_add_safe_terminal_punctuation) is the only one used in the pipeline.
# The unsafe variant was dead code that shipped in the bundle but was
# never called.


# ─── M2: File extension fix ──────────────────────────────────────────────

_KNOWN_EXTENSIONS = {
    ".txt", ".md", ".exe", ".py", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".json", ".xml", ".html", ".css", ".js", ".ts",
    ".bat", ".sh", ".ps1", ".cmd", ".msi", ".dll", ".zip", ".rar", ".7z",
    ".mp3", ".mp4", ".avi", ".wav", ".jpg", ".jpeg", ".png", ".gif", ".svg",
    ".ico", ".log", ".ini", ".cfg", ".yaml", ".yml", ".toml", ".db",
    ".sqlite", ".bak", ".tmp", ".sys", ".mov", ".mkv", ".webm", ".flac",
    ".ogg", ".webp", ".bmp", ".tiff", ".psd", ".ai",
}


def _fix_file_extensions(text: str) -> str:
    """Fix file extension patterns corrupted by text cleanup.

    Whisper transcribes 'features dot md' as 'features. md'. The cleanup
    pipeline then capitalizes after the period: 'features. Md'. This function
    collapses such patterns back to 'features.md' before capitalization runs.

    Must not break:
    - Sentence-ending periods (normal text)
    - URLs (example.com)
    - Abbreviations (U.S.A., Dr., etc.)
    """
    # Pattern: word. ext or word . ext or word .ext
    # Match: word characters followed by optional space, dot, optional space, 2-4 letter extension
    def _replace_extension(m):
        before = m.group(1)   # word before the dot
        ext = m.group(2)      # extension without leading dot
        # Only collapse if the extension is a known file extension
        if f".{ext.lower()}" in _KNOWN_EXTENSIONS:
            return f"{before}.{ext.lower()}"
        # Not a known extension — leave as-is
        return m.group(0)

    # Match word. ext  (e.g., "features. md")
    # PERF-004: use precompiled pattern
    text = _RE_FILE_EXT.sub(
        _replace_extension,
        text,
    )
    return text


# ─── Safe auto-punctuation ──────────────────────────────────────────────

# Patterns that should NOT get terminal punctuation appended
_NO_PUNCTUATION_PATTERNS = [
    re.compile(r'https?://'),           # URLs
    re.compile(r'\.(com|org|net|io|dev)$', re.IGNORECASE),  # Domain names
    re.compile(r'[\\/]'),               # File paths
    re.compile(r'`[^`]*`'),            # Inline code
    re.compile(r'\{\{.*\}\}'),          # Template variables
    re.compile(r'\{.*\}'),              # Variable placeholders
]


def _add_safe_terminal_punctuation(text: str) -> str:
    """Add terminal punctuation with safety guards for URLs, paths, code.

    This version of auto-punctuation checks for patterns that should
    NOT receive punctuation before appending.
    """
    if not text or text[-1] in ".!?":
        return text

    # Check safety patterns — don't add punctuation if any match
    for pattern in _NO_PUNCTUATION_PATTERNS:
        if pattern.search(text):
            return text

    words = text.split()
    if len(words) <= 4:
        return text

    if _looks_like_question(text):
        return f"{text}?"
    return f"{text}."


def _looks_like_question(text: str) -> bool:
    """Detect whether the final sentence looks like a question.

    Uses a conservative set of question openers that excludes "how"
    and "what" to avoid false positives on declarative sentences.
    """
    sentence = re.split(r"[.!?]\s+", text.strip())[-1]
    words = re.findall(r"[A-Za-z']+", sentence.lower())
    if not words:
        return False
    if words[0] in _QUESTION_OPENERS:
        return True
    question_starters = {
        ("do", "you"),
        ("did", "you"),
        ("can", "you"),
        ("could", "you"),
        ("would", "you"),
        ("should", "we"),
    }
    return len(words) >= 2 and tuple(words[:2]) in question_starters
