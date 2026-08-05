"""Lightweight cleanup for raw speech-to-text output."""

import collections as _collections
import contextlib
import functools
import json
import logging
import re
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from voice_typer.server.vocabulary import BUNDLED_CORRECTIONS_PATH as _BUNDLED_CORRECTIONS_PATH

log = logging.getLogger(__name__)
# Question openers used by _looks_like_question().
# "how" and "what" are EXCLUDED because they frequently start
# declarative sentences (e.g. "How to install Python",
# "What I did yesterday") where a question mark would be wrong.
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

# ─── Corrections loaded from corrections.json ──────────────────────────
#
# The bundled corrections.json is the canonical source of corrections.
# The built-in dicts below have been removed to avoid duplication (P4 #29).
# A minimal fallback is provided in _active_corrections() if corrections.json
# is somehow missing.

# ─── External corrections loader ─────────────────────────────────────────
# When a corrections.json exists in the config directory (or at the path
# specified by config.corrections_path), its entries OVERRIDE the built-in
# defaults.  This allows users to tailor corrections for their model/version
# without editing source code.


class CorrectionsLoadError(RuntimeError):
    """Raised when external corrections could not be loaded.

    previously ``_load_external_corrections`` returned ``None``
    for both "no file" and "load failed", and the caller couldn't
    distinguish them. We now raise this typed exception on load
    failure; "no file" still returns ``None``.
    """


def _load_bundled_corrections():
    """Load the bundled ``corrections.json`` shipped with the app.

    AC-82 helper: factored out of :func:`_load_external_corrections`
    so the orchestrator is left with phase composition (load bundled
    → load user → merge → truncate → filter) rather than 4 inline
    phases. Returns ``(misspellings, phrase_corrections,
    extra_word_patterns, loaded_any, load_errors)``.

    On success: fresh containers populated from the bundled JSON,
    ``loaded_any=True``, ``load_errors=[]``. On failure (file missing
    OR parse error): empty containers, ``loaded_any=False``, and a
    single ``"bundled: <exc>"`` entry in ``load_errors`` (the missing-
    file case is the normal first-launch path and produces no error
    entry — matching the prior inline behaviour where
    ``if _BUNDLED_CORRECTIONS_PATH.exists()`` skipped the load block
    silently).

    The bundled path uses the lenient ``data.get("phrase_corrections",
    [])`` form (no outer ``isinstance`` check) to preserve the prior
    behaviour — any iterable of 2-element pairs is accepted. The user
    path (:func:`_load_user_corrections`) is stricter.
    """
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

    AC-82 helper: factored out of :func:`_load_external_corrections`.
    Returns ``(path, misspellings, phrase_corrections, extra_word_patterns,
    roman_context_ext, roman_following_ext, loaded_any, load_errors)``.

    When no user file exists (no ``corrections_path`` AND no ``config_dir``,
    OR the resolved path does not exist): returns
    ``(None, {}, [], [], set(), set(), False, [])`` — no error, no log
    (matching the prior ``if path is not None and path.exists()`` silent skip).

    On success: returns the resolved path (for the success log),
    FRESH containers (NOT merged with bundled — the orchestrator
    merges them via ``dict.update`` / ``list.extend``), the
    AC-84 Roman-numeral word-set extensions (lowercase strings,
    EMPTY sets if the user file doesn't include those keys),
    ``loaded_any=True``, ``load_errors=[]``.

    On failure (parse error, IO error): returns the path (for the
    error log), empty containers, empty extension sets,
    ``loaded_any=False``, and a single ``"<filename>: <exc>"`` entry
    in ``load_errors``.

    The user path uses the strict ``isinstance(data[key], list)`` /
    ``isinstance(data[key], dict)`` form (preserving the prior inline
    behaviour where a non-dict ``misspellings`` was silently skipped
    rather than raising).
    """
    path: Path | None = None
    if corrections_path:
        path = Path(corrections_path)
    elif config_dir is not None:
        path = config_dir / "voice-typer-corrections.json"

    if path is None or not path.exists():
        return None, {}, [], [], set(), set(), False, []

    try:
        # SEC-002: use _secure_read_text to prevent symlink-TOCTOU attacks
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
        # AC-84: extract optional Roman-numeral word-set extensions.
        # Both keys default to empty sets when absent or wrongly typed
        # (silent skip — matches the strict isinstance pattern used for
        # the other correction fields). Strings are lowercased so the
        # case-insensitive membership check in _capitalize_pronoun_i
        # works regardless of how the user capitalised them in the file.
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
    """Load corrections from an external JSON file.

    Loads bundled corrections.json first, then merges user-provided
    file on top.  Returns (misspellings, phrase_corrections, extra_word_patterns)
    from the external file, or None if no corrections file exists.

    raises ``CorrectionsLoadError`` when a file exists but
    could not be parsed (was previously a silent ``None`` return).

    AC-82: the four phases (load bundled → load user → merge →
    truncate → filter) are now composed from focused helpers
    (:func:`_load_bundled_corrections`, :func:`_load_user_corrections`,
    :func:`_truncate_corrections`, :func:`_filter_corrections_by_length`)
    instead of being inlined as copy-paste blocks. The body drops
    from ~160 lines to ~50; each phase is a one-liner.
    """
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

    # AC-84: refresh the user-extension state for the Roman-numeral word
    # sets on every load. The state is REPLACED (not extended) so removing
    # the keys from the user file reverts to bundled-only behaviour. The
    # bundled defaults themselves are constants and never mutated. The
    # state update happens outside the ``_active_state_lock`` (same
    # accepted race window as the other module-level state — the lock is
    # taken in ``configure_corrections`` for the other state, and a
    # concurrent ``clean_transcribed_text`` call seeing the OLD extension
    # set for one dictation is benign: it just uses a slightly-staler set
    # of context words, never a corrupt one).
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

    # raise whenever ANY load error occurred — previously the
    # raise was gated on ``not loaded_any``, which meant a malformed
    # USER file was silently swallowed when the BUNDLED file loaded OK
    # (the bundled corrections were returned as if nothing had gone
    # wrong, and the caller had no way to know the user's edits were
    # being ignored). ``configure_corrections`` catches this raise and
    # surfaces it as its returned error-message string, then falls back
    # to a bundled-only reload so ``clean_transcribed_text`` still
    # works. The "no files existed" case (``loaded_any=False`` AND
    # ``load_errors`` empty) still returns ``None`` (silent fallback —
    # first-launch with no user file is the normal case).
    #
    # The message includes the word "malformed" so existing caller-side
    # assertions (``tests/test_text_cleanup.py::
    # TestConfigureCorrectionsSurfacesLoadErrors::test_returns_error_for_malformed_json``
    # checks for ``"malformed" in result.lower() or "invalid" in
    # result.lower()``) keep passing after the  refactor that
    # replaced the inline parse with the typed-exception path.
    if load_errors:
        raise CorrectionsLoadError(
            "Corrections file(s) existed but were malformed or could not be loaded: " + "; ".join(load_errors)
        )
    if not loaded_any:
        return None

    # SEC-010/SEC-011: Cap corrections to prevent ReDoS and resource exhaustion
    max_corrections_entries = 5000
    max_pattern_length = 200
    max_replacement_length = 500

    # the 3 per-correction-type truncation blocks + 3 per-correction-
    # type length-filter loops were copy-paste with different variable names
    # and subtly different filter semantics (misspellings/extra_words used
    # separate pattern-vs-replacement counters; phrases used a single
    # counter with an OR condition). Both helpers below collapse the
    # 6 inline blocks into 6 one-liners, and the filter helper unifies
    # on the OR semantics (drop if EITHER field exceeds its limit —
    # identical drop BEHAVIOR to the prior separate-counter form, since
    # an entry with both fields too long was dropped either way; only
    # the per-counter log message granularity changes).
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
    """cap a corrections list at ``max_count`` entries.

    Keeps the first ``max_count`` items (matching the prior
    ``list(items)[:max_count]`` slice semantics — preserves load order
    so bundled corrections, which load first, are never evicted by
    user-provided corrections appended on top). Logs a single
    ``[CLEANUP] Too many <label>...`` warning when truncation fires so
    operators can spot a runaway corrections file.

    The prior per-call-site inline form logged the literal count
    (``(%d > %d)``) for misspellings but not for phrases/extra-words;
    the unified helper logs the count for ALL three so the operator
    sees the same diagnostic granularity regardless of correction type.
    """
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
    """drop corrections whose pattern OR replacement exceeds the
    SEC-011 length limits.

    SEC-011 rationale: long patterns cause expensive regex backtracking
    (ReDoS vector); long replacements cause excessive memory/CPU during
    substitution. The limit is per-field — an entry is dropped if EITHER
    field exceeds its limit (the OR semantics unify the prior
    per-correction-type variants, which all dropped the entry either way
    but counted pattern-vs-replacement overflows separately for
    misspellings).

    Returns the filtered list (preserves input order). Logs a single
    ``[CLEANUP] Dropped N <label> exceeding length limits`` warning
    when any entries are dropped, so operators can spot a malformed
    corrections file.
    """
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


# ─── Active corrections (initialized to built-in defaults) ──────────────
# Updated by configure_corrections() when an external corrections file
# is available.  The cleanup functions below read from these instead of
# the built-in dicts directly.

_active_misspellings: dict[str, str] = {}
_active_phrases: list[tuple[str, str]] = []
_active_extra_words: list[tuple[str, str]] = []
# eager-compiled patterns, kept in parallel with _active_phrases
# _active_extra_words so each substitution step reuses a precompiled
# Pattern instead of touching the LRU cache on every dictation. The LRU
# cache (_phrase_pattern_cache / _get_compiled_phrase_pattern below) is
# kept for backward compatibility with the test suite and as a fallback.
_active_phrase_patterns: list["re.Pattern[str]"] = []
_active_extra_word_patterns: list["re.Pattern[str]"] = []
# guard the three module-level mutables with a lock so
# concurrent dictations don't clobber each other. The proper fix is
# to move these into a TextCleanupService instance; for now the lock
# prevents the worst race (two threads each replacing the dict mid-
# cleanup of the other). The instance refactor is deferred because
# it touches ~20 call sites.
_active_state_lock = threading.Lock()

# cache of compiled regex patterns for phrase corrections.
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
        with contextlib.suppress(KeyError):
            _phrase_pattern_cache.popitem(last=False)
    _phrase_pattern_cache[phrase] = compiled
    return compiled


# helper that eagerly precompiles one Pattern per phrase at
# configure_corrections time so the hot cleanup path never pays a
# compile cost. The patterns are used by pattern.sub() during the
# substitution step; the per-phrase membership test ("does this phrase
# appear in the dictation?") uses Python's highly-optimised
# ``bad.lower() in lower`` substring check instead of regex search,
# which benchmarks ~10× faster than re.Pattern.search for the short
# patterns in corrections.json.
def _compile_phrase_patterns(
    phrases: "list[tuple[str, str]]",
) -> "list[re.Pattern[str]]":
    """Eagerly compile one case-insensitive ``re.Pattern`` per phrase.

    Each pattern is ``re.compile(re.escape(bad), re.IGNORECASE)``,
    identical to what ``_get_compiled_phrase_pattern`` would produce
    on a cache miss — but built once at corrections-load time instead
    of lazily on first use.
    """
    return [re.compile(re.escape(bad), re.IGNORECASE) for bad, _ in phrases]


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
_phrases_re_cache: tuple[object | None, "re.Pattern[str] | None", dict[str, str]] = (
    None,
    None,
    {},
)
_extra_words_re_cache: tuple[object | None, "re.Pattern[str] | None", dict[str, str]] = (
    None,
    None,
    {},
)


def _build_phrases_regex(
    phrases: "list[tuple[str, str]]",
) -> "tuple[re.Pattern[str] | None, dict[str, str]]":
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


def _get_phrases_regex() -> "tuple[re.Pattern[str] | None, dict[str, str]]":
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


def _get_extra_words_regex() -> "tuple[re.Pattern[str] | None, dict[str, str]]":
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
    global _active_phrase_patterns, _active_extra_word_patterns

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
    # changes slightly (``"Corrections file(s) existed but could not be
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
    # eagerly precompile per-phrase patterns while we hold the
    # lock, so the hot cleanup path reuses precompiled Patterns for
    # substitution and uses a cheap ``in`` substring check (instead of
    # re.Pattern.search) for the per-phrase membership test.  Benchmarks
    # show this is ~10× faster than the original per-phrase regex search.
    misspellings, phrases, extra_words = result
    phrase_patterns = _compile_phrase_patterns(phrases)
    extra_word_patterns = _compile_phrase_patterns(extra_words)
    # take the lock when replacing the module-level mutables so
    # a concurrent cleanup() call doesn't see a half-replaced state.
    with _active_state_lock:
        _active_misspellings = misspellings
        _active_phrases = phrases
        _active_extra_words = extra_words
        _active_phrase_patterns = phrase_patterns
        _active_extra_word_patterns = extra_word_patterns
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
# _fix_file_extensions compiled pattern
_RE_FILE_EXT = re.compile(r"(\w+)\.\s+([a-zA-Z]{2,4})\b")
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
    :func:`_remove_extra_words` (AC-81 DRY fix). The two functions
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
        while regex search carries engine overhead per call) and reuse the
        eagerly-precompiled ``_active_phrase_patterns`` for the actual
        ``pattern.sub`` substitution.

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

    AC-81: the shared short-circuit + ``re.sub`` plumbing is
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
        Always resolve the pattern via the LRU-cached
        ``_get_compiled_phrase_pattern(bad)`` keyed on the bad string itself,
        instead of indexing ``_active_extra_word_patterns`` in parallel with
        ``_active_extra_words``. The parallel-lists indexing reintroduced the
    race here: if ``configure_corrections()`` ran between reading
        ``_active_extra_words`` and ``_active_extra_word_patterns``,
        ``patterns[idx]`` could be a compiled regex for a DIFFERENT bad
        string than ``phrases[idx]``, producing corrupted text. The LRU
        cache keyed on the bad string itself guarantees the pattern always
        matches the phrase, at O(1) cost after warmup.

        This revision replaces the O(N×M) per-phrase membership loop with
        a single O(N+M) ``re.sub`` pass driven by a combined-alternation
        regex (mirroring the ``_correct_whisper_phrases`` refactor). The
        ``re.sub`` callback looks up the plain replacement by
        ``match.group(0).lower()`` in a precomputed dict — no
        case-preservation needed for extra-word removal (the original
        ``pattern.sub(good, text)`` substituted the literal ``good``
        string regardless of matched casing, which we preserve).

    AC-81: the shared short-circuit + ``re.sub`` plumbing is
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


# Roman numeral context words that precede lowercase 'i'.
#
# AC-84: these remain the BUNDLED DEFAULTS. Users can extend them
# (e.g. with "george", "edward", "charles", "napoleon", "alexander"
# — names the original hardcoded set was missing) by adding a
# ``roman_numeral_context_words`` list (lowercase strings) to their
# ``voice-typer-corrections.json``. The user-provided words are
# purely ADDITIVE to this bundled set — they extend, never replace.
# See :func:`_load_user_corrections` for the loader path.
#
# Format in the user corrections file:
#     {
#         "roman_numeral_context_words": ["george", "edward", ...],
#         "roman_numeral_following_words": ["until", "until-iv", ...]
#     }
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

# AC-84: user-provided extensions to the bundled Roman-numeral word
# sets. Populated by :func:`_load_external_corrections` (via
# :func:`_load_user_corrections`) when the user corrections file
# includes ``roman_numeral_context_words`` /
# ``roman_numeral_following_words`` keys. Resets to empty on every
# load call (so removing the keys from the user file reverts to
# bundled-only behaviour). Checked ADDITIVELY to the bundled defaults
# in :func:`_capitalize_pronoun_i` — a word in EITHER set triggers
# the Roman-numeral lowercase behaviour.
_user_roman_numeral_context_extensions: set[str] = set()
_user_roman_numeral_following_extensions: set[str] = set()

# precompiled regex for standalone 'i' (not preceded or followed by
# an alpha character — preserves the original semantics where 'i3' or '3i'
# do NOT match, which differs from `\b` word boundaries that treat digits
# and underscore as word characters). The regex is compiled once at module
# load; `re.finditer` scans the text in C and yields match positions to
# the Python loop, which then mutates a mutable ``list[text]`` buffer in
# place — avoiding both the per-character Python loop (O(N) Python iter)
# AND the per-match O(N) substring slicing the prior re.sub callback
# performed (``text[:start].rstrip()`` + ``text[end:].lstrip()`` each
# allocated a fresh string of length O(start) / O(N-end), giving O(M·N)
# total slicing for M standalone-'i' matches).
_PRONOUN_I_RE = re.compile(r"(?<![a-zA-Z])i(?![a-zA-Z])")


def _prev_word_ending_at(text: str, end_idx: int) -> str:
    """Return the lowercased word immediately preceding ``end_idx``.

    Mirrors the original ``text[:end_idx].rstrip()`` + ``rsplit(None, 1)[-1]``
    semantics: scan backward skipping only whitespace, then require the
    first non-whitespace char to be alphabetic (a digit or punctuation
    immediately before ``end_idx`` means no preceding word, matching the
    original ``preceding[-1].isalpha()`` guard). Walks back to the start
    of that word and returns it lowercased.

    Bounded by the preceding word length (typically <30 chars) instead
    of allocating ``text[:end_idx]``.
    """
    i = end_idx - 1
    # Skip trailing whitespace (mirrors .rstrip()).
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0:
        return ""
    # Original guard: preceding[-1].isalpha() — a digit/punctuation
    # immediately before the match means no Roman-numeral context applies.
    if not text[i].isalpha():
        return ""
    word_end = i + 1  # exclusive
    # Walk back to the start of the word (mirrors rsplit(None, 1)[-1]).
    while i >= 0 and not text[i].isspace():
        i -= 1
    return text[i + 1 : word_end].lower()


def _next_word_starting_at(text: str, start_idx: int) -> str:
    """Return the lowercased word immediately following ``start_idx``.

    Mirrors the original ``text[start_idx:].lstrip()`` + leading-alpha
    extraction: scan forward skipping only whitespace, then require the
    first non-whitespace char to be alphabetic. Walks forward to the end
    of that word and returns it lowercased.

    Bounded by the following word length (typically <30 chars) instead
    of allocating ``text[start_idx:]``.
    """
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
    """Capitalize the pronoun 'i' but not Roman numeral 'i'.

    replaced the character-by-character loop with a single
        :func:`re.finditer` pass driven by :data:`_PRONOUN_I_RE`.

        This revision eliminates the per-match O(N) substring slicing the
        prior ``re.sub`` callback performed (``text[:start].rstrip()`` +
        ``text[end:].lstrip()`` each allocated a fresh O(N) string, giving
        O(M·N) total slicing for M standalone-'i' matches — quadratic on
        pathological input like ``"i i i i i"``). The replacer now uses
        bounded backward/forward scans (:func:`_prev_word_ending_at` /
        :func:`_next_word_starting_at`) that touch only the surrounding
        word characters (typically <30 chars per match), making the total
        work O(N + M·k) where k is the average word length — effectively
        O(N) for any realistic input.

        Behaviour is identical to the original: a standalone ``i`` is
        capitalized to ``I`` unless the preceding word is a Roman-numeral
        context word (e.g. "King Henry i") OR the following word is a
        Roman-numeral continuation (e.g. "i through iv"), in which case
        it is kept lowercase.
    """
    # Fast path: no standalone-'i' candidates at all.
    if "i" not in text:
        return text
    matches = list(_PRONOUN_I_RE.finditer(text))
    if not matches:
        return text
    # Mutate a mutable buffer in place — no per-match string allocation
    # beyond the O(k) word slices inside the helpers.
    chars = list(text)
    # AC-84: check both the bundled defaults AND the user-provided
    # extension sets (additive — a word in EITHER set triggers the
    # Roman-numeral lowercase behaviour). The extension sets are
    # refreshed by ``_load_external_corrections`` on every
    # ``configure_corrections`` call, so the user's corrections file
    # is the source of truth for extensions.
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


# _add_terminal_punctuation deleted. The safe variant
# (_add_safe_terminal_punctuation) is the only one used in the pipeline.
# The unsafe variant was dead code that shipped in the bundle but was
# never called.


# ─── M2: File extension fix ──────────────────────────────────────────────

_KNOWN_EXTENSIONS = {
    ".txt",
    ".md",
    ".exe",
    ".py",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".bat",
    ".sh",
    ".ps1",
    ".cmd",
    ".msi",
    ".dll",
    ".zip",
    ".rar",
    ".7z",
    ".mp3",
    ".mp4",
    ".avi",
    ".wav",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".ico",
    ".log",
    ".ini",
    ".cfg",
    ".yaml",
    ".yml",
    ".toml",
    ".db",
    ".sqlite",
    ".bak",
    ".tmp",
    ".sys",
    ".mov",
    ".mkv",
    ".webm",
    ".flac",
    ".ogg",
    ".webp",
    ".bmp",
    ".tiff",
    ".psd",
    ".ai",
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
        before = m.group(1)  # word before the dot
        ext = m.group(2)  # extension without leading dot
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
