"""AI text enhancement orchestration."""

from __future__ import annotations

import logging
import re
from typing import Any

from voice_typer.server.text_cleanup import _NO_PUNCTUATION_PATTERNS, _looks_like_question

log = logging.getLogger(__name__)


# PERF: these are invoked once per dictation, so a single compile per

# Sentence boundary: a . ! or ? followed by one or more spaces and a
_RE_SENTENCE_BOUNDARY = re.compile(r"([.!?]\s+)([a-z])")

# A bare lower-case "i" surrounded by word boundaries, this is the
_RE_PRONOUN_I = re.compile(r"(?<![A-Za-z'])i(?![A-Za-z'])")

# Two or more consecutive spaces, collapse to one.  We don't touch
_RE_DOUBLE_SPACE = re.compile(r"  +")

# Missing apostrophe in common contractions.  Keys are lowercased
_CONTRACTION_FIXES: dict[str, str] = {
    "dont": "don't",
    "cant": "can't",
    "wont": "won't",
    "isnt": "isn't",
    "wasnt": "wasn't",
    "arent": "aren't",
    "werent": "weren't",
    "doesnt": "doesn't",
    "didnt": "didn't",
    "hasnt": "hasn't",
    "havent": "haven't",
    "hadnt": "hadn't",
    "couldnt": "couldn't",
    "wouldnt": "wouldn't",
    "shouldnt": "shouldn't",
    "mustnt": "mustn't",
    "im": "I'm",
    "ive": "I've",
    "youre": "you're",
    "youve": "you've",
    "youll": "you'll",
    "youd": "you'd",
    "hes": "he's",
    "shes": "she's",
    # "its" -> "it's" and "were" -> "we're" are deliberately ABSENT:
    "theyre": "they're",
    "theyve": "they've",
    "theyll": "they'll",
    "theyd": "they'd",
    "thats": "that's",
    "whats": "what's",
    "whos": "who's",
    "wheres": "where's",
    "hows": "how's",
    "lets": "let's",
}

# Build a single alternation regex once.  The keys are sorted by
_CONTRACTION_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_CONTRACTION_FIXES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Proper-noun heuristics.  These are deliberately tiny, we only
_PROPER_NOUN_SINGLE_WORDS: frozenset[str] = frozenset(
    {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "april",
        "june",
        "july",
        "september",
        "october",
        "november",
        "december",
        "english",
        "spanish",
        "french",
        "german",
        "italian",
        "american",
        "european",
        "african",
        "asian",
    }
)

# Word boundary regex for the proper-noun set.  We don't use a
_RE_PROPER_NOUN = re.compile(
    r"(?<![A-Za-z'])("
    + "|".join(re.escape(w) for w in sorted(_PROPER_NOUN_SINGLE_WORDS, key=len, reverse=True))
    + r")(?![A-Za-z'])",
)

# hoisted from ``auto_capitalize`` and ``auto_punctuate`` function

# Leading URL guard for ``auto_capitalize``: if the text starts with an
_RE_LEADING_URL = re.compile(r"^\s*https?://", re.IGNORECASE)

# Subject pronouns used by ``auto_punctuate``'s conjunction-break
_PRONOUN_SUBJECTS = frozenset({"i", "you", "he", "she", "it", "we", "they"})

# Match: (and|but|or) + space + pronoun, with no existing comma
_RE_CONJUNCTION_BREAK = re.compile(r"(?<![,\s])(\s+)(and|but|or)(\s+)([A-Za-z]+)\b")


def auto_capitalize(text: str) -> str:
    """Capitalize sentence starts and a small set of proper nouns.

    Returns
    """
    if not text:
        return ""

    result = text

    # 1. Capitalize the very first alphabetic character.
    if not _RE_LEADING_URL.match(result):
        for i, ch in enumerate(result):
            if ch.isalpha():
                result = result[:i] + ch.upper() + result[i + 1 :]
                break
            if ch.isspace():
                continue
            # Non-alpha, non-space (e.g. '"', '('). Keep scanning.

    # 2. Capitalize letters that follow a sentence-ending punctuation
    def _cap_after_sentence(match: re.Match[str]) -> str:
        return match.group(1) + match.group(2).upper()

    result = _RE_SENTENCE_BOUNDARY.sub(_cap_after_sentence, result)

    # 3. Capitalize the small fixed set of weekday / month / language
    def _cap_proper_noun(match: re.Match[str]) -> str:
        word = match.group(1)
        return word[0].upper() + word[1:]

    result = _RE_PROPER_NOUN.sub(_cap_proper_noun, result)

    return result


def auto_punctuate(text: str) -> str:
    """Add periods at sentence boundaries and commas at natural breaks.

    Returns
    """
    if not text or not text.strip():
        return ""

    # ``text_cleanup`` imports here are module-level: the dependency
    result = text.rstrip()

    # Step 1: terminal punctuation.
    if result and result[-1] not in ".!?":
        # Safety: don't add punctuation to URLs, paths, code, templates.
        skip = False
        for pattern in _NO_PUNCTUATION_PATTERNS:
            if pattern.search(result):
                skip = True
                break
        if not skip:
            words = result.split()
            if len(words) >= 4:
                result = result + "?" if _looks_like_question(result) else result + "."

    # Step 2: comma at "X and/but Y" breath breaks where Y is a

    def _maybe_insert_comma(match: re.Match[str]) -> str:
        conj = match.group(2)
        after = match.group(4).lower()
        if after in _PRONOUN_SUBJECTS:
            # Insert a comma before the conjunction.
            return f",{match.group(1)}{conj}{match.group(3)}{match.group(4)}"
        return match.group(0)

    result = _RE_CONJUNCTION_BREAK.sub(_maybe_insert_comma, result)

    return result


def fix_grammar_basics(text: str) -> str:
    """to a single space.  This is a defensive measure for upstream"""
    if not text:
        return ""

    result = text

    # 1. Bare pronoun "i" → "I".  We use the precompiled pattern.
    result = _RE_PRONOUN_I.sub("I", result)

    # 2. Contractions.  Case-preserving replacement: we mirror the
    def _apply_contraction_fix(match: re.Match[str]) -> str:
        original = match.group(1)
        replacement = _CONTRACTION_FIXES[original.lower()]
        if original.isupper():
            return replacement.upper()
        if original[0].isupper() and not original[1:].isupper():
            return replacement[0].upper() + replacement[1:]
        return replacement

    result = _CONTRACTION_PATTERN.sub(_apply_contraction_fix, result)

    # 3. Collapse double spaces.  We do NOT strip leading/trailing
    result = _RE_DOUBLE_SPACE.sub(" ", result)

    return result


def enhance_transcription(text: str, config: Any) -> str:
    """The function is defensive: if any individual step raises, the"""
    # Master toggle, default OFF.  We use getattr with a default of
    if not getattr(config, "ai_enhancement_enabled", False):
        return text

    if not text:
        return text

    result = text

    # Step 1: grammar basics (i → I, contractions, double spaces).
    if getattr(config, "fix_grammar_basics", True):
        try:
            result = fix_grammar_basics(result)
        except Exception:
            log.warning("[AI_ENHANCE] fix_grammar_basics failed", exc_info=True)

    # Step 2: auto-punctuation.
    if getattr(config, "auto_punctuate", True):
        try:
            result = auto_punctuate(result)
        except Exception:
            log.warning("[AI_ENHANCE] auto_punctuate failed", exc_info=True)

    # Step 3: auto-capitalization (runs LAST so it sees the
    if getattr(config, "auto_capitalize", True):
        try:
            result = auto_capitalize(result)
        except Exception:
            log.warning("[AI_ENHANCE] auto_capitalize failed", exc_info=True)

    return result


__all__ = [
    "auto_capitalize",
    "auto_punctuate",
    "fix_grammar_basics",
    "enhance_transcription",
]
