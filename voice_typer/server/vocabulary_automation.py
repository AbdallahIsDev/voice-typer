"""Vocabulary automation: confidence-score-based correction suggestions.

This module analyzes transcribed text alongside its confidence scores
and proposes vocabulary corrections for low-confidence words.  The
suggestions can be auto-applied (when confidence is very high) or
queued for the user to review via the IPC handlers in
``vocabulary_automation_handlers.py``.

The class is *opt-in*: the dictation pipeline only calls
:meth:`VocabularyAutomation.analyze_transcription` when the config's
``vocabulary_automation_enabled`` flag is True (default False).

Pipeline placement
------------------
Analysis runs AFTER the transcription has been fully cleaned up,
enhanced, polished, and is about to be pasted — so the suggested
corrections target the final text the user sees, not a stale
intermediate.  See ``DictationPipeline._analyze_vocabulary``.

Confidence model
----------------
Faster-whisper emits segment-level ``avg_logprob`` (a log-probability,
typically in ``[-1.0, 0.0]``).  We convert this to a 0–1 confidence
score using the standard logistic transform
``exp(avg_logprob)``.  This is the same transform WhisperX and other
downstream tools use; it's not perfect (it ignores word-level
probabilities) but it's good enough for a "is this segment suspicious"
heuristic.  When the caller passes a per-word ``confidence`` directly
(as Parakeet does), we use it as-is.

Levenshtein distance
--------------------
We use a hand-rolled implementation to avoid adding ``python-Levenshtein``
as a dependency.  The implementation is the standard O(m·n) dynamic
programming variant with a single-row rolling array; for typical
vocabulary entries (5–15 characters) the cost is negligible.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — import only for type checkers
    from voice_typer.server.vocabulary import VocabularyManager

log = logging.getLogger(__name__)


# ─── Data model ─────────────────────────────────────────────────────────────


@dataclass
class CorrectionSuggestion:
    """A single vocabulary correction suggestion.

    Attributes
    ----------
    original : str
        The word as it appeared in the transcription (lower-cased
        for matching, but the original casing is preserved for
        display).
    corrected : str
        The proposed correction.  Either a close Levenshtein match
        from the user's vocabulary, or the same as ``original`` if
        the only signal is low confidence (in which case the user
        supplies the correction via the IPC handler).
    confidence : float
        The confidence score associated with the original word.
        Range [0.0, 1.0].  Lower = more suspicious.
    context : str
        A short snippet of the surrounding text (the sentence the
        word appeared in, truncated to 80 chars).  Used by the
        frontend to show the user where the word came from.
    timestamp : float
        Unix timestamp (seconds since epoch) when the suggestion was
        created.  Used to order suggestions by recency in the UI.
    applied : bool
        Internal: True once the suggestion has been applied to the
        vocabulary (via :meth:`VocabularyAutomation.apply_suggestion`
        or auto-apply).  Excluded from "pending" lists.
    dismissed : bool
        Internal: True once the user has dismissed the suggestion
        via :meth:`VocabularyAutomation.dismiss_suggestion`.
        Excluded from "pending" lists.
    """

    original: str
    corrected: str
    confidence: float
    context: str
    timestamp: float
    applied: bool = False
    dismissed: bool = False

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict for IPC transport.

        Excludes the internal ``applied`` / ``dismissed`` flags —
        those are server-side state the frontend doesn't need.
        """
        return {
            "original": self.original,
            "corrected": self.corrected,
            "confidence": self.confidence,
            "context": self.context,
            "timestamp": self.timestamp,
        }


# ─── Levenshtein distance (hand-rolled, no external dep) ────────────────────


def _levenshtein(a: str, b: str, *, max_distance: int | None = None) -> int:
    """Compute the Levenshtein edit distance between ``a`` and ``b``.

    Uses the standard O(m·n) dynamic programming algorithm with a
    single-row rolling array.  When ``max_distance`` is provided and
    the distance exceeds it, the function returns ``max_distance + 1``
    early — this is the "uR-l" / "bounded Levenshtein" optimization
    that lets us short-circuit irrelevant comparisons without
    computing the full distance.

    Parameters
    ----------
    a, b : str
        The strings to compare.  Case-sensitive — callers should
        normalize case before calling.
    max_distance : int, optional
        If provided and the distance exceeds this, return
        ``max_distance + 1`` immediately.  Use this to skip
        comparisons that can't possibly be a match.

    Returns
    -------
    int
        The edit distance (number of insertions / deletions /
        substitutions), or ``max_distance + 1`` if ``max_distance``
        was provided and the actual distance exceeds it.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # Ensure a is the shorter string (smaller row width).
    if len(a) > len(b):
        a, b = b, a

    m, n = len(a), len(b)
    if max_distance is not None and abs(m - n) > max_distance:
        # Length difference alone exceeds the bound — can't match.
        return max_distance + 1

    # Single-row rolling array.
    previous_row = list(range(m + 1))
    for j in range(1, n + 1):
        current_row = [j] + [0] * m
        b_char = b[j - 1]
        row_min = current_row[0]
        for i in range(1, m + 1):
            insert_cost = current_row[i - 1] + 1
            delete_cost = previous_row[i] + 1
            substitute_cost = previous_row[i - 1] + (
                0 if a[i - 1] == b_char else 1
            )
            current_row[i] = min(insert_cost, delete_cost, substitute_cost)
            if current_row[i] < row_min:
                row_min = current_row[i]
        # Bounded-Levenshtein early exit: if every entry in this row
        # exceeds max_distance, the final answer will too.
        if max_distance is not None and row_min > max_distance:
            return max_distance + 1
        previous_row = current_row

    return previous_row[m]


# ─── Helper: collect known-good words from the vocabulary ───────────────────


def _collect_vocabulary_words(vm: VocabularyManager) -> set[str]:
    """Return the set of "correct" words from the vocabulary manager.

    Includes the *values* (corrected forms) of all dict-based
    categories (misspellings, technical_terms, names, products) and
    the *correct* side of all list-based categories
    (phrase_corrections, extra_word_patterns).  We only consider
    single-word values; multi-word phrases are skipped (we can't
    match them against a single low-confidence word).

    Parameters
    ----------
    vm : VocabularyManager
        The vocabulary manager to read from.

    Returns
    -------
    set[str]
        Lower-cased set of known-good words.
    """
    words: set[str] = set()
    try:
        for cat in ("misspellings", "technical_terms", "names", "products"):
            data = vm.get_category(cat)
            if isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, str):
                        for token in value.split():
                            token = token.strip().lower()
                            if token and token.isalpha():
                                words.add(token)
        for cat in ("phrase_corrections", "extra_word_patterns"):
            data = vm.get_category(cat)
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        value = entry[1]
                        if isinstance(value, str):
                            for token in value.split():
                                token = token.strip().lower()
                                if token and token.isalpha():
                                    words.add(token)
    except Exception:
        log.debug("[VOCAB_AUTO] could not collect vocabulary words", exc_info=True)
    return words


# ─── Main class ─────────────────────────────────────────────────────────────


# Maximum Levenshtein distance we consider a "close match".  2 is
# conservative — it catches single-character typos and most
# phonetic confusions (recieve → receive, definately → definitely)
# without matching unrelated words.  We also require the lengths to
# be within 1 of each other to avoid silly matches.
_MAX_LEVENSHTEIN_DISTANCE = 2

# Minimum word length to consider for suggestion.  Words shorter
# than 3 characters are too short for meaningful Levenshtein matching
# (a 2-character word has at most a 2-edit distance to any other
# 2-character word).
_MIN_WORD_LENGTH = 3

# Maximum context snippet length (characters).  Truncated so the
# IPC payload stays small even for long transcriptions.
_MAX_CONTEXT_LENGTH = 80


class VocabularyAutomation:
    """Analyze transcriptions and suggest vocabulary corrections.

    The class is stateful: it maintains a queue of pending
    suggestions (``_pending``) that the user can review via the IPC
    handlers.  Suggestions are kept in insertion order; the
    ``timestamp`` field on each suggestion lets the UI sort by
    recency if desired.

    The class is NOT thread-safe — the dictation pipeline runs on a
    single background thread, and the IPC handlers run on the IPC
    thread.  We use a single ``threading.Lock`` around mutations to
    ``_pending`` so a user dismissing a suggestion while a new
    dictation is being analyzed doesn't corrupt the list.
    """

    def __init__(
        self,
        vocabulary_manager: VocabularyManager,
        config: Any,
    ) -> None:
        """Initialize the automation.

        Parameters
        ----------
        vocabulary_manager : VocabularyManager
            The existing vocabulary manager.  Suggestions are applied
            to this manager (so the user's existing vocabulary is
            extended, not replaced).  We hold a reference; we do NOT
            take ownership.
        config : Config
            The application config.  We read the
            ``vocabulary_auto_confidence_threshold`` and
            ``vocabulary_auto_apply_threshold`` fields.  We accept
            ``Any`` to avoid an import cycle with
            :mod:`voice_typer.server.config`.
        """
        self._vm = vocabulary_manager
        self._config = config
        self._pending: list[CorrectionSuggestion] = []
        # Late import to avoid a circular import at module load time
        # (this module is imported by dictation_pipeline, which is
        # imported during app startup before threading is fully set
        # up — but threading is always available, so the import is
        # actually fine; we keep it lazy for symmetry with other
        # late imports in this codebase).
        import threading
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────────

    def analyze_transcription(
        self,
        text: str,
        segments: list,
        confidence: float,
    ) -> list[CorrectionSuggestion]:
        """Analyze ``text`` for low-confidence / unknown words.

        Parameters
        ----------
        text : str
            The transcribed text.  Already cleaned up and enhanced
            by the time it reaches here.
        segments : list
            Segment-level metadata from the transcription engine.
            Each segment may be a dict with ``text`` / ``avg_logprob``
            keys, or an object with ``.text`` / ``.avg_logprob``
            attributes (faster-whisper's Segment namedtuple).  May
            be empty — in that case we treat the whole text as one
            segment with the given ``confidence``.
        confidence : float
            The overall transcription confidence, in [0.0, 1.0].
            Used as the per-word confidence when ``segments`` is
            empty or doesn't expose per-segment logprobs.

        Returns
        -------
        list[CorrectionSuggestion]
            The newly-created suggestions (also added to the
            internal pending queue).  Empty list if the master
            toggle is off or no suspicious words were found.
        """
        if not getattr(self._config, "vocabulary_automation_enabled", False):
            return []

        if not text or not text.strip():
            return []

        # Build per-word confidence map.  Each segment's avg_logprob
        # applies to all words in that segment.  When segments is
        # empty, we fall back to the overall confidence for every
        # word.
        word_confidences: dict[int, float] = {}  # word_index → confidence
        words = text.split()
        if not words:
            return []

        threshold = float(getattr(
            self._config, "vocabulary_auto_confidence_threshold", 0.7,
        ))

        if segments:
            # Map segment-level confidence onto words by accumulating
            # word counts.  We assume segments are in order and their
            # texts concatenate to roughly ``text`` (the cleanup
            # pipeline may have slightly modified spacing / case, so
            # we use a soft match: count words per segment and assign
            # them sequentially).
            word_idx = 0
            for seg in segments:
                seg_text = _get_segment_text(seg)
                seg_conf = _get_segment_confidence(seg, confidence)
                seg_word_count = len(seg_text.split()) if seg_text else 0
                for _ in range(seg_word_count):
                    if word_idx >= len(words):
                        break
                    word_confidences[word_idx] = seg_conf
                    word_idx += 1
            # Any remaining words (segment/word count mismatch) get
            # the overall confidence.
            for i in range(word_idx, len(words)):
                word_confidences[i] = confidence
        else:
            for i in range(len(words)):
                word_confidences[i] = confidence

        # Collect the user's known-good vocabulary words for
        # Levenshtein matching.  We do this once per call rather
        # than caching because the vocabulary can change between
        # dictations (the user may have applied a previous
        # suggestion).
        vocab_words = _collect_vocabulary_words(self._vm)

        # Build a regex for sentence splitting (reuse text_cleanup's
        # approach: split on . ! ? followed by whitespace).
        sentence_split = re.compile(r"(?<=[.!?])\s+")
        sentences = sentence_split.split(text)

        # Map each word index to its containing sentence (for the
        # context field).  We do this by walking sentences and
        # counting their words.
        word_to_sentence: dict[int, str] = {}
        idx = 0
        for sentence in sentences:
            sentence_word_count = len(sentence.split())
            for _ in range(sentence_word_count):
                if idx >= len(words):
                    break
                word_to_sentence[idx] = sentence
                idx += 1
        # Fallback for any remaining words.
        for i in range(idx, len(words)):
            word_to_sentence[i] = text

        suggestions: list[CorrectionSuggestion] = []
        now = time.time()

        for i, word in enumerate(words):
            # Strip punctuation for matching but keep the original
            # for display.
            clean = re.sub(r"^\W+|\W+$", "", word).lower()
            if len(clean) < _MIN_WORD_LENGTH:
                continue
            if not clean.isalpha():
                continue

            word_conf = word_confidences.get(i, confidence)

            # Signal 1: low confidence.
            if word_conf < threshold:
                # Try to find a close vocabulary match as the
                # proposed correction.
                corrected = _find_closest_vocabulary_match(
                    clean, vocab_words, _MAX_LEVENSHTEIN_DISTANCE,
                )
                if corrected is None:
                    # No close match — the user will need to supply
                    # the correction themselves.  We still queue the
                    # suggestion so they know the word was flagged.
                    corrected = clean

                context = word_to_sentence.get(i, text)
                if len(context) > _MAX_CONTEXT_LENGTH:
                    context = context[:_MAX_CONTEXT_LENGTH].rstrip() + "…"

                suggestion = CorrectionSuggestion(
                    original=clean,
                    corrected=corrected,
                    confidence=word_conf,
                    context=context,
                    timestamp=now,
                )
                suggestions.append(suggestion)

            # Signal 2: word not in vocabulary but close to a
            # vocabulary entry.  This catches high-confidence but
            # "wrong" words (the model was sure, but it was wrong).
            # We only flag this when the word is NOT already a
            # vocabulary word (otherwise we'd re-suggest corrections
            # the user has already made).
            elif clean not in vocab_words and vocab_words:
                corrected = _find_closest_vocabulary_match(
                    clean, vocab_words, _MAX_LEVENSHTEIN_DISTANCE,
                )
                if corrected is not None and corrected != clean:
                    context = word_to_sentence.get(i, text)
                    if len(context) > _MAX_CONTEXT_LENGTH:
                        context = context[:_MAX_CONTEXT_LENGTH].rstrip() + "…"

                    suggestion = CorrectionSuggestion(
                        original=clean,
                        corrected=corrected,
                        confidence=word_conf,
                        context=context,
                        timestamp=now,
                    )
                    suggestions.append(suggestion)

        # Add the new suggestions to the pending queue.
        with self._lock:
            self._pending.extend(suggestions)

        if suggestions:
            log.info(
                "[VOCAB_AUTO] Analyzed %d words, found %d suggestion(s)",
                len(words), len(suggestions),
            )

        return suggestions

    def apply_suggestion(self, suggestion: CorrectionSuggestion) -> None:
        """Apply a suggestion to the vocabulary.

        Adds ``original → corrected`` to the ``misspellings``
        category of the underlying VocabularyManager.  The
        suggestion is marked as ``applied`` and removed from the
        pending queue.

        Parameters
        ----------
        suggestion : CorrectionSuggestion
            The suggestion to apply.  Must be a suggestion returned
            by :meth:`analyze_transcription` (or constructed with
            matching fields).
        """
        if suggestion.applied or suggestion.dismissed:
            return
        try:
            # Add to misspellings — that's the most appropriate
            # category for "the ASR said X, the correct word is Y".
            self._vm.add_entry(
                "misspellings", suggestion.original, suggestion.corrected,
            )
        except Exception:
            log.warning(
                "[VOCAB_AUTO] Failed to apply suggestion %r -> %r",
                suggestion.original, suggestion.corrected, exc_info=True,
            )
            return
        suggestion.applied = True
        with self._lock:
            self._pending = [
                s for s in self._pending
                if not (s is suggestion or s.applied or s.dismissed)
            ]

    def get_pending_suggestions(self) -> list[CorrectionSuggestion]:
        """Return the suggestions not yet applied or dismissed.

        Returns a copy of the internal list so the caller can iterate
        without holding the lock.
        """
        with self._lock:
            return [s for s in self._pending if not s.applied and not s.dismissed]

    def dismiss_suggestion(self, suggestion: CorrectionSuggestion) -> None:
        """Mark a suggestion as dismissed (user rejected it).

        The suggestion is removed from the pending queue.  It is NOT
        added to the vocabulary.

        Parameters
        ----------
        suggestion : CorrectionSuggestion
            The suggestion to dismiss.
        """
        suggestion.dismissed = True
        with self._lock:
            self._pending = [
                s for s in self._pending
                if not (s is suggestion or s.applied or s.dismissed)
            ]

    def auto_apply_high_confidence_suggestions(
        self,
        threshold: float = 0.95,
    ) -> int:
        """Auto-apply suggestions whose confidence is at or above ``threshold``.

        "High confidence" here means the *suggestion* confidence
        (i.e. the per-word transcription confidence that triggered
        the suggestion).  When the model is very confident in a
        word, but the word is also a close Levenshtein match to a
        vocabulary entry, we trust the vocabulary and auto-apply.

        This sounds backwards (high confidence → auto-correct?) but
        it's correct: the suggestion was only created because the
        word was NOT in the vocabulary in the first place.  A high-
        confidence word that's one edit away from a vocabulary
        entry is almost certainly a typo.

        Parameters
        ----------
        threshold : float
            Minimum confidence to auto-apply.  Defaults to 0.95.

        Returns
        -------
        int
            Number of suggestions auto-applied.
        """
        applied_count = 0
        # Snapshot under the lock to avoid mutating while iterating.
        with self._lock:
            pending_snapshot = list(self._pending)

        for suggestion in pending_snapshot:
            if suggestion.applied or suggestion.dismissed:
                continue
            if suggestion.confidence >= threshold and suggestion.corrected != suggestion.original:
                # Only auto-apply if we actually have a proposed
                # correction different from the original.  Suggestions
                # where ``corrected == original`` (no Levenshtein
                # match found) require user input.
                    self.apply_suggestion(suggestion)
                    applied_count += 1

        if applied_count > 0:
            log.info(
                "[VOCAB_AUTO] Auto-applied %d suggestion(s) at threshold %.2f",
                applied_count, threshold,
            )
        return applied_count


# ─── Helpers ────────────────────────────────────────────────────────────────


def _get_segment_text(seg: Any) -> str:
    """Extract the text from a segment, handling both dict and namedtuple."""
    if isinstance(seg, dict):
        text = seg.get("text", "")
        return text if isinstance(text, str) else ""
    return str(getattr(seg, "text", "") or "")


def _get_segment_confidence(seg: Any, fallback: float) -> float:
    """Extract a per-segment confidence in [0, 1].

    Handles three forms:
    * A ``confidence`` field already in [0, 1] (Parakeet-style).
    * An ``avg_logprob`` field in roughly [-1, 0] (Whisper-style);
      we transform with ``exp(avg_logprob)``.
    * Neither — return ``fallback``.
    """
    if isinstance(seg, dict):
        if "confidence" in seg:
            c = seg["confidence"]
            if isinstance(c, (int, float)) and 0.0 <= float(c) <= 1.0:
                return float(c)
        if "avg_logprob" in seg:
            lp = seg["avg_logprob"]
            if isinstance(lp, (int, float)):
                return _logprob_to_confidence(float(lp))
    else:
        c = getattr(seg, "confidence", None)
        if isinstance(c, (int, float)) and 0.0 <= float(c) <= 1.0:
            return float(c)
        lp = getattr(seg, "avg_logprob", None)
        if isinstance(lp, (int, float)):
            return _logprob_to_confidence(float(lp))
    return fallback


def _logprob_to_confidence(logprob: float) -> float:
    """Convert a log-probability to a [0, 1] confidence via exp().

    Whisper's ``avg_logprob`` is typically in [-1.0, 0.0].  ``exp``
    of that range gives [0.37, 1.0].  We clamp to [0, 1] for safety.
    """
    import math
    try:
        c = math.exp(logprob)
    except (OverflowError, ValueError):
        return 0.0
    return max(0.0, min(1.0, c))


def _find_closest_vocabulary_match(
    word: str,
    vocab_words: Iterable[str],
    max_distance: int,
) -> str | None:
    """Find the closest vocabulary word within ``max_distance`` edits.

    Iterates over ``vocab_words`` and returns the one with the
    smallest Levenshtein distance to ``word``, provided that distance
    is ``<= max_distance``.  Ties are broken by vocabulary iteration
    order (first match wins).  Returns ``None`` if no word is within
    the bound.

    The function uses bounded Levenshtein (``max_distance`` is passed
    through to :func:`_levenshtein`) so irrelevant comparisons
    short-circuit quickly.
    """
    if not word or not vocab_words:
        return None

    best_distance = max_distance + 1
    best_match: str | None = None

    for candidate in vocab_words:
        # Quick length sanity check before the expensive DP.
        if abs(len(candidate) - len(word)) > max_distance:
            continue
        d = _levenshtein(word, candidate, max_distance=max_distance)
        if d < best_distance:
            best_distance = d
            best_match = candidate
            if d == 0:
                # Exact match — can't do better.
                break

    return best_match if best_distance <= max_distance else None


__all__ = [
    "CorrectionSuggestion",
    "VocabularyAutomation",
]
