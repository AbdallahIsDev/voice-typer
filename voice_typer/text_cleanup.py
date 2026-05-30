"""Lightweight cleanup for raw speech-to-text output."""

import json
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


_INTENTIONAL_REPEAT_WORDS = {
    "no",
    "test",
    "very",
}

_BUNDLED_CORRECTIONS_PATH = Path(__file__).parent / "corrections.json"

_misspelling_token_re: re.Pattern | None = None
_phrase_patterns_cache: list[tuple[re.Pattern, str, str]] | None = None
_extra_word_patterns_cache: list[tuple[re.Pattern, str, str]] | None = None


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
    """
    loaded_any = False

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
                and isinstance(item[0], str) and isinstance(item[1], str)
            ]
            extra_word_patterns = [
                tuple(item) for item in data.get("extra_word_patterns", [])
                if isinstance(item, (list, tuple)) and len(item) == 2
                and isinstance(item[0], str) and isinstance(item[1], str)
            ]
            loaded_any = True
        except Exception as exc:
            log.warning("[CLEANUP] Failed to load bundled corrections: %s", exc)

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
                    and isinstance(item[0], str) and isinstance(item[1], str)
                )
            if "extra_word_patterns" in data and isinstance(data["extra_word_patterns"], list):
                extra_word_patterns.extend(
                    tuple(item) for item in data["extra_word_patterns"]
                    if isinstance(item, (list, tuple)) and len(item) == 2
                    and isinstance(item[0], str) and isinstance(item[1], str)
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
    return {}, [], []


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
    global _misspelling_token_re, _phrase_patterns_cache, _extra_word_patterns_cache
    result = _active_corrections(config_dir, corrections_path)
    _active_misspellings, _active_phrases, _active_extra_words = result

    if _active_misspellings:
        escaped = [re.escape(k) for k in _active_misspellings]
        _misspelling_token_re = re.compile(
            r"^(?P<lead>\W*)(?P<word>" + "|".join(escaped) + r")(?P<trail>\W*)$",
            re.IGNORECASE,
        )
    else:
        _misspelling_token_re = None

    _phrase_patterns_cache = [
        (re.compile(re.escape(bad), re.IGNORECASE), bad, good)
        for bad, good in _active_phrases
    ]

    _extra_word_patterns_cache = [
        (re.compile(re.escape(bad), re.IGNORECASE), bad, good)
        for bad, good in _active_extra_words
    ]


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
                if key2.startswith(key1) or key1.startswith(key2):
                    output.append(tokens[i + 1])
                    i += 2
                    continue
                if len(key1) >= 4 and len(key2) >= 4:
                    common = 0
                    for a, b in zip(key1, key2):
                        if a == b:
                            common += 1
                        else:
                            break
                    threshold = min(len(key1), len(key2)) // 2 + 1
                    if threshold < 5:
                        threshold = 5
                    if common >= threshold:
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
    if not _active_misspellings:
        return text
    if _misspelling_token_re is not None:
        tokens = text.split(" ")
        output = []
        for token in tokens:
            m = _misspelling_token_re.match(token)
            if m:
                key = _token_key(m.group("word"))
                if key in _active_misspellings:
                    token = f"{m.group('lead')}{_active_misspellings[key]}{m.group('trail')}"
            output.append(token)
        return " ".join(output)
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
    patterns = _phrase_patterns_cache
    if patterns is None:
        patterns = [
            (re.compile(re.escape(bad), re.IGNORECASE), bad, good)
            for bad, good in _active_phrases
        ]
    lower = text.lower()
    for pattern, bad, good in patterns:
        if pattern.search(lower):
            original_first_upper = text[0].isupper() if text else False
            text = pattern.sub(good, text)
            if original_first_upper and text and text[0].islower():
                text = text[0].upper() + text[1:]
            lower = text.lower()
    return text


def _remove_extra_words(text: str) -> str:
    """Remove common extra word insertions from Whisper."""
    patterns = _extra_word_patterns_cache
    if patterns is None:
        patterns = [
            (re.compile(re.escape(bad), re.IGNORECASE), bad, good)
            for bad, good in _active_extra_words
        ]
    lower = text.lower()
    for pattern, bad, good in patterns:
        if pattern.search(lower):
            text = pattern.sub(good, text)
            lower = text.lower()
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


def _capitalize_pronoun_i(text: str) -> str:
    return re.sub(r"\bi\b(?!\.)", "I", text)


configure_corrections()
