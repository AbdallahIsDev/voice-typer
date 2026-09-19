"""Vocabulary automation helpers."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# Token-key normalizer shared with text_cleanup (memoized
from voice_typer.server.text_cleanup import _token_key

if TYPE_CHECKING:  # pragma: no cover, import only for type checkers
    from voice_typer.server.vocabulary import VocabularyManager

log = logging.getLogger(__name__)


@dataclass
class CorrectionSuggestion:
    """A single vocabulary correction suggestion."""

    original: str
    corrected: str
    confidence: float
    context: str
    timestamp: float
    applied: bool = False
    dismissed: bool = False

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict for IPC transport."""
        return {
            "original": self.original,
            "corrected": self.corrected,
            "confidence": self.confidence,
            "context": self.context,
            "timestamp": self.timestamp,
        }


def _levenshtein(a: str, b: str, *, max_distance: int | None = None) -> int:
    """Compute the Levenshtein edit distance between ``a`` and ``b``."""
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
        # Length difference alone exceeds the bound, can't match.
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
            substitute_cost = previous_row[i - 1] + (0 if a[i - 1] == b_char else 1)
            current_row[i] = min(insert_cost, delete_cost, substitute_cost)
            if current_row[i] < row_min:
                row_min = current_row[i]
        # Bounded-Levenshtein early exit: if every entry in this row
        if max_distance is not None and row_min > max_distance:
            return max_distance + 1
        previous_row = current_row

    return previous_row[m]


def _collect_vocabulary_words(vm: VocabularyManager) -> set[str]:
    """Return the set of "correct" words from the vocabulary manager."""
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
                    if isinstance(entry, list | tuple) and len(entry) >= 2:
                        value = entry[1]
                        if isinstance(value, str):
                            for token in value.split():
                                token = token.strip().lower()
                                if token and token.isalpha():
                                    words.add(token)
    except Exception:
        log.debug("[VOCAB_AUTO] could not collect vocabulary words", exc_info=True)
    return words


# Maximum Levenshtein distance we consider a "close match".  2 is
_MAX_LEVENSHTEIN_DISTANCE = 2

# Sentence-split pattern for the suggestion context field: split
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Minimum word length to consider for suggestion.  Words shorter
_MIN_WORD_LENGTH = 3

# Maximum context snippet length (characters).  Truncated so the
_MAX_CONTEXT_LENGTH = 80

# cap on the number of pending (not-yet-applied / not-yet-
MAX_PENDING = 200


class VocabularyAutomation:
    """Analyze transcriptions and suggest vocabulary corrections."""

    def __init__(
        self,
        vocabulary_manager: VocabularyManager,
        config: Any,
    ) -> None:
        """Initialize the automation."""
        self._vm = vocabulary_manager
        self._config = config
        self._pending: list[CorrectionSuggestion] = []
        # Late import to avoid a circular import at module load time
        import threading

        self._lock = threading.Lock()

    def analyze_transcription(
        self,
        text: str,
        segments: list | tuple,
        confidence: float,
    ) -> list[CorrectionSuggestion]:
        """Analyze ``text`` for low-confidence / unknown words."""
        if not getattr(self._config, "vocabulary_automation_enabled", False):
            return []

        if not text or not text.strip():
            return []

        # Build per-word confidence map.  Each segment's avg_logprob
        word_confidences: dict[int, float] = {}  # word_index → confidence
        words = text.split()
        if not words:
            return []

        threshold = float(
            getattr(
                self._config,
                "vocabulary_auto_confidence_threshold",
                0.7,
            )
        )

        if segments:
            # Map segment-level confidence onto words by accumulating
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
            for i in range(word_idx, len(words)):
                word_confidences[i] = confidence
        else:
            for i in range(len(words)):
                word_confidences[i] = confidence

        # Collect the user's known-good vocabulary words for
        vocab_words = _collect_vocabulary_words(self._vm)

        # Split into sentences (``. ! ?`` followed by whitespace —
        sentences = _SENTENCE_SPLIT_RE.split(text)

        # Map each word index to its containing sentence (for the
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
            clean = _token_key(word)
            if len(clean) < _MIN_WORD_LENGTH:
                continue
            if not clean.isalpha():
                continue

            word_conf = word_confidences.get(i, confidence)

            # Signal 1: low confidence.
            if word_conf < threshold:
                # Try to find a close vocabulary match as the
                corrected = _find_closest_vocabulary_match(
                    clean,
                    vocab_words,
                    _MAX_LEVENSHTEIN_DISTANCE,
                )
                if corrected is None:
                    # No close match, the user will need to supply
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
            elif clean not in vocab_words and vocab_words:
                corrected = _find_closest_vocabulary_match(
                    clean,
                    vocab_words,
                    _MAX_LEVENSHTEIN_DISTANCE,
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
            # enforce the MAX_PENDING cap.  Items only get
            if len(self._pending) > MAX_PENDING:
                overflow = len(self._pending) - MAX_PENDING
                log.info(
                    "[VOCAB_AUTO] pending queue exceeded MAX_PENDING=%d; dropping %d oldest suggestions",
                    MAX_PENDING,
                    overflow,
                )
                self._pending = self._pending[-MAX_PENDING:]

        if suggestions:
            log.info(
                "[VOCAB_AUTO] Analyzed %d words, found %d suggestions",
                len(words),
                len(suggestions),
            )

        return suggestions

    def apply_suggestion(self, suggestion: CorrectionSuggestion) -> None:
        """Apply a suggestion to the vocabulary."""
        with self._lock:
            self._apply_suggestion_locked(suggestion)

    def _apply_suggestion_locked(self, suggestion: CorrectionSuggestion) -> bool:
        """Apply a suggestion assuming ``self._lock`` is held by the caller.

        Returns ``True`` if the suggestion was newly applied (the
        """
        if suggestion.applied or suggestion.dismissed:
            return False
        try:
            # Add to misspellings, that's the most appropriate
            self._vm.add_entry(
                "misspellings",
                suggestion.original,
                suggestion.corrected,
            )
        except Exception:
            log.warning(
                "[VOCAB_AUTO] Failed to apply suggestion %r -> %r",
                suggestion.original,
                suggestion.corrected,
                exc_info=True,
            )
            return False
        suggestion.applied = True
        self._pending = [s for s in self._pending if not (s is suggestion or s.applied or s.dismissed)]
        return True

    def get_pending_suggestions(self) -> list[CorrectionSuggestion]:
        """Return the suggestions not yet applied or dismissed.

        Returns a copy of the internal list so the caller can iterate
        """
        with self._lock:
            return [s for s in self._pending if not s.applied and not s.dismissed]

    def dismiss_suggestion(self, suggestion: CorrectionSuggestion) -> None:
        """Mark a suggestion as dismissed (user rejected it)."""
        with self._lock:
            self._dismiss_suggestion_locked(suggestion)

    def _dismiss_suggestion_locked(self, suggestion: CorrectionSuggestion) -> bool:
        """Dismiss a suggestion assuming ``self._lock`` is held by the caller.

        Returns ``True`` if the suggestion was newly dismissed,
        """
        if suggestion.applied or suggestion.dismissed:
            return False
        suggestion.dismissed = True
        self._pending = [s for s in self._pending if not (s is suggestion or s.applied or s.dismissed)]
        return True

    def auto_apply_high_confidence_suggestions(
        self,
        threshold: float = 0.95,
    ) -> int:
        """Auto-apply suggestions whose confidence is at or above ``threshold``."""
        applied_count = 0
        with self._lock:
            # Iterate over a snapshot of ``_pending`` because
            pending_snapshot = list(self._pending)
            for suggestion in pending_snapshot:
                if suggestion.applied or suggestion.dismissed:
                    continue
                if (
                    suggestion.confidence >= threshold
                    and suggestion.corrected != suggestion.original
                    and self._apply_suggestion_locked(suggestion)
                ):
                    # Only auto-apply if we actually have a proposed
                    applied_count += 1

        if applied_count > 0:
            log.info(
                "[VOCAB_AUTO] Auto-applied %d suggestions at threshold %.2f",
                applied_count,
                threshold,
            )
        return applied_count


def _get_segment_text(seg: Any) -> str:
    """Extract the text from a segment, handling both dict and namedtuple."""
    if isinstance(seg, dict):
        text = seg.get("text", "")
        return text if isinstance(text, str) else ""
    return str(getattr(seg, "text", "") or "")


def _get_segment_confidence(seg: Any, fallback: float) -> float:
    """Extract a per-segment confidence in [0, 1]."""
    if isinstance(seg, dict):
        if "confidence" in seg:
            c = seg["confidence"]
            if isinstance(c, int | float) and 0.0 <= float(c) <= 1.0:
                return float(c)
        if "avg_logprob" in seg:
            lp = seg["avg_logprob"]
            if isinstance(lp, int | float):
                return _logprob_to_confidence(float(lp))
    else:
        c = getattr(seg, "confidence", None)
        if isinstance(c, int | float) and 0.0 <= float(c) <= 1.0:
            return float(c)
        lp = getattr(seg, "avg_logprob", None)
        if isinstance(lp, int | float):
            return _logprob_to_confidence(float(lp))
    return fallback


def _logprob_to_confidence(logprob: float) -> float:
    """Convert a log-probability to a [0, 1] confidence via exp()."""
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
    """Find the closest vocabulary word within ``max_distance`` edits."""
    if not word or not vocab_words:
        return None

    word_len = len(word)

    # Build a fresh {length: [word, ...]} index on every call, the
    buckets: dict[int, list[str]] = {}
    for candidate in vocab_words:
        if not isinstance(candidate, str):
            continue
        buckets.setdefault(len(candidate), []).append(candidate)
    if not buckets:
        return None

    best_distance = max_distance + 1
    best_match: str | None = None

    # Iterate length buckets in [word_len - max_distance, word_len + max_distance].
    for length in range(max(1, word_len - max_distance), word_len + max_distance + 1):
        candidates = buckets.get(length)
        if not candidates:
            continue
        for candidate in candidates:
            d = _levenshtein(word, candidate, max_distance=max_distance)
            if d < best_distance:
                best_distance = d
                best_match = candidate
                if d == 0:
                    # Exact match, can't do better.
                    return best_match

    return best_match if best_distance <= max_distance else None


__all__ = [
    "CorrectionSuggestion",
    "MAX_PENDING",
    "VocabularyAutomation",
]
