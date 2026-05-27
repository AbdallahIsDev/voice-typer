"""Lightweight cleanup for raw speech-to-text output."""

import json
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_QUESTION_OPENERS = {
    "am", "are", "can", "could", "did", "do", "does", "has", "have",
    "how", "is", "may", "should", "was", "were", "what", "when",
    "where", "which", "who", "whom", "whose", "why", "will", "would",
}

_INTENTIONAL_REPEAT_WORDS = {
    "no",
    "test",
    "very",
}

_WHISPER_MISSPELLINGS = {
    "infestigate": "investigate",
    "grammer": "grammar",
    "recieve": "receive",
    "occured": "occurred",
    "seperate": "separate",
    "definately": "definitely",
    "accomodate": "accommodate",
    "occassion": "occasion",
    "untill": "until",
    "wierd": "weird",
    "thier": "their",
    "goverment": "government",
    "enviroment": "environment",
    "developement": "development",
    "begining": "beginning",
    "sucessful": "successful",
    "neccessary": "necessary",
    "recomend": "recommend",
    "tommorow": "tomorrow",
    "beautifull": "beautiful",
    "wonderfull": "wonderful",
    "awfull": "awful",
    "carefull": "careful",
    "helpfull": "helpful",
    "usefull": "useful",
    "powerfull": "powerful",
    "grammerly": "grammatically",
    "grammarly": "grammatically",
}

_WHISPER_PHRASE_CORRECTIONS = [
    ("to 2 ", "to "),
    (" to 2", " to"),
    ("they working", "it's working"),
    ("this me either", "I'm also"),
    ("treat 3", "treat this"),
    ("adds a test", "is a test"),
    ("Execute execute", "Execute"),
]

_COMMON_EXTRA_WORD_PATTERNS = [
    ("without whether", "whether"),
    ("didn't and ", "didn't "),
]


def _load_external_corrections(config_dir: Optional[Path] = None, corrections_path: Optional[str] = None):
    """Load corrections from an external JSON file if available.

    Returns (misspellings, phrase_corrections) — either from the external
    file or the built-in defaults.

    P3: External corrections file support.
    """
    misspellings = dict(_WHISPER_MISSPELLINGS)
    phrase_corrections = list(_WHISPER_PHRASE_CORRECTIONS)

    # Determine the file path
    file_path = None
    if corrections_path:
        file_path = Path(corrections_path)
    elif config_dir is not None:
        file_path = config_dir / "voice-typer-corrections.json"

    if file_path is None or not file_path.exists():
        return misspellings, phrase_corrections

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if "misspellings" in data and isinstance(data["misspellings"], dict):
            misspellings.update(data["misspellings"])
        if "phrase_corrections" in data and isinstance(data["phrase_corrections"], list):
            phrase_corrections.extend(
                tuple(item) for item in data["phrase_corrections"]
                if isinstance(item, (list, tuple)) and len(item) == 2
            )
        log.info("[CLEANUP] Loaded external corrections from %s", file_path)
    except Exception as exc:
        log.warning("[CLEANUP] Failed to load external corrections from %s: %s", file_path, exc)

    return misspellings, phrase_corrections


def clean_transcribed_text(text: str, enabled: bool = True, config_dir: Optional[Path] = None, corrections_path: Optional[str] = None) -> str:
    """Apply conservative cleanup without changing the user's meaning.

    P2: If enabled=False, skip all cleanup and return raw text (just stripped).
    P3: If config_dir or corrections_path provided, load external corrections.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""

    if not enabled:
        return cleaned

    # Load corrections (built-in + external if available)
    misspellings, phrase_corrections = _load_external_corrections(config_dir, corrections_path)

    cleaned = _normalize_spacing(cleaned)
    cleaned = _clean_self_corrections(cleaned)
    cleaned = _remove_adjacent_duplicate_phrases(cleaned)
    cleaned = _remove_near_duplicate_words(cleaned)
    cleaned = _fix_common_misspellings(cleaned, misspellings)
    cleaned = _correct_whisper_phrases(cleaned, phrase_corrections)
    cleaned = _remove_extra_words(cleaned)
    cleaned = _capitalize_sentences(cleaned)
    cleaned = _capitalize_pronoun_i(cleaned)
    cleaned = _add_terminal_punctuation(cleaned)
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


def _fix_common_misspellings(text: str, misspellings: Optional[dict] = None) -> str:
    """Fix common Whisper small-model misspellings."""
    if misspellings is None:
        misspellings = _WHISPER_MISSPELLINGS
    tokens = text.split(" ")
    output = []
    for token in tokens:
        key = _token_key(token)
        if key in misspellings:
            correction = misspellings[key]
            match = re.match(r"^(\W*)(\w+)(\W*)$", token)
            if match:
                token = f"{match.group(1)}{correction}{match.group(3)}"
            else:
                token = correction
        output.append(token)
    return " ".join(output)


def _correct_whisper_phrases(text: str, phrase_corrections: Optional[list] = None) -> str:
    """Fix known Whisper small-model phrase misrecognitions."""
    if phrase_corrections is None:
        phrase_corrections = _WHISPER_PHRASE_CORRECTIONS
    lower = text.lower()
    for bad, good in phrase_corrections:
        pattern = re.compile(re.escape(bad), re.IGNORECASE)
        if pattern.search(lower):
            text = pattern.sub(good, text)
    return text


def _remove_extra_words(text: str) -> str:
    """Remove common extra word insertions from Whisper."""
    lower = text.lower()
    for bad, good in _COMMON_EXTRA_WORD_PATTERNS:
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


def _capitalize_pronoun_i(text: str) -> str:
    return re.sub(r"\bi\b", "I", text)


def _add_terminal_punctuation(text: str) -> str:
    if not text or text[-1] in ".!?":
        return text
    words = text.split()
    if len(words) <= 4:
        return text
    if _looks_like_question(text):
        return f"{text}?"
    return f"{text}."


def _looks_like_question(text: str) -> bool:
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
