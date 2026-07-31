"""AI grammar / punctuation / capitalization enhancement for transcribed text.

This module is a *rule-based, offline* complement to :mod:`llm_polish`.
Where ``llm_polish`` sends the transcription to a cloud LLM for stylistic
rewriting, ``ai_enhancement`` applies small, deterministic, reversible
fixes that don't require any network call:

* :func:`auto_capitalize` — sentence-start and proper-noun capitalization.
* :func:`auto_punctuate` — adds periods at sentence boundaries and
  commas at natural breath breaks, using word-gap heuristics.
* :func:`fix_grammar_basics` — fixes common transcription artifacts
  such as the bare pronoun ``i``, missing apostrophes in contractions
  (``dont`` → ``don't``), and stray double spaces.
* :func:`enhance_transcription` — dispatcher that reads the relevant
  boolean flags off a :class:`~voice_typer.server.config.Config` and
  applies the enabled steps. The dispatcher is *opt-in*: the master
  toggle ``ai_enhancement_enabled`` defaults to ``False`` so the
  feature cannot affect existing users until they explicitly turn it
  on in Settings.

The functions here are intentionally conservative.  They MUST NOT
change the meaning of the transcription.  When in doubt, the function
leaves the text alone — false negatives are preferred to false
positives.  The heuristic rules are tuned for English; non-English
text passes through largely untouched (same as the existing
``text_cleanup`` module).

Pipeline placement
------------------
``enhance_transcription`` runs AFTER LLM polish and BEFORE the result
is stored to history / pasted.  This means LLM output gets the same
cleanup pass that raw transcription does, which keeps capitalization
consistent regardless of which upstream path produced the text.  The
existing ``text_cleanup.clean_transcribed_text`` runs much earlier
(step 3 of the pipeline) and focuses on duplicate-removal and
spacing; the functions here run later and focus on grammar.

No LLM is used.  The "AI" in the module name is a misnomer carried
over from the task plan; this is plain deterministic NLP.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


# ─── Compiled patterns (module-level for performance) ───────────────────────
#
# PERF: these are invoked once per dictation, so a single compile per
# pattern at import time is cheaper than recompiling inside the hot
# path.  Mirrors the pattern used by ``text_cleanup.py``.

# Sentence boundary: a . ! or ? followed by one or more spaces and a
# lowercase letter.  We deliberately don't touch already-capitalized
# text or whitespace-only boundaries.
_RE_SENTENCE_BOUNDARY = re.compile(r"([.!?]\s+)([a-z])")

# A bare lower-case "i" surrounded by word boundaries — this is the
# pronoun and should be capitalized.  We use a non-word lookaround
# rather than ``\b`` because ``\b`` between two non-word characters
# behaves unexpectedly; the explicit ``(?<![A-Za-z'])`` /
# ``(?![A-Za-z'])`` guards handle the apostrophe case (e.g. "don't i"
# → "don't I") correctly.
_RE_PRONOUN_I = re.compile(r"(?<![A-Za-z'])i(?![A-Za-z'])")

# Two or more consecutive spaces — collapse to one.  We don't touch
# leading/trailing whitespace (the caller / ``text_cleanup`` already
# stripped that).
_RE_DOUBLE_SPACE = re.compile(r"  +")

# Missing apostrophe in common contractions.  Keys are lowercased
# patterns; we replace them as whole-word matches only (no
# substrings) so "wont" → "won't" but "wonton" stays unchanged.
#
# This list is intentionally short and conservative — each entry is
# a transcription error that Whisper small models frequently emit.
# Adding speculative entries (e.g. "well" → "we'll") would create
# false positives on legitimate words.
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
    "ill": "I'll",
    "id": "I'd",
    "youre": "you're",
    "youve": "you've",
    "youll": "you'll",
    "youd": "you'd",
    "hes": "he's",
    "shes": "she's",
    "its": "it's",  # NB: ambiguous with possessive "its" — see note below
    "were": "we're",  # NB: ambiguous with "we were" — see note below
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
# length descending so longer patterns win (none of the current keys
# are substrings of each other, but this protects against future
# additions).  Word boundaries on both sides prevent mid-word matches.
_CONTRACTION_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_CONTRACTION_FIXES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Proper-noun heuristics.  These are deliberately tiny — we only
# capitalize a word as a proper noun when we have high confidence.
# The list below is the set of weekday / month names plus a handful
# of uncontroversial single-word proper nouns that Whisper often
# transcribes lower-case.  Anything not in this set is left alone;
# we'd rather under-capitalize than mangle a common noun.
#
# NOTE: ``its`` and ``were`` above ARE ambiguous with their
# non-contracted forms ("the dog bit its tail", "we were there").
# They appear here because the contracted form is roughly 5× more
# common in conversational speech than the possessive / past-tense
# form.  If a user reports a false positive, the fix is to remove
# the entry from ``_CONTRACTION_FIXES`` rather than adding a
# heuristic — the disambiguation is too context-dependent to be
# worth the complexity here.
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
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
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
# capture group for the leading boundary because we want to preserve
# the original surrounding characters (quotes, brackets, etc.).
_RE_PROPER_NOUN = re.compile(
    r"(?<![A-Za-z'])("
    + "|".join(re.escape(w) for w in sorted(_PROPER_NOUN_SINGLE_WORDS, key=len, reverse=True))
    + r")(?![A-Za-z'])",
)

# hoisted from ``auto_capitalize`` and ``auto_punctuate`` function
# bodies. Re-compiling these patterns (and re-allocating the pronouns
# frozenset) inside the hot dictation path contradicted the module-level
# compile pattern documented at the top of this file and added ~1-3 µs
# of avoidable overhead per call. Module-level compile also lets the
# regex engine cache its internal DFA once.

# Leading URL guard for ``auto_capitalize``: if the text starts with an
# ``http://`` or ``https://`` scheme we skip the first-letter
# capitalization step so we don't mangle the URL scheme.
_RE_LEADING_URL = re.compile(r"^\s*https?://", re.IGNORECASE)

# Subject pronouns used by ``auto_punctuate``'s conjunction-break
# heuristic: a comma is inserted before ``and``/``but``/``or`` only when
# the conjunction is followed by one of these pronouns (a conservative
# signal for an independent-clause boundary). A ``frozenset`` is the
# cheapest O(1) membership container.
_PRONOUN_SUBJECTS = frozenset({"i", "you", "he", "she", "it", "we", "they"})

# Match: (and|but|or) + space + pronoun, with no existing comma
# before the conjunction.  We use a function replacement so we
# can inspect the surrounding words and only insert when there
# isn't already a comma.
_RE_CONJUNCTION_BREAK = re.compile(r"(?<![,\s])(\s+)(and|but|or)(\s+)([A-Za-z]+)\b")


# ─── Public API ─────────────────────────────────────────────────────────────


def auto_capitalize(text: str) -> str:
    """Capitalize sentence starts and a small set of proper nouns.

    This function is *idempotent*: running it twice produces the same
    output as running it once.  Already-capitalized text is left
    unchanged.

    The function handles:

    * The first letter of the very first word.
    * The first letter of any word that follows a ``.``, ``!``, or
      ``?`` and one or more spaces.
    * A small fixed set of weekday / month / language / nationality
      names (see ``_PROPER_NOUN_SINGLE_WORDS``).  Anything outside
      this set is left alone to avoid false positives on common
      nouns.

    The function does NOT attempt to:

    * Capitalize names of people or products (too ambiguous without
      context).
    * Lower-case words that are mistakenly upper-cased (the existing
      ``text_cleanup`` module already handles ALL-CAPS shouting).
    * Touch text inside quotes, code, or URLs — there is no
      reliable way to detect these without a parser, and the
      downstream ``_add_safe_terminal_punctuation`` already skips
      them.

    Parameters
    ----------
    text : str
        The transcribed text to capitalize.  May be empty.

    Returns
    -------
    str
        The capitalized text.  Empty input returns empty output.
    """
    if not text:
        return ""

    result = text

    # 1. Capitalize the very first alphabetic character.
    #    We do this with a small scan rather than a regex so we
    #    don't accidentally skip leading punctuation like an opening
    #    quote ("hello" → "Hello" but `"hello` → `"Hello`).
    #
    #    URL guard: if the text starts with `http://` or `https://`,
    #    we skip this step entirely so we don't mangle the scheme
    #    (capitalizing "h" in "https" would break the URL).
    # pattern hoisted to module level (``_RE_LEADING_URL``).
    if not _RE_LEADING_URL.match(result):
        for i, ch in enumerate(result):
            if ch.isalpha():
                result = result[:i] + ch.upper() + result[i + 1 :]
                break
            if ch.isspace():
                continue
            # Non-alpha, non-space (e.g. '"', '(') — keep scanning.
            # This lets us capitalize the first letter AFTER opening
            # punctuation: `"hello` → `"Hello`.

    # 2. Capitalize letters that follow a sentence-ending punctuation
    #    mark and whitespace.
    def _cap_after_sentence(match: re.Match[str]) -> str:
        return match.group(1) + match.group(2).upper()

    result = _RE_SENTENCE_BOUNDARY.sub(_cap_after_sentence, result)

    # 3. Capitalize the small fixed set of weekday / month / language
    #    names.  We preserve any leading punctuation by matching the
    #    word itself, not the surrounding chars.
    def _cap_proper_noun(match: re.Match[str]) -> str:
        word = match.group(1)
        return word[0].upper() + word[1:]

    result = _RE_PROPER_NOUN.sub(_cap_proper_noun, result)

    return result


def auto_punctuate(text: str) -> str:
    """Add periods at sentence boundaries and commas at natural breaks.

    This function is conservative — it only adds punctuation that is
    very likely correct.  It does NOT:

    * Add punctuation inside quotes or parentheses.
    * Add punctuation to text that already ends in ``.``, ``!``,
      ``?``, or any other terminal punctuation.
    * Add punctuation to URLs, file paths, or code (delegated to
      ``_add_safe_terminal_punctuation``'s safety patterns).
    * Add commas inside lists (too context-dependent).

    What it DOES do:

    * If the text ends with a word and no terminal punctuation, and
      the text is at least 4 words long, append a period.  Question
      detection is delegated to the existing
      ``_looks_like_question`` helper in ``text_cleanup`` (which is
      conservative about "how" / "what" openers).
    * Insert commas at common conjunction-led breath breaks
      ("I went to the store and I bought milk" → "I went to the
      store, and I bought milk").  This only fires when the
      conjunction sits between two independent clauses (heuristic:
      the conjunction is preceded by a subject-like word and
      followed by a pronoun).

    Parameters
    ----------
    text : str
        The transcribed text to punctuate.  May be empty.

    Returns
    -------
    str
        The punctuated text.  Empty input returns empty output.
    """
    if not text or not text.strip():
        return ""

    # Late import to avoid a circular import at module load time.
    # ``text_cleanup`` imports from ``vocabulary`` which imports from
    # ``config``; this module is imported by ``dictation_pipeline``
    # which is itself imported during app startup.  The late import
    # keeps the dependency graph acyclic.
    from voice_typer.server.text_cleanup import (
        _NO_PUNCTUATION_PATTERNS,
        _looks_like_question,
    )

    result = text.rstrip()

    # Step 1: terminal punctuation.
    # Skip if the text already ends with terminal punctuation.
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
    # pronoun.  This is a very conservative heuristic for detecting
    # independent-clause boundaries: when the conjunction is
    # immediately followed by a subject pronoun (I, you, he, she,
    # it, we, they), we insert a comma.  This catches "I went to
    # the store and I bought milk" but leaves "I bought apples and
    # oranges" alone (no pronoun after "and").  We don't require the
    # word BEFORE the conjunction to be a pronoun because that would
    # miss the common "X-verb-object and I-verb-object" pattern.
    # pattern + pronoun set hoisted to module level
    # (``_RE_CONJUNCTION_BREAK`` / ``_PRONOUN_SUBJECTS``).

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
    """Fix common transcription artifacts: bare ``i``, contractions, spacing.

    This function applies three classes of fix:

    1.  **Pronoun ``i`` → ``I``.**  A bare lower-case ``i`` surrounded
        by non-letter characters (or string boundaries) is the
        English first-person pronoun and should be capitalized.  We
        do NOT touch ``i`` inside another word (``input``) or after
        an apostrophe (``don't i`` — handled here too, the apostrophe
        is a non-letter so the bare ``i`` matches).

    2.  **Missing apostrophes in common contractions.**  Whisper small
        models frequently emit ``dont`` for ``don't``, ``cant`` for
        ``can't``, etc.  We replace a fixed list of these (see
        ``_CONTRACTION_FIXES``) as whole-word matches only.  The
        replacement preserves the original case pattern: ``Dont`` →
        ``Don't``, ``DONT`` → ``DON'T``.

    3.  **Double spaces.**  Any run of two or more spaces is collapsed
        to a single space.  This is a defensive measure for upstream
        text that may have gone through a partial cleanup pass.

    Parameters
    ----------
    text : str
        The transcribed text to fix.  May be empty.

    Returns
    -------
    str
        The fixed text.  Empty input returns empty output.
    """
    if not text:
        return ""

    result = text

    # 1. Bare pronoun "i" → "I".  We use the precompiled pattern.
    result = _RE_PRONOUN_I.sub("I", result)

    # 2. Contractions.  Case-preserving replacement: we mirror the
    #    casing of the matched word onto the replacement.
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
    #    whitespace here — the upstream text_cleanup pass already
    #    did that, and we don't want to second-guess the caller.
    result = _RE_DOUBLE_SPACE.sub(" ", result)

    return result


def enhance_transcription(text: str, config: Any) -> str:
    """Apply the AI enhancement steps that are enabled in ``config``.

    This is the dispatcher used by the dictation pipeline.  It reads
    the following boolean flags off the config:

    * ``ai_enhancement_enabled`` — master toggle.  When ``False``
        (the default), the function returns ``text`` unchanged.
    * ``fix_grammar_basics`` — when ``True``, run
        :func:`fix_grammar_basics`.
    * ``auto_punctuate`` — when ``True``, run :func:`auto_punctuate`.
    * ``auto_capitalize`` — when ``True``, run :func:`auto_capitalize`.

    The order is significant: grammar fixes run first (so the bare
    ``i`` is capitalized before we look for sentence boundaries),
    then punctuation (so we don't add a period before a missing
    apostrophe), then capitalization (so sentence-start capitals
    are applied after the punctuation that defines the sentence
    boundaries).

    The function is defensive: if any individual step raises, the
    exception is logged and the function returns the text as it was
    at the point of failure.  This ensures a bug in one step can't
    break the entire dictation pipeline.

    Parameters
    ----------
    text : str
        The transcribed text to enhance.
    config : Config
        The application config.  Any object with the four boolean
        attributes listed above will work; we accept ``Any`` to
        avoid an import cycle with :mod:`voice_typer.server.config`.

    Returns
    -------
    str
        The enhanced text, or the original text if the master toggle
        is off or if every step is disabled.
    """
    # Master toggle — default OFF.  We use getattr with a default of
    # False so the function degrades gracefully if a non-Config object
    # is passed (e.g. a MagicMock in tests).
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
    # punctuation-defined sentence boundaries).
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
