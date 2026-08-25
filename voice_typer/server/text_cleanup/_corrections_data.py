"""Corrections loading + corrections-driven casing for text cleanup.

Split verbatim out of the pre-split ``text_cleanup`` module.

Holds:

* ``CorrectionsLoadError`` + the bundled / user / external corrections
  loaders (including the truncation + length-filter helpers and the
  ``_active_corrections`` orchestrator).
* The Roman-numeral / pronoun-I cluster: bundled word-set tables, the
  user-extension state that ``_load_external_corrections`` refreshes on
  every load, the standalone-``i`` regex, the bounded word-scan helpers,
  and :func:`_capitalize_pronoun_i`.

NOTE on co-location: the ``_user_roman_numeral_*_extensions`` globals are
REBOUND (not mutated) inside ``_load_external_corrections`` via a
``global`` statement, and read as bare names inside
``_capitalize_pronoun_i``. A Python function's ``global`` always targets
its own module, so writer + reader must share one module for the
verbatim bodies to keep working — hence this cluster lives here rather
than in :mod:`._casing`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from voice_typer.server.vocabulary import BUNDLED_CORRECTIONS_PATH as _BUNDLED_CORRECTIONS_PATH

log = logging.getLogger("voice_typer.server.text_cleanup")

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

    helper: factored out of :func:`_load_external_corrections`
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

    helper: factored out of :func:`_load_external_corrections`.
    Returns ``(path, misspellings, phrase_corrections, extra_word_patterns,
    roman_context_ext, roman_following_ext, loaded_any, load_errors)``.

    When no user file exists (no ``corrections_path`` AND no ``config_dir``,
    OR the resolved path does not exist): returns
    ``(None, {}, [], [], set(), set(), False, [])`` — no error, no log
    (matching the prior ``if path is not None and path.exists()`` silent skip).

    On success: returns the resolved path (for the success log),
    FRESH containers (NOT merged with bundled — the orchestrator
    merges them via ``dict.update`` / ``list.extend``), the
    Roman-numeral word-set extensions (lowercase strings,
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

    the four phases (load bundled → load user → merge →
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

    # refresh the user-extension state for the Roman-numeral word
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
            "Corrections files existed but were malformed or could not be loaded: " + "; ".join(load_errors)
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
    length limits.

    rationale: long patterns cause expensive regex backtracking
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


# Roman numeral context words that precede lowercase 'i'.
#
# these remain the BUNDLED DEFAULTS. Users can extend them
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

# user-provided extensions to the bundled Roman-numeral word
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
    # check both the bundled defaults AND the user-provided
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
