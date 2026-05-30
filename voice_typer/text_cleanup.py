"""Lightweight cleanup for raw speech-to-text output."""

import json
import logging
import re
from pathlib import Path
from typing import Optional

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

_BUNDLED_CORRECTIONS_PATH = Path(__file__).parent / "corrections.json"


def _load_external_corrections(
    config_dir: Path | None = None,
    corrections_path: str | None = None,
):
    """Load corrections from an external JSON file.

    Loads bundled corrections.json first, then merges user-provided
    file on top.  Returns (misspellings, phrase_corrections, extra_word_patterns)
    from the external file, or None if no corrections file exists or could
    not be loaded.  Callers should fall back to built-in defaults when None
    is returned.

    P2 fix: Returns None when no file exists, so callers can distinguish
    "no external file" from "external file loaded successfully".
    """
    loaded_any = False

    # Start with bundled corrections
    misspellings: dict[str, str] = {}
    phrase_corrections: list[tuple[str, str]] = []
    extra_word_patterns: list[tuple[str, str]] = []

    if _BUNDLED_CORRECTIONS_PATH.exists():
        try:
            data = json.loads(_BUNDLED_CORRECTIONS_PATH.read_text(encoding="utf-8"))
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

    # Merge user-provided corrections on top
    path = None
    if corrections_path:
        path = Path(corrections_path)
    elif config_dir is not None:
        path = config_dir / "voice-typer-corrections.json"

    if path is not None and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
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

    if not loaded_any:
        return None

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


def configure_corrections(
    config_dir: Path | None = None,
    corrections_path: str | None = None,
):
    """Load corrections from external JSON (if present).

    When an external file exists and is valid, its entries replace the
    built-in defaults.  Otherwise the built-in dicts are restored.
    Call this once at startup so that ``clean_transcribed_text`` uses
    the user-provided corrections.
    """
    global _active_misspellings, _active_phrases, _active_extra_words
    result = _active_corrections(config_dir, corrections_path)
    _active_misspellings, _active_phrases, _active_extra_words = result


def clean_transcribed_text(text: str) -> str:
    """Apply conservative cleanup without changing the user's meaning."""
    cleaned = text.strip()
    if not cleaned:
        return ""
    cleaned = _normalize_spacing(cleaned)
    cleaned = _clean_self_corrections(cleaned)
    cleaned = _remove_adjacent_duplicate_phrases(cleaned)
    cleaned = _remove_near_duplicate_words(cleaned)
    cleaned = _fix_common_misspellings(cleaned)
    cleaned = _correct_whisper_phrases(cleaned)
    cleaned = _remove_extra_words(cleaned)
    cleaned = _capitalize_sentences(cleaned)
    cleaned = _capitalize_pronoun_i(cleaned)
    # NOTE: _add_terminal_punctuation() removed from the pipeline.
    # It forced '.' or '?' onto speech without terminal punctuation,
    # which corrupted URLs (example.com.), commands (open settings.),
    # file paths, and code snippets.  Users who want auto-punctuation
    # can re-enable it in a future version with a config toggle.
    return cleaned


def _normalize_spacing(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,.;:!?])(?=[^\s,.;:!?])", r"\1 ", text)
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
    """Fix known Whisper small-model phrase misrecognitions."""
    lower = text.lower()
    for bad, good in _active_phrases:
        pattern = re.compile(re.escape(bad), re.IGNORECASE)
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
    """Remove common extra word insertions from Whisper."""
    lower = text.lower()
    for bad, good in _active_extra_words:
        pattern = re.compile(re.escape(bad), re.IGNORECASE)
        if pattern.search(lower):
            text = pattern.sub(good, text)
    return text


def _token_key(token: str) -> str:
    return re.sub(r"^\W+|\W+$", "", token).lower()


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


def _add_terminal_punctuation(text: str) -> str:
    """Add terminal punctuation to sentences that lack it.

    REMOVED from the cleanup pipeline by default.  Still available
    for callers who explicitly want auto-punctuation.
    """
    if not text or text[-1] in ".!?":
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
