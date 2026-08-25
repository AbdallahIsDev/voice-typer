"""Cleanup engine: entry points + state + cleaning rules for text cleanup.

Split verbatim out of the pre-split ``text_cleanup`` module. Holds the
module-level active-corrections state (+ its lock and the combined-
alternation regex caches), ``configure_corrections`` /
``clean_transcribed_text``, the precompiled regex constants, and every
spacing / token / phrase / extra-word / punctuation rule.
"""

from __future__ import annotations

import functools
import logging
import re
import threading
from pathlib import Path
from typing import Final

from ._casing import _capitalize_sentences, _fix_file_extensions
from ._corrections_data import (
    _INTENTIONAL_REPEAT_WORDS,
    _QUESTION_OPENERS,
    CorrectionsLoadError,
    _active_corrections,
    _capitalize_pronoun_i,
)

log = logging.getLogger("voice_typer.server.text_cleanup")

# ─── Active corrections (initialized to built-in defaults) ──────────────
# Updated by configure_corrections() when an external corrections file
# is available.  The cleanup functions below read from these instead of
# the built-in dicts directly.

_active_misspellings: dict[str, str] = {}
_active_phrases: list[tuple[str, str]] = []
_active_extra_words: list[tuple[str, str]] = []
# guard the three module-level mutables with a lock so
# concurrent dictations don't clobber each other. The proper fix is
# to move these into a TextCleanupService instance; for now the lock
# prevents the worst race (two threads each replacing the dict mid-
# cleanup of the other). The instance refactor is deferred because
# it touches ~20 call sites.
_active_state_lock = threading.Lock()

# Combined-alternation regex cache. The prior implementation iterated
# through the phrase list once per dictation, doing an O(M) ``bad.lower()
# in lower`` substring check per phrase — O(N×M) total for N phrases.
# This cache builds a single ``re.compile(r"(?:p1|p2|p3|...)",
# re.IGNORECASE)`` alternation that the ``re`` engine compiles to a trie
# of escaped literals, giving O(N+M) total matching regardless of how
# many phrases are in the dictionary (the SRE trie optimization kicks in
# for alternations of ``re.escape``d strings because every alternative is
# a literal with no regex metacharacters).
#
# The cache holds a reference to the exact list object it was built
# from and invalidates via identity (``cached_list is _active_phrases``),
# NOT ``id()``. Keying on ``id()`` was an id-reuse hazard: once the old
# list was GC'd, CPython could allocate the NEW list at the same address,
# so ``id(new_list) == id(old_list)`` returned a stale cached regex built
# from the PREVIOUS corrections (wrong substitutions in production, and
# a flaky cross-file test interaction). Holding the object reference in
# the cache keeps the old list alive, so its address can never be reused
# for a different list — identity comparison is both correct and O(1).
# ``configure_corrections`` (and the test suite) REPLACES the module
# attribute with a new list object, so ``is`` fails and the cache
# rebuilds; in-place mutation (rare, and equally stale under the old
# ``id()`` key) is not cached.
_phrases_re_cache: tuple[object | None, re.Pattern[str] | None, dict[str, str]] = (
    None,
    None,
    {},
)
_extra_words_re_cache: tuple[object | None, re.Pattern[str] | None, dict[str, str]] = (
    None,
    None,
    {},
)


def _build_phrases_regex(
    phrases: list[tuple[str, str]],
) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    """Build a single alternation regex + lowercased lookup dict.

    The regex is ``re.compile(r"(?:p1|p2|...)", re.IGNORECASE)`` where
    each ``pN`` is ``re.escape(bad)``. The lookup dict maps
    ``bad.lower() → good`` so the ``re.sub`` callback can find the
    replacement for an arbitrary-case match.

    Deduplicates by lowercased bad string (first wins) — matching the
    original sequential ``for bad, good in phrases`` behaviour where the
    first phrase in list order is the one whose substitution applies.

    Returns ``(None, {})`` if ``phrases`` is empty so the caller can
    short-circuit.
    """
    if not phrases:
        return None, {}
    lookup: dict[str, str] = {}
    parts: list[str] = []
    for bad, good in phrases:
        key = bad.lower()
        if key in lookup:
            continue  # first wins, matching the original list-order behaviour
        lookup[key] = good
        parts.append(re.escape(bad))
    if not parts:
        return None, {}
    # Sort alternatives by length DESCENDING so longer phrases match
    # first at any given position. The original sequential loop checked
    # phrases in list order, so for non-overlapping phrases the order is
    # irrelevant. For overlapping phrases (e.g. "abc" and "abcd" both in
    # the list, text = "abcd"), the original loop applied BOTH
    # substitutions sequentially (first "abc"→X, then "abcd"→Y would
    # find no match because the text is now "Xd"). The combined regex
    # finds non-overlapping matches in one pass, so for the overlapping
    # case it picks the longer match (greedy leftmost-longest), which is
    # the user-intuitive behaviour. The bundled corrections.json has no
    # overlapping phrases, so this difference is theoretical.
    parts.sort(key=len, reverse=True)
    pattern = re.compile("|".join(parts), re.IGNORECASE)
    return pattern, lookup


def _get_phrases_regex() -> tuple[re.Pattern[str] | None, dict[str, str]]:
    """Return the combined regex for ``_active_phrases``, rebuilding if stale.

    Cached by object identity: when ``configure_corrections`` (or a test)
    replaces the list object, ``cached_list is _active_phrases`` fails and
    the cache rebuilds on the next call. Identity (not ``id()``) is used
    so a GC'd list whose address was reused cannot produce a stale hit.
    """
    global _phrases_re_cache
    cached_list, cached_re, cached_lookup = _phrases_re_cache
    if cached_list is _active_phrases:
        return cached_re, cached_lookup
    new_re, new_lookup = _build_phrases_regex(_active_phrases)
    _phrases_re_cache = (_active_phrases, new_re, new_lookup)
    return new_re, new_lookup


def _get_extra_words_regex() -> tuple[re.Pattern[str] | None, dict[str, str]]:
    """Return the combined regex for ``_active_extra_words``, rebuilding if stale."""
    global _extra_words_re_cache
    cached_list, cached_re, cached_lookup = _extra_words_re_cache
    if cached_list is _active_extra_words:
        return cached_re, cached_lookup
    new_re, new_lookup = _build_phrases_regex(_active_extra_words)
    _extra_words_re_cache = (_active_extra_words, new_re, new_lookup)
    return new_re, new_lookup


def configure_corrections(
    config_dir: Path | None = None,
    corrections_path: str | None = None,
) -> str | None:
    """Load corrections from external JSON (if present).

    When an external file exists and is valid, its entries replace the
    built-in defaults.  Otherwise the built-in dicts are restored.
    Call this once at startup so that ``clean_transcribed_text`` uses
    the user-provided corrections.

    Returns an error message string if the external file
    exists but could not be loaded (malformed JSON, permission error,
    etc.), or None on success / no-file.  Callers can surface this
    to the user via a tray notification so they know why their
    corrections aren't taking effect.

    The user-corrections path is resolved inside ``_active_corrections``
    → ``_load_external_corrections`` (which raises ``CorrectionsLoadError``
    on a malformed file, caught below and surfaced as the returned
    error-message string). The previous inline ``_user_path`` block
    duplicated that resolution and was dead — removed.
    """
    global _active_misspellings, _active_phrases, _active_extra_words

    # previously this function did its OWN ``_secure_read_text`` +
    # ``json.loads(raw)`` parse to detect a malformed user file, and then
    # IMMEDIATELY called ``_active_corrections`` (which calls
    # ``_load_external_corrections``) that re-parsed the SAME file via the
    # SAME ``_secure_read_text`` + ``json.loads`` path — a redundant
    # double-read+double-parse on every configure call (and a double
    # failure on every malformed file). The inline parse was a leftover
    # from before  introduced the typed ``CorrectionsLoadError``:
    # the inline parse produced an error message string, while the typed
    # exception is the canonical signal. We now rely on
    # ``_active_corrections`` → ``_load_external_corrections`` to raise
    # ``CorrectionsLoadError`` on a malformed file, and we surface that
    # as the returned error message string. The error-message format
    # changes slightly (``"Corrections files existed but could not be
    # loaded: <name>: <reason>"`` instead of the previous ``"Corrections
    # file <name> is malformed: <reason>"``) but the contract — return a
    # descriptive string on failure, ``None`` on success — is preserved.
    error_msg: str | None = None
    try:
        result = _active_corrections(config_dir, corrections_path)
    except CorrectionsLoadError as e:
        error_msg = str(e)
        log.warning("[CLEANUP] %s", error_msg)
        # Fall back to bundled-only path so cleanup() still works —
        # mirrors the previous behavior where the inline parse set
        # ``error_msg`` and then ``_active_corrections`` was called
        # (which would have raised on a malformed file; the caller
        # never saw the raise because the inline parse already
        # detected the malformation). Now we catch the raise and
        # re-load with the user path disabled (``config_dir=None,
        # corrections_path=None``) so only the bundled corrections
        # are loaded — the user file is skipped entirely, which is
        # safe because we already know it's malformed.
        result = _active_corrections(config_dir=None, corrections_path=None)
    misspellings, phrases, extra_words = result
    # take the lock when replacing the module-level mutables so
    # a concurrent cleanup() call doesn't see a half-replaced state.
    with _active_state_lock:
        _active_misspellings = misspellings
        _active_phrases = phrases
        _active_extra_words = extra_words
    return error_msg


def clean_transcribed_text(
    text: str,
    *,
    auto_punctuation: bool = False,
    skip_corrections: bool = False,
) -> str:
    """Apply conservative cleanup without changing the user's meaning.

    when ``skip_corrections=True``, the misspelling, phrase,
    and extra-word corrections are skipped.  This is used when
    ``VocabularyManager`` will apply the same corrections later in the
    pipeline (avoiding double-application).  The structural cleanup
    (spacing, self-corrections, capitalization) always runs.

    The four token-based structural helpers
    (``_clean_self_corrections``, ``_remove_adjacent_duplicate_phrases``,
    ``_remove_near_duplicate_words``, ``_fix_common_misspellings``) used
    to each call ``text.split(" ")`` independently — 4 tokenisations per
    dictation.  We now split once after ``_normalize_spacing`` (which
    collapses whitespace to single spaces) and pass the token list
    through the ``*_tokens`` variants, re-joining only when we hand off
    to the regex-based helpers (``_correct_whisper_phrases`` /
    ``_remove_extra_words``).  Same outputs, fewer allocations.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""
    cleaned = _normalize_spacing(cleaned)
    # tokenise ONCE and reuse the list across the four
    # token-based helpers.  _normalize_spacing guarantees single-space
    # separation, so split(" ") is lossless here.
    tokens = cleaned.split(" ")
    tokens = _clean_self_corrections_tokens(tokens)
    tokens = _remove_adjacent_duplicate_phrases_tokens(tokens)
    tokens = _remove_near_duplicate_words_tokens(tokens)
    if not skip_corrections:
        tokens = _fix_common_misspellings_tokens(tokens)
    cleaned = " ".join(tokens)
    if not skip_corrections:
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
# precompile the per-token misspelling wrapping regex. Previously
# a re.match with an uncompiled ``^(\W*)(\w+)(\W*)$`` pattern was called
# per-token — wasteful since _fix_common_misspellings runs on every
# dictation.
_RE_MISSPELL_WRAP = re.compile(r"^(\W*)(\w+)(\W*)$")
# precompile the regexes used in _looks_like_question. Previously
# re.split and re.findall with uncompiled patterns were used. Only
# reached when auto_punctuation=True, but precompiling is free and
# avoids the re module's per-call cache lookup.
_RE_SENTENCE_SPLIT = re.compile(r"[.!?]\s+")
_RE_WORD_CHARS = re.compile(r"[A-Za-z']+")


def _normalize_spacing(text: str) -> str:
    # PERF-004: use precompiled patterns
    text = _RE_SPACING_WS.sub(" ", text).strip()
    text = _RE_SPACING_PUNCT_BEFORE.sub(r"\1", text)
    text = _RE_SPACING_PUNCT_AFTER.sub(r"\1 ", text)
    return text.strip()


def _clean_self_corrections_tokens(tokens: list[str]) -> list[str]:
    """Token-based core of ``_clean_self_corrections``.

    factored out so ``clean_transcribed_text`` can tokenize the
    dictation once and pass the token list through the four token-based
    helpers without re-splitting + re-joining between each step.
    """
    output: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        if i + 1 < n:
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
                    for a, b in zip(key1, key2, strict=False):
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
    return output


def _clean_self_corrections(text: str) -> str:
    """Remove self-correction patterns like 'talk talking' → 'talking'."""
    return " ".join(_clean_self_corrections_tokens(text.split(" ")))


def _remove_adjacent_duplicate_phrases_tokens(tokens: list[str]) -> list[str]:
    """Token-based core of ``_remove_adjacent_duplicate_phrases`` ()."""
    output: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        duplicate_len = _duplicate_phrase_length(tokens, i)
        if duplicate_len:
            output.extend(tokens[i : i + duplicate_len])
            i += duplicate_len * 2
        else:
            output.append(tokens[i])
            i += 1
    return output


def _remove_adjacent_duplicate_phrases(text: str) -> str:
    return " ".join(_remove_adjacent_duplicate_phrases_tokens(text.split(" ")))


def _duplicate_phrase_length(tokens: list[str], index: int) -> int:
    max_len = min(4, (len(tokens) - index) // 2)
    for size in range(max_len, 0, -1):
        left = [_token_key(token) for token in tokens[index : index + size]]
        right = [_token_key(token) for token in tokens[index + size : index + (size * 2)]]
        if left == right and any(left):
            if size == 1 and left[0] in _INTENTIONAL_REPEAT_WORDS:
                continue
            return size
    return 0


def _remove_near_duplicate_words_tokens(tokens: list[str]) -> list[str]:
    """Token-based core of ``_remove_near_duplicate_words`` ()."""
    output: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        if i + 1 < n:
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
                if abs(len(key1) - len(key2)) <= 2 and (key1 in key2 or key2 in key1):
                    longer = tokens[i] if len(key1) >= len(key2) else tokens[i + 1]
                    output.append(longer)
                    i += 2
                    continue
        output.append(tokens[i])
        i += 1
    return output


def _remove_near_duplicate_words(text: str) -> str:
    """Remove adjacent words where one is a substring of the other."""
    return " ".join(_remove_near_duplicate_words_tokens(text.split(" ")))


def _fix_common_misspellings_tokens(tokens: list[str]) -> list[str]:
    """Token-based core of ``_fix_common_misspellings`` ().

    uses the module-level precompiled ``_RE_MISSPELL_WRAP``
        instead of calling ``re.match`` with an uncompiled pattern per
        token (the pattern wraps a word with its leading/trailing
        non-word characters so punctuation is preserved across the
        substitution).
    """
    misspellings = _active_misspellings
    output: list[str] = []
    for token in tokens:
        key = _token_key(token)
        if key in misspellings:
            correction = misspellings[key]
            match = _RE_MISSPELL_WRAP.match(token)
            token = f"{match.group(1)}{correction}{match.group(3)}" if match else correction
        output.append(token)
    return output


def _fix_common_misspellings(text: str) -> str:
    """Fix common Whisper small-model misspellings."""
    return " ".join(_fix_common_misspellings_tokens(text.split(" ")))


def _apply_phrase_substitutions(
    text: str,
    get_regex,
    replacer,
) -> str:
    """Apply a combined-alternation regex substitution to ``text``.

    Unified core of :func:`_correct_whisper_phrases` and
    :func:`_remove_extra_words` (DRY fix). The two functions
    previously each repeated the same shape:

        pattern, lookup = get_regex()
        if pattern is None:
            return text
        return pattern.sub(<callback using lookup>, text)

    with the only meaningful difference being the per-match callback
    (case-preserving for phrase corrections vs plain literal
    substitution for extra-word removal). This helper hoists the
    shared short-circuit + ``re.sub`` plumbing out; each call site
    now passes ``get_regex`` (returning ``(pattern, lookup)``) and
    ``replacer`` (taking ``(match, lookup)`` and returning the
    substitution string).

    Both call sites route pattern resolution through the same
    identity-keyed combined-regex cache (``_get_phrases_regex`` /
    ``_get_extra_words_regex``), so a concurrent
    ``configure_corrections`` call invalidates the cache via the
    ``is`` identity change — closing the parallel-lists race the
    prior ``_remove_extra_words`` had reintroduced.
    """
    pattern, lookup = get_regex()
    if pattern is None:
        return text
    return pattern.sub(lambda m: replacer(m, lookup), text)


def _correct_whisper_phrases(text: str) -> str:
    """Fix known Whisper small-model phrase misrecognitions.

    previously ``re.compile(re.escape(bad), re.IGNORECASE)``
        was called inside the loop, recompiling the same pattern on every
        dictation. With hundreds of phrases × thousands of dictations,
        that's significant CPU. We now use a module-level cache keyed on
        the phrase string so each pattern is compiled at most once.

    previously this function did an O(N×M) regex search per
        dictation (one ``pattern.search(lower)`` per phrase).  We now use
        Python's highly-optimised ``bad.lower() in lower`` substring check
        for the per-phrase membership test (benchmarked ~10× faster than
        ``re.Pattern.search`` for the short patterns in corrections.json,
        because ``str.__contains__`` runs in C with the Two-Way algorithm
        while regex search carries engine overhead per call) and reuse
        a combined-alternation regex (see ``_get_phrases_regex``) for the
        actual ``pattern.sub`` substitution.

        This revision replaces the O(N×M) per-phrase membership loop with
        a single O(N+M) ``re.sub`` pass driven by a combined-alternation
        regex (``re.compile(r"(?:p1|p2|...)", re.IGNORECASE)``). The SRE
        engine compiles alternations of ``re.escape``d literals to a trie,
        so a single pass through the text finds every phrase match
        regardless of how many phrases are in the dictionary. The
        ``re.sub`` callback looks up the replacement by
        ``match.group(0).lower()`` in a precomputed dict and applies the
        L19 case-preserving substitution. Behaviour is identical to the
        original for non-overlapping phrases: ``re.sub`` naturally uses
        the ORIGINAL text for matching (not the mutated text), so a
        substitution that introduces a phrase that LATER matches another
        phrase does NOT trigger a second substitution — preserving the
    invariant. (For overlapping phrases within the same
        alternation, the longer match wins via the
        length-descending sort in ``_build_phrases_regex``; the bundled
        corrections.json has no overlapping phrases, so this is
        theoretical.)

    the shared short-circuit + ``re.sub`` plumbing is
        hoisted into :func:`_apply_phrase_substitutions`; this function
        passes the case-preserving replacer that re-applies the original
        casing to the looked-up replacement.
    """
    return _apply_phrase_substitutions(
        text,
        _get_phrases_regex,
        lambda m, lookup: _apply_case_preserving_replacement(m, lookup[m.group(0).lower()]),
    )


def _apply_case_preserving_replacement(match: re.Match[str], good: str) -> str:
    """Replace ``match.group(0)`` with ``good`` preserving the original casing.

    hoisted out of ``_correct_whisper_phrases`` so the
        function object is created once per call site rather than once
        per bad-word in the corrections table. The function follows
        four casing rules, in order:

        1. ALL UPPER → ``good.upper()`` (e.g. "WONT" → "WON'T").
        2. Title case (first letter upper, rest lower) → first letter
           of ``good`` upper, rest as-is.
        3. Mixed case (any upper after the first char) → map each
           uppercase position from the original to the replacement,
           lowercasing the rest.
        4. All lower (the common case) → ``good`` as-is.
    """
    original = match.group(0)
    # Apply same casing pattern as original
    if original.isupper():
        return good.upper()
    if original[0].isupper() and not original[1:].isupper():
        # Title case: first letter upper, rest lower
        return good[0].upper() + good[1:]
    if any(c.isupper() for c in original[1:]):
        # Mixed case: try to map uppercase positions from original to replacement
        result = list(good.lower())
        for i, c in enumerate(original):
            if i < len(result) and c.isupper():
                result[i] = result[i].upper()
        return "".join(result)
    return good


def _remove_extra_words(text: str) -> str:
    """Remove common extra word insertions from Whisper.

    previously ``re.compile(re.escape(bad), re.IGNORECASE)`` was
        called inside the loop, recompiling the same pattern on every dictation.
        We now reuse the same module-level cache as ``_correct_whisper_phrases``.

    same ``pattern.search`` → ``bad.lower() in lower`` optimisation
        as ``_correct_whisper_phrases`` (see its docstring for the rationale).

    mirror the  fix applied to ``_correct_whisper_phrases``.
        Always resolve the pattern via the combined-alternation regex
        built by ``_get_extra_words_regex`` (keyed on the bad string via
        the lookup dict), instead of indexing a parallel list of
        ``_active_extra_word_patterns`` alongside ``_active_extra_words``.
        The parallel-lists indexing reintroduced the race here: if
        ``configure_corrections()`` ran between reading
        ``_active_extra_words`` and ``_active_extra_word_patterns``,
        ``patterns[idx]`` could be a compiled regex for a DIFFERENT bad
        string than ``phrases[idx]``, producing corrupted text. The
        combined-regex cache keyed on the list object's identity
        guarantees the pattern always matches the active phrase set,
        at O(1) cost after warmup.

        This revision replaces the O(N×M) per-phrase membership loop with
        a single O(N+M) ``re.sub`` pass driven by a combined-alternation
        regex (mirroring the ``_correct_whisper_phrases`` refactor). The
        ``re.sub`` callback looks up the plain replacement by
        ``match.group(0).lower()`` in a precomputed dict — no
        case-preservation needed for extra-word removal (the original
        ``pattern.sub(good, text)`` substituted the literal ``good``
        string regardless of matched casing, which we preserve).

    the shared short-circuit + ``re.sub`` plumbing is
        hoisted into :func:`_apply_phrase_substitutions`; this function
        passes the plain-literal replacer (no case preservation — the
        original ``pattern.sub(good, text)`` behaviour).
    """
    return _apply_phrase_substitutions(
        text,
        _get_extra_words_regex,
        lambda m, lookup: lookup[m.group(0).lower()],
    )


@functools.lru_cache(maxsize=4096)
def _token_key(token: str) -> str:
    # PERF-PIPE: use precompiled regex instead of re.sub(pattern, ...)
    # PERF-KEY-CACHE: memoize on the token string. The four token-based
    # cleanup helpers (_clean_self_corrections_tokens,
    # _remove_adjacent_duplicate_phrases_tokens via _duplicate_phrase_length,
    # _remove_near_duplicate_words_tokens, _fix_common_misspellings_tokens)
    # each iterate the full token list and re-compute the key for every
    # position — up to ~7N calls for an N-token dictation. Most dictations
    # repeat tokens heavily (function words, punctuation), so a small
    # bounded LRU cache amortises this to ~unique-token-count calls.
    # maxsize=4096 bounds memory in pathological cases; the cache is
    # thread-safe (functools.lru_cache holds an internal lock).
    return _RE_TOKEN_KEY.sub("", token).lower()


# ─── Safe auto-punctuation ──────────────────────────────────────────────

# minimum word count before ``_add_safe_terminal_punctuation``
# appends a period or question mark. Short fragments like "ok" or
# "no thanks" should never be auto-punctuated — appending "." to
# "ok" produces "ok." which the user did NOT dictate, and
# auto-punctuating a two-word short reply like "no thanks" risks
# turning a deliberate lowercase response into a forced
# sentence-shaped artifact. 4 is the empirically chosen floor: at
# 5+ words the heuristic is reliable (a real sentence) and the
# false-positive rate drops to near zero.
_MIN_WORDS_FOR_TERMINAL_PUNCTUATION: Final[int] = 4

# Patterns that should NOT get terminal punctuation appended
_NO_PUNCTUATION_PATTERNS = [
    re.compile(r"https?://"),  # URLs
    re.compile(r"\.(com|org|net|io|dev)$", re.IGNORECASE),  # Domain names
    re.compile(r"[\\/]"),  # File paths
    re.compile(r"`[^`]*`"),  # Inline code
    re.compile(r"\{\{.*\}\}"),  # Template variables
    re.compile(r"\{.*\}"),  # Variable placeholders
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
    # the magic ``4``-word cutoff was extracted to a named
    # constant so the threshold is auditable and the rationale
    # (single-word fragments like "ok" or two-word short replies
    # like "no thanks" should never be auto-punctuated) lives next
    # to the number, not in an unwritten comment.
    if len(words) <= _MIN_WORDS_FOR_TERMINAL_PUNCTUATION:
        return text

    if _looks_like_question(text):
        return f"{text}?"
    return f"{text}."


def _looks_like_question(text: str) -> bool:
    """Detect whether the final sentence looks like a question.

        Uses a conservative set of question openers that excludes "how"
        and "what" to avoid false positives on declarative sentences.

    uses the module-level precompiled ``_RE_SENTENCE_SPLIT`` and
        ``_RE_WORD_CHARS`` patterns instead of ``re.split`` / ``re.findall``
        with uncompiled string patterns.
    """
    sentence = _RE_SENTENCE_SPLIT.split(text.strip())[-1]
    words = _RE_WORD_CHARS.findall(sentence.lower())
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
