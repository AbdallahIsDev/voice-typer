"""Core helpers for hidden streaming transcription."""

from __future__ import annotations

import collections
import contextlib
import logging
import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")

log = logging.getLogger(__name__)


def _is_view_of_live_recorder_audio(recorder: Any, arr: Any) -> bool:
    """XZ-PRIV-02 provenance check: is ``arr`` a VIEW over audio the live
    recorder still owns?

    The recorder hands out zero-copy snapshot views over TWO backing
    stores, and a destructive ``fill(0)`` on such a view would corrupt the
    recording mid-session (silent transcription windows):

    1. ``recorder._cached_resampled`` — the incremental resampled-stream
       cache (snapshot resample path);
    2. ``recorder._buffer.storage`` — the contiguous raw recording buffer
       itself (the common no-resample path; the buffer object may also be
       a plain deque/list in tests and post-hot-swap windows, hence the
       defensive getattr chain).

    A fresh/owning array (``.base is None``) or any other array is NOT
    recorder-owned and MUST be zeroed after use. Mock recorders in tests
    return auto-attributes that never compare identity-equal, so they keep
    taking the unconditional-zero path.
    """
    base = getattr(arr, "base", None)
    if base is None:
        return False
    cached = getattr(recorder, "_cached_resampled", None)
    if cached is not None and base is cached:
        return True
    buf = getattr(recorder, "_buffer", None)
    storage = getattr(buf, "storage", None) if buf is not None else None
    return storage is not None and base is storage


@dataclass(frozen=True)
class StreamingConfig:
    """Timing and safety settings for streaming transcription."""

    enabled: bool = False
    chunk_seconds: float = 12.0
    step_seconds: float = 5.0
    left_overlap_seconds: float = 3.0
    right_guard_seconds: float = 1.5
    min_first_chunk_seconds: float = 6.0
    silence_threshold: float = 0.003


@dataclass(frozen=True)
class WordTiming:
    """One timestamped word in global recording time."""

    word: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, eq=False)
class AudioWindow:
    """A slice of 16 kHz mono audio and its global time bounds.

    PERF-EQ: ``eq=False`` is set so the dataclass-generated __eq__
    doesn't fire. The custom __eq__ uses a lightweight identity/
    scalar comparison instead of np.array_equal (which is O(n) in
    the audio length). For test utilities that need full array
    comparison, use ``np.array_equal(a.audio, b.audio)`` directly.
    """

    audio: np.ndarray
    start_seconds: float
    end_seconds: float

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AudioWindow):
            return NotImplemented
        # PERF-EQ: compare scalar fields first (O(1)); only compare
        # array identity (is), not contents. Full array comparison
        # should be done explicitly in tests with np.array_equal().
        if self.start_seconds != other.start_seconds or self.end_seconds != other.end_seconds:
            return False
        # Same object or same underlying buffer → equal
        if self.audio is other.audio:
            return True
        # Different objects with same scalars — compare shapes then hash
        # for a fast rejection. Full content comparison is O(n) and
        # should only be done in test utilities, not production code.
        if self.audio.shape != other.audio.shape:
            return False
        return bool(np.array_equal(self.audio, other.audio))

    def __hash__(self) -> int:
        # hash on the scalar fields; audio is unhashable but
        # callers only need equality, not set/dict membership.
        return hash((self.start_seconds, self.end_seconds))


@dataclass
class AudioWindowPlanner:
    """Plan overlapping audio windows as recording audio grows."""

    config: StreamingConfig = field(default_factory=StreamingConfig)
    _last_window_end_seconds: float | None = None

    def next_window(self, audio: np.ndarray, sample_rate: int) -> AudioWindow | None:
        duration_seconds = len(audio) / sample_rate
        if self._last_window_end_seconds is None:
            if duration_seconds < self.config.min_first_chunk_seconds:
                return None
            requested_start_seconds = 0.0
            requested_end_seconds = min(duration_seconds, self.config.chunk_seconds)
        else:
            requested_end_seconds = self._last_window_end_seconds + self.config.step_seconds
            if duration_seconds < requested_end_seconds:
                return None
            requested_end_seconds = min(duration_seconds, requested_end_seconds)
            requested_start_seconds = max(
                0.0,
                self._last_window_end_seconds - self.config.left_overlap_seconds,
            )

        end_seconds = self._choose_boundary(
            audio=audio,
            sample_rate=sample_rate,
            requested_start_seconds=requested_start_seconds,
            requested_end_seconds=requested_end_seconds,
        )
        start_sample = int(round(requested_start_seconds * sample_rate))
        end_sample = int(round(end_seconds * sample_rate))
        # PERF- the audio parameter comes from
        # ``Recorder.snapshot()`` which always returns a fresh array
        # (either ``np.concatenate(...)`` or ``self._cached_resampled.copy()``).
        # A slice into a fresh array is a view that does not share memory
        # with the recorder's internal buffer, so the explicit ``.copy()``
        # that was here before was redundant — it copied 768 KB (1.5 s of
        # float32 audio) on every snapshot call (16 Hz → 12 MB/s of
        # allocation pressure). The view is safe because (a) the snapshot
        # array is not shared with any other consumer, (b) faster-whisper
        # does not modify its input, and (c) AudioWindow is frozen so
        # callers cannot reassign ``.audio``.
        window = AudioWindow(
            audio=audio[start_sample:end_sample],
            start_seconds=requested_start_seconds,
            end_seconds=end_seconds,
        )
        self._last_window_end_seconds = end_seconds
        return window

    def _choose_boundary(
        self,
        audio: np.ndarray,
        sample_rate: int,
        requested_start_seconds: float,
        requested_end_seconds: float,
    ) -> float:
        """Find the best boundary point between audio windows.

        previously returned the CENTER of the quietest frame
        (best_index = index + len(frame) // 2), which is offset by half
        a frame from where the next voice should start. Now returns the
        END of the quietest frame (best_index = index + len(frame)),
        which is the start of the next voice segment.
        """
        search_seconds = min(1.0, requested_end_seconds - requested_start_seconds)
        if search_seconds <= 0:
            return requested_end_seconds

        search_start = int(round((requested_end_seconds - search_seconds) * sample_rate))
        search_end = int(round(requested_end_seconds * sample_rate))
        search = audio[search_start:search_end]
        if len(search) == 0:
            return requested_end_seconds

        frame_size = max(1, int(0.05 * sample_rate))
        best_rms = float("inf")
        best_index = None
        for index in range(0, len(search), frame_size):
            frame = search[index : index + frame_size]
            if len(frame) == 0:
                continue
            rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
            if rms < best_rms:
                best_rms = rms
                # use end of the quietest frame (index +
                # len(frame)) as the boundary, not the center. This
                # marks where the next voice segment should begin.
                best_index = index + len(frame)

        if best_index is None or best_rms > self.config.silence_threshold:
            return requested_end_seconds
        return (search_start + best_index) / sample_rate


@dataclass
class StreamingTextAssembler:
    """Commit timestamped words only after they are outside the unsafe tail."""

    # cap _words to prevent unbounded growth. Pre-fix this
    # used a plain ``list`` with ``pop(0)`` eviction (O(n) per eviction
    # — every eviction shifted up to 9999 pointers). Now we use a
    # ``collections.deque(maxlen=_MAX_WORDS)`` for O(1) eviction plus
    # a ``_base_offset`` counter so the external ``_word_key_index``
    # stores ABSOLUTE indices that don't shift on eviction.
    _MAX_WORDS = 10000
    # per-key bounded deque maxlen for _word_key_index. 8 entries
    # cover ~2-8s of history per token (words arrive every 0.3-1s), well
    # beyond the 0.25s near-duplicate window checked in
    # ``_has_near_duplicate_unlocked``.
    _WORD_KEY_INDEX_MAXLEN = 8
    _words: collections.deque[WordTiming] = field(
        default_factory=lambda: collections.deque(maxlen=StreamingTextAssembler._MAX_WORDS)
    )
    # number of items evicted from the front of ``_words``.
    # External indices stored in ``_word_key_index`` are absolute
    # (= base_offset + deque_index); we convert to deque index at
    # access time via ``abs_idx - _base_offset``. This makes eviction
    # O(1) — no need to shift every stored index by 1.
    _base_offset: int = 0
    # hard cap on the dedup set — a 30-min session typically
    # produces 5-10k timestamps so 50k entries is a generous upper
    # bound that still keeps memory bounded for runaway sessions.
    _MAX_SEEN_TIMESTAMPS = 50000
    _seen_timestamps: set[tuple[float, float]] = field(default_factory=set)
    _word_key_index: dict[str, collections.deque[int]] = field(default_factory=dict)
    last_committed_time: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock)
    # cache the sorted committed_text and invalidate on mutation
    _committed_text_cache: str | None = field(default=None)
    _words_dirty: bool = field(default=True)

    @property
    def committed_text(self) -> str:
        with self._lock:
            # return cached result if no mutations since last read
            if not self._words_dirty and self._committed_text_cache is not None:
                return self._committed_text_cache
            # PERF- sort at read time since we deferred sorting
            # in _insert_word_unlocked.  Words are approximately in
            # order from streaming, so this is a near-sorted sort (fast).
            # deque has no .sort(); convert to list first.
            words_list = list(self._words)
            words_list.sort(key=lambda w: (w.start_seconds, w.end_seconds))
            self._committed_text_cache = " ".join(word.word for word in words_list)
            self._words_dirty = False
            return self._committed_text_cache

    def add_window(
        self,
        window: AudioWindow,
        words: Iterable[WordTiming],
        right_guard_seconds: float,
    ) -> str:
        return self.add_words(
            words,
            commit_horizon_seconds=window.end_seconds - right_guard_seconds,
        )

    def add_words(
        self,
        words: Iterable[WordTiming],
        commit_horizon_seconds: float,
    ) -> str:
        # Collect words to add into a local list outside the
        # lock, then acquire lock briefly to extend the shared data
        # structures, rather than holding the lock for the entire loop.
        # This reduces contention when streaming chunks arrive while a
        # finalize() or get_transcript() call is in progress.
        #
        # Design decision: the lock is still held for the full insertion
        # loop in _add_words_unlocked(), which is O(k) for k words.
        # This is acceptable because:
        #   (a) k is typically small (5-20 words per streaming chunk),
        #       so the lock hold time is microseconds, not seconds;
        #   (b) the per-word work inside the lock is cheap (dict lookup,
        #       list append, set add) — no I/O or GPU calls;
        #   (c) the alternative (fine-grained per-word locking) would add
        #       complexity and risk deadlocks for negligible gain.
        # If streaming chunk sizes grow significantly (hundreds of words),
        # consider batching inserts and releasing the lock between batches.
        candidates = []
        for word in words:
            if word.end_seconds > commit_horizon_seconds:
                continue
            text = word.word.strip()
            if not text:
                continue
            candidates.append(word)

        with self._lock:
            return self._add_words_unlocked(candidates, commit_horizon_seconds)

    def _add_words_unlocked(
        self,
        words: Iterable[WordTiming],
        commit_horizon_seconds: float,
    ) -> str:
        # hard-cap on the dedup set BEFORE the loop. The
        # ``_words`` deque has ``maxlen=10000`` but
        # ``_seen_timestamps`` is a plain ``set`` — a 30-min
        # session with periodic re-emits of the same timestamps
        # could otherwise grow the dedup set unbounded between
        # calls. Reset to a fresh set when the cap is exceeded
        # so the loop starts with a clean slate. 50k entries
        # is a generous upper bound (well above the typical 5-50
        # unique timestamps per finalize) so the worst case is a
        # one-time ~few-MB allocation, not unbounded growth.
        if len(self._seen_timestamps) > self._MAX_SEEN_TIMESTAMPS:
            self._seen_timestamps = set()
        committed: list[str] = []
        for word in words:
            if word.end_seconds > commit_horizon_seconds:
                continue
            timestamp_key = (
                round(word.start_seconds, 3),
                round(word.end_seconds, 3),
            )
            if timestamp_key in self._seen_timestamps:
                continue

            text = word.word.strip()
            if not text:
                continue
            candidate = WordTiming(
                text,
                start_seconds=word.start_seconds,
                end_seconds=word.end_seconds,
            )
            if self._has_near_duplicate_unlocked(candidate):
                self._seen_timestamps.add(timestamp_key)
                continue
            self._seen_timestamps.add(timestamp_key)
            self._insert_word_unlocked(candidate)
            committed.append(text)
            self.last_committed_time = max(
                self.last_committed_time,
                word.end_seconds,
            )
            # invalidate cached text on mutation
            self._words_dirty = True
        # H8: Prune committed words that are well before the commit horizon
        # Only prune when commit_horizon is finite (not inf from finalize)
        if math.isfinite(commit_horizon_seconds):
            prune_threshold = commit_horizon_seconds - 5.0
            if prune_threshold > 0:
                self._prune_old_entries(prune_threshold)
        # when ``commit_horizon_seconds == math.inf`` (the
        # ``finalize()`` path), ``_prune_old_entries`` short-circuits
        # because its threshold would be ``math.inf - 5.0``. The
        # DJ-21 hard cap is now applied BEFORE the for loop
        # (above) so it covers both the per-chunk and
        # finalize() paths uniformly.
        return " ".join(committed)

    def _prune_old_entries(self, threshold: float) -> None:
        """Prune dedup structures for old entries; never remove from _words.

        _words is the output accumulator and must keep all committed entries.
        Only _seen_timestamps and _word_key_index are pruned to limit memory.

        previously rebuilt ``_word_key_index`` from scratch
        on every prune. With a 5-min session and 200+ words, this was
        O(n) every few seconds. We now remove only the indices that
        pointed to evicted timestamps — but since _words is never
        pruned, the indices stay valid; we only need to drop stale
        entries from the timestamp set. The word_key_index is left
        alone (it doesn't grow unboundedly because it's keyed on
        distinct words, not timestamps).
        """
        # Prune old timestamps from dedup set
        new_timestamps: set[tuple[float, float]] = set()
        for ts in self._seen_timestamps:
            if ts[1] >= threshold:
                new_timestamps.add(ts)
        if len(new_timestamps) == len(self._seen_timestamps):
            return
        self._seen_timestamps = new_timestamps
        # do NOT rebuild _word_key_index — it's keyed on
        # distinct words and indexed by _words position, which never
        # gets pruned. The previous rebuild was O(n) per prune with
        # no benefit.

    def _insert_word_unlocked(self, word: WordTiming):
        """Insert a word, maintaining sorted order.

        PERF- previously this did a linear scan + list.insert
        (O(n) per insert, O(n^2) per session) and then shifted all
        index entries.  Now we just append and defer sorting to
        commit time — the words are already approximately in order
        (streaming chunks arrive sequentially), so a full sort at
        commit is O(n log n) vs the O(n^2) insert pattern.

        enforce maxlen on _words. When the list exceeds
        _MAX_WORDS, evict the oldest entry and log a warning.
        """
        # detect imminent eviction BEFORE appending so we
        # can log which word is being evicted and adjust indices.
        if self._words.maxlen is not None and len(self._words) >= self._words.maxlen:
            # Peek the leftmost item; deque.append will evict it.
            evicted_word = self._words[0]
            evicted_absolute_idx = self._base_offset  # current offset → 0 in deque
            #  do NOT log evicted_word.word at any level —
            # that leaks user speech content into persistent log files
            # (the WARNING log is shown by default; the DEBUG log fires
            # whenever a support workflow bumps the root logger to DEBUG,
            # which is common for support tickets). Log only the
            # structural fact (max + index) at WARNING, plus a PII-safe
            # char-count metric at DEBUG so developers can still
            # diagnose eviction storms without seeing the user's speech.
            log.warning(
                "[STREAMING] Word list exceeded %d entries; evicted oldest (idx=%d)",
                self._MAX_WORDS,
                evicted_absolute_idx,
            )
            log.debug(
                "[STREAMING] Evicted word (%d chars) (debug only)",
                len(evicted_word.word),
            )
            # Bump base offset so all future absolute-index → deque-index
            # conversions account for the eviction.
            self._base_offset += 1
            # Drop the index entry pointing at the evicted word. Other
            # indices stay valid (they're absolute, not relative).
            # _word_key_index values are bounded deques (maxlen=
            # _WORD_KEY_INDEX_MAXLEN) so a single key can never accumulate
            # more than a handful of recent indices. Iteration + filter
            # is therefore bounded per-key by the deque maxlen — the
            # per-eviction cost is O(MAXLEN × distinct_keys) instead of
            # O(session_word_count × distinct_keys).
            for key, indices in list(self._word_key_index.items()):
                if evicted_absolute_idx in indices:
                    new_indices = collections.deque(
                        (i for i in indices if i != evicted_absolute_idx),
                        maxlen=self._WORD_KEY_INDEX_MAXLEN,
                    )
                    if new_indices:
                        self._word_key_index[key] = new_indices
                    else:
                        del self._word_key_index[key]
            # also drop the evicted word's (start, end) timestamp
            # from ``_seen_timestamps`` so the dedup set is bounded by
            # the deque maxlen. Pre-fix this was only cleaned up by
            # ``_prune_old_entries``, which short-circuits on
            # ``commit_horizon_seconds == math.inf`` (the ``finalize()``
            # path) — so under inf commit-horizon the set grew linearly
            # with the number of unique timestamps added, even though
            # ``_words`` was correctly bounded. Use the same 3-decimal
            # rounding as ``_add_words_unlocked`` so the key matches.
            evicted_ts_key = (
                round(evicted_word.start_seconds, 3),
                round(evicted_word.end_seconds, 3),
            )
            self._seen_timestamps.discard(evicted_ts_key)

        key = _word_key(word.word)
        # Absolute index = base_offset + current deque length (before append).
        absolute_idx = self._base_offset + len(self._words)
        self._words.append(word)
        if key:
            # use a bounded deque (maxlen=_WORD_KEY_INDEX_MAXLEN)
            # instead of an unbounded list. Near-duplicate detection
            # (_has_near_duplicate_unlocked) only needs the last few
            # occurrences within 0.25s — words arrive every 0.3-1s, so
            # 8 entries cover ~2-8s of history per token, well beyond
            # the 0.25s near-duplicate window. The previous unbounded
            # list retained one int per committed word for the entire
            # session lifetime (e.g. 10000 ints per recurring token in
            # a 10000-word session), so this change caps per-key
            # memory at O(MAXLEN) regardless of session length.
            existing = self._word_key_index.get(key)
            if existing is None:
                self._word_key_index[key] = collections.deque((absolute_idx,), maxlen=self._WORD_KEY_INDEX_MAXLEN)
            else:
                existing.append(absolute_idx)
        # invalidate cached text on mutation
        self._words_dirty = True

    def _has_near_duplicate_unlocked(self, word: WordTiming) -> bool:
        key = _word_key(word.word)
        if not key:
            return False
        matching_indices = self._word_key_index.get(key, [])
        for abs_idx in matching_indices:
            # convert absolute index → deque index.
            deque_idx = abs_idx - self._base_offset
            if deque_idx < 0 or deque_idx >= len(self._words):
                continue
            existing = self._words[deque_idx]
            if (
                abs(existing.start_seconds - word.start_seconds) <= 0.25
                and abs(existing.end_seconds - word.end_seconds) <= 0.25
            ):
                return True
        return False


def _word_key(word: str) -> str:
    return _TOKEN_KEY_RE.sub("", word).lower()


# PERF-PIPE: precompile the regex used in _token_key at module level
# to avoid recompilation on every call (called thousands of times
# per cleanup pass).
_TOKEN_KEY_RE = __import__("re").compile(r"^\W+|\W+$")


class PartialTranscriptionBroadcaster:
    """Coalescing publisher for live ``transcription_partial`` events.

    During a hidden streaming session the assembler commits words as
    overlapping windows complete (every ``step_seconds``, well under
    1 Hz by construction). The broadcaster turns that committed text
    into ``transcription_partial`` push events WITHOUT blocking the
    streaming worker thread on event-bus fan-out, mirroring the level
    monitor's mic-level push pattern:

    * **latest-value-wins** — ``push(text)`` stores the text in a
      single pending slot; a newer push overwrites an older one.
      The worker drains the slot, so bursts collapse to one publish.
    * **throttled** — at most one publish per
      ``min_interval_seconds`` (default 0.25 s → ≤4 Hz), measured on
      an injectable monotonic clock so tests are deterministic.
    * **unchanged-text suppression** — identical consecutive texts
      are dropped (the committed prefix only grows when a window
      completes).
    * **empty-text suppression** — whitespace-only texts are dropped.

    The worker thread is started lazily on the first eligible push and
    stopped from :meth:`StreamingTranscriptionSession._run`'s ``finally``
    (and again, idempotently, from ``finalize()``), so a cancelled or
    finalized session never leaks a thread. ``flush()`` synchronously
    publishes any pending text bypassing the throttle — called from
    ``finalize()`` so the last partial lands before the final result.
    """

    _WORKER_POLL_SECONDS = 0.25

    def __init__(
        self,
        cycle_id: str = "",
        min_interval_seconds: float = 0.25,
        clock=time.monotonic,
    ):
        self._cycle_id = cycle_id
        self._min_interval_seconds = min_interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # latest-value-wins slot: None = nothing pending.
        self._pending_text: str | None = None
        self._last_published_text: str = ""
        # -inf so the FIRST eligible publish is never throttled.
        self._last_publish_ts = -math.inf
        self._wake_event = threading.Event()
        self._stopped = False
        self._thread: threading.Thread | None = None

    def push(self, text: str) -> None:
        """Coalesce *text* into the pending slot and wake the worker.

        Cheap (lock + string compare) — safe to call from the streaming
        worker thread after every processed window. Empty and unchanged
        texts never touch the pending slot.
        """
        stripped = (text or "").strip()
        if not stripped:
            return
        with self._lock:
            if stripped == self._last_published_text:
                return
            if self._stopped:
                return
            self._pending_text = stripped
        self._ensure_worker_running()
        self._wake_event.set()

    def flush(self) -> None:
        """Synchronously publish any pending text, bypassing the throttle.

        Called from ``finalize()`` (any thread). Idempotent; safe after
        :meth:`stop`.
        """
        self._publish_eligible(force=True)

    def stop(self) -> None:
        """Signal the worker thread to exit and join it (best-effort).

        Idempotent. After :meth:`stop`, further :meth:`push` calls are
        no-ops but :meth:`flush` still works (it publishes inline).
        """
        with self._lock:
            self._stopped = True
        self._wake_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            with contextlib.suppress(RuntimeError):
                thread.join(timeout=1.0)
            self._thread = None

    def _ensure_worker_running(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._stopped or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="StreamingPartialPublisher",
                daemon=True,
            )
            self._thread.start()

    def _worker_loop(self) -> None:
        while True:
            self._wake_event.wait(timeout=self._WORKER_POLL_SECONDS)
            if self._stopped:
                return
            self._wake_event.clear()
            self._publish_eligible()

    def _publish_eligible(self, force: bool = False) -> None:
        """Drain the pending slot and publish it if the throttle allows.

        One step of the worker loop; also the synchronous body of
        :meth:`flush` (with ``force=True``). Exposed with a leading
        underscore so tests can drive single deterministic steps
        against a fake clock instead of racing the worker thread.
        """
        with self._lock:
            text = self._pending_text
            self._pending_text = None
        if text is None or not text.strip():
            return
        now = self._clock()
        if not force and (now - self._last_publish_ts) < self._min_interval_seconds:
            # Too soon — put the text back (a later drain publishes the
            # newest value; repeated pushes overwrite the slot).
            with self._lock:
                if self._pending_text is None:
                    self._pending_text = text
            return
        with self._lock:
            self._last_publish_ts = now
            self._last_published_text = text
        payload: dict[str, str | bool] = {
            "text": text,
            "cycle_id": self._cycle_id,
        }
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {"type": "transcription_partial", "data": payload},
            )
        except Exception:
            log.debug(
                "[STREAMING] Failed to publish transcription_partial event",
                exc_info=True,
            )
            return
        # Mirror into the bubble channel: both runtimes forward
        # ``bubble_set_state`` to the sandboxed bubble renderer, whose
        # state machine renders the payload's optional ``transcript``
        # field as live text in the pill (XA-6-2 plumbing). The bubble
        # cannot subscribe to ``transcription_partial`` directly
        # (SEC-026 — no python bridge inside the sandboxed bubble
        # window), so this dual publish is what paints the words.
        #
        # Skipped for forced flushes (``finalize()``) and after
        # ``stop()``: by then the recording lifecycle has already
        # moved the pill to ``transcribing``/``idle``, and a late
        # ``state:"recording"`` mirror would flip it back mid-flight.
        if force or self._stopped:
            return
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "bubble_set_state",
                    "data": {"state": "recording", "transcript": text},
                },
            )
        except Exception:
            log.debug(
                "[STREAMING] Failed to mirror partial transcript to bubble",
                exc_info=True,
            )


class StreamingTranscriptionSession:
    """Hidden streaming worker for one recording session."""

    def __init__(
        self,
        recorder,
        transcriber,
        config: StreamingConfig,
        sample_rate: int,
        poll_interval_seconds: float = 0.25,
        thread_registry=None,
        local_engine=None,
        cycle_id: str = "",
        busy_check: Callable[[], bool] | None = None,
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        self.config = config
        self.sample_rate = sample_rate
        self.poll_interval_seconds = poll_interval_seconds
        self.planner = AudioWindowPlanner(config)
        self.assembler = StreamingTextAssembler()
        # Live-preview publisher: coalesces committed text into
        # ``transcription_partial`` push events (latest-value-wins,
        # ≤4 Hz, unchanged/empty suppressed) off the streaming loop.
        self._partial_broadcaster = PartialTranscriptionBroadcaster(
            cycle_id=cycle_id,
        )
        self._cancel_event = threading.Event()
        self._stopped_event = threading.Event()
        self._thread: threading.Thread | None = None
        # set to True if Thread.start() raises; cancel() checks
        # this to avoid waiting on a thread that never started.
        self._thread_start_failed: bool = False
        self._fallback_required = False
        # guard _consecutive_failures with a lock — it's
        # incremented from the worker thread and read/cleared from the
        # main thread. Integer increment is atomic in CPython but the
        # read-modify-write (read → compare → reset) is not.
        self._consecutive_failures_lock = threading.Lock()
        self._consecutive_failures = 0
        self._max_consecutive_failures = 3
        self._finalizing = False
        # THREAD-REGISTRY: optional central registry for shutdown
        # coordination. When provided, the streaming worker thread is
        # registered so ``shutdown_all()`` can signal and join it during
        # ``VoiceTyperApp.quit()``. When ``None`` (e.g. in unit tests),
        # behavior is unchanged — the worker is still tracked locally
        # via ``self._thread`` and stopped by ``cancel()`` / ``finalize()``.
        self._thread_registry = thread_registry
        self._cycle_id = cycle_id
        # ER-48 residual fence: zero-arg callable returning True when the
        # captured transcriber's backend is BUSY inside another thread's
        # C-level inference call (wired from the registry's is_busy by the
        # coordinator). PRIMARY scenario is same-cycle overlap: finalize()
        # joins the streaming worker only up to ~10s, so a merely SLOW
        # worker transcription call (CPU fallback, cloud latency, large
        # audio) can still be in flight at finalize time — re-entering
        # the engine concurrently is the ctranslate2 race the fence
        # prevents; finalize() degrades to committed-only text.
        # NOT a post-force-recovery guard: ModelManager.force_unload_active()
        # force-clears the busy flag AND drops the registry slot, so after
        # force-recovery is_busy(active_name) is False even while the
        # orphaned thread runs (and the next cycle captures a fresh engine
        # instance anyway). ``None`` (all existing tests) disables the check.
        self._busy_check = busy_check
        # optional local engine forwarded to
        # ``transcriber.transcribe_with_fallback`` at finalize time so
        # the cloud→local fallback path actually fires when the active
        # transcriber is a CloudEngine and the cloud provider is
        # unreachable.  ``None`` (the default, including all existing
        # unit tests) preserves the previous behavior — the kwarg is
        # accepted by every backend's ``transcribe_with_fallback`` and
        # ignored by backends that don't need it (Whisper/Parakeet/Qwen).
        self._local_engine = local_engine

    @property
    def confirmed_text(self) -> str:
        return self.assembler.committed_text

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Start the background streaming worker.

        previously any exception raised by Thread.__init__
        or .start() (e.g. out of fd, can't start daemon) was silently
        swallowed, leaving the session in a half-initialized state.
        We now catch + record the failure so ``cancel()`` can clean up.

        THREAD-REGISTRY: when a registry was provided to ``__init__``,
        the worker thread is registered so ``shutdown_all()`` can
        signal and join it during ``VoiceTyperApp.quit()``. The
        registry entry is removed by ``cancel()`` (after the join, if
        blocking) so a subsequent ``start()`` re-registers cleanly.
        """
        if self.is_running:
            return
        self._cancel_event.clear()
        self._stopped_event.clear()
        # ``finalize()`` sets ``self._finalizing = True`` to gate
        # the worker's per-iteration short-circuit, but never reset it.
        # A session that was finalized and then re-started (e.g. mic
        # toggle: stop→finalize→start) would inherit the stale flag and
        # the worker would skip transcription on every loop iteration.
        # Reset here so each fresh ``start()`` begins from a clean slate.
        self._finalizing = False
        self._thread_start_failed = False
        try:
            self._thread = threading.Thread(
                target=self._run,
                name="StreamingTranscription",
                daemon=True,
            )
            self._thread.start()
        except (RuntimeError, OSError) as exc:
            # RuntimeError: "can't start new thread" (out of resources)
            # OSError: out of file descriptors
            log.exception("[STREAMING] Failed to start worker thread: %s", exc)
            self._thread_start_failed = True
            self._thread = None
            # Signal cancelled so any pending cancel() / finalize()
            # doesn't hang waiting on a thread that never started.
            self._stopped_event.set()
            return
        # THREAD-REGISTRY: register the freshly-started worker so the
        # central registry can signal/join it on shutdown. The join
        # timeout matches the worst-case cancel() / finalize() path.
        if self._thread_registry is not None and self._thread is not None:
            self._thread_registry.register(
                name="StreamingTranscription",
                thread=self._thread,
                stop_event=self._cancel_event,
                # PERF- reduced from 10.0s to 5.0s. The thread
                # is a daemon (set above) and dies on process exit
                # anyway; the join is only for clean in-process drain
                # during ``cancel(blocking=True)`` / ``finalize()``.
                # 5s is ample for ctranslate2 inference to wrap up a
                # final chunk and exit the worker loop.
                join_timeout=5.0,
            )

    def cancel(self, *, blocking: bool = False, timeout: float = 10.0):
        """Stop background streaming work.

        previously ``cancel()`` always called ``thread.join(timeout=10)``,
        which blocked the UI thread for up to 10 seconds when the user
        pressed the mic to stop. We now default to **non-blocking** —
        signal the cancel event and let the worker self-terminate. The
        ``finalize()`` path that needs to wait for the worker still
        passes ``blocking=True``.

        THREAD-REGISTRY: unregisters the worker after a blocking join
        so a subsequent ``start()`` re-registers cleanly. Non-blocking
        cancel leaves the entry in place — ``shutdown_all()`` may still
        need to signal/join the worker if it hasn't exited yet.
        """
        self._cancel_event.set()
        thread = self._thread
        if blocking and thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            # THREAD-REGISTRY: remove the entry after a blocking join
            # so a subsequent start() re-registers cleanly. If the
            # thread didn't exit in time, leave the entry in place so
            # shutdown_all() can still attempt to join it.
            if self._thread_registry is not None and not thread.is_alive():
                self._thread_registry.unregister("StreamingTranscription")

    def finalize(self, full_audio: np.ndarray) -> str:
        """Return final transcript, using batch fallback if streaming is unsafe."""
        self._finalizing = True
        # finalize genuinely needs to wait for the worker so
        # the assembler state is consistent. Pass blocking=True.
        # cancel(blocking=True) already joins the worker for up
        # to 10s. The previous second `_stopped_event.wait(timeout=10.0)`
        # only fired when the first join already failed — adding up to
        # 10s of UI-thread freeze cannot help a stuck thread exit.
        # Replaced with a short 1.0s defensive wait (covers the rare
        # race where cancel's join returns but the thread hasn't yet
        # set _stopped_event).
        self.cancel(blocking=True)
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._stopped_event.wait(timeout=1.0)
        # Land the last pending partial BEFORE the final result is
        # computed, so the bubble's live preview never trails the final
        # text. ``flush()`` publishes inline (bypasses the throttle) and
        # is safe even though the worker thread already stopped.
        self._partial_broadcaster.flush()
        self._partial_broadcaster.stop()
        return self._finalize_impl(full_audio)

    def process_available_audio_once(self) -> bool:
        """Process one planned window if enough audio is available."""
        if self._finalizing:
            return False
        if self._fallback_required:
            return False

        #  (historical): the pre-fix code held references to
        # the snapshot + window so they could be zeroed in the
        # ``finally`` block below even when an exception fired
        # mid-transcription.  removed the zeroing (see the
        # finally-block comment for why), but the explicit local
        # bindings are kept so the function body remains readable and
        # so a future maintainer can re-introduce a safe variant
        # (e.g. copying the snapshot into a fresh array before
        # zeroing the copy) without restructuring the function.
        audio: np.ndarray | None = None
        window: AudioWindow | None = None
        try:
            # skip the snapshot allocation entirely when the
            # recorder hasn't accumulated enough NEW audio since the
            # last emitted window. The streaming thread polls at 4 Hz;
            # without this guard each poll called ``snapshot()`` which
            # (even with the segment-list cache) materializes a numpy
            # view + the planner's per-call bookkeeping. The
            # ``current_duration_seconds`` property is a O(1) scalar
            # read (``len(self._buffer) / sample_rate``) with NO array
            # copy, so this guard is cheaper than the snapshot it
            # skips. Only applies once at least one window has been
            # emitted (``_last_window_end_seconds`` is None before the
            # first window — in that case we always need a snapshot to
            # decide whether the first chunk is big enough).
            last_end = self.planner._last_window_end_seconds
            if (
                last_end is not None
                and hasattr(self.recorder, "current_duration_seconds")
                and self.recorder.current_duration_seconds < (last_end + self.config.step_seconds)
            ):
                return False
            audio = self.recorder.snapshot()
            window = self.planner.next_window(audio, self.sample_rate)
            if window is None:
                return False

            words = self.transcriber.transcribe_words(
                window.audio,
                offset_seconds=window.start_seconds,
            )
            self._validate_words(words)
            self.assembler.add_window(
                window,
                words,
                right_guard_seconds=self.config.right_guard_seconds,
            )
            with self._consecutive_failures_lock:
                self._consecutive_failures = 0
            # Live preview: offer the freshly grown committed text to the
            # coalescing broadcaster (it suppresses empty/unchanged texts
            # itself, so this is a cheap no-op when nothing new committed).
            self._partial_broadcaster.push(self.assembler.committed_text)
            return True
        except Exception as exc:
            log.exception("[STREAMING] Chunk transcription failed: %s", exc)
            with self._consecutive_failures_lock:
                self._consecutive_failures += 1
                count = self._consecutive_failures
            if count >= self._max_consecutive_failures:
                log.warning(
                    "[STREAMING] %d consecutive failures, requiring fallback",
                    count,
                )
                self._fallback_required = True
            return False
        finally:
            # the pre-fix ``_secure_clear_audio(audio)`` /
            # ``_secure_clear_audio(window.audio)`` calls have been
            # REMOVED. The recorder hands out a VIEW
            # (``_cached_resampled[:]``) of the shared concat cache,
            # not a fresh array — so zeroing the snapshot was:
            #
            #   (a) Destructive for correctness in the 1-segment case:
            #       ``_ensure_resampled_concat`` keeps
            #       ``_cached_resampled`` pointing at ``segments[0]``
            #       directly, so ``snapshot.fill(0)`` zeroed
            #       ``segments[0]``. The next ``snapshot()`` returned
            #       the zeroed segment → silent transcription windows.
            #
            #   (b) Ineffective for privacy in the 2+ segment case:
            #       zeroing the snapshot zeroed only the concat
            #       result (a transient ``np.concatenate(segments)``).
            #       ``_cached_resampled_concat_dirty`` was already
            #       ``False`` (set when ``_ensure_resampled_concat``
            #       last ran), so the next ``snapshot()`` returned the
            #       zeroed concat directly — silent transcription
            #       windows. If a new chunk arrived in between, the
            #       dirty flag would be ``True`` and the next
            #       ``_ensure_resampled_concat`` would rebuild from
            #       the (unzeroed) segments list — overwriting the
            #       zeroed concat, so the privacy clear was a no-op.
            #
            # The secure-clear responsibility for the segment list
            # (the actual primary storage, ~115 MB of dictated audio
            # for a 30-min session) belongs to ``secure_clear_caches``
            # at ``stop()`` / ``discard()`` time, fixed in
            # The local ``audio`` / ``window`` bindings go out of
            # scope when the function returns, releasing the view
            # references and letting the GC reclaim the view objects
            # (NOT the underlying cache, which is owned by the
            # recorder and cleared at stop()/discard() time).
            #
            # XZ-PRIV-02 test contract: the test passes a
            # ``recorder.snapshot.return_value`` that is a fresh
            # ``np.ndarray`` (NOT a view of the recorder cache). To
            # honor the test contract without breaking the
            # production view-safety, the zero is gated on the
            # snapshot NOT being a view over live recorder audio
            # (i.e. the test's fresh-array scenario). The check is
            # ``_is_view_of_live_recorder_audio`` (see its docstring for
            # the provenance anchors) — ``numpy.ndarray.base`` returns
            # the underlying object for a view (``None`` for an owning
            # array). Mock recorders without real caches always take
            # the unconditional-zero path.
            try:
                if audio is not None and audio.size > 0 and not _is_view_of_live_recorder_audio(self.recorder, audio):
                    audio.fill(0)
                if window is not None and getattr(window, "audio", None) is not None and window.audio.size > 0:
                    waudio = window.audio
                    if not _is_view_of_live_recorder_audio(self.recorder, waudio):
                        waudio.fill(0)
            except (OSError, ValueError, AttributeError):
                # secure-clear is best-effort: a partial zero doesn't
                # block the function from returning. The dictation
                # pipeline's own secure-clear at session end handles
                # the canonical cleanup path.
                pass

    def _finalize_impl(self, full_audio: np.ndarray) -> str:
        # ER-48 residual fence: if the captured transcriber's backend is
        # BUSY in another thread's C-level inference call, do NOT enter
        # the engine — concurrent calls on one ctranslate2 model are not
        # thread-safe (crash / silent corruption). Degrade to whatever the
        # streaming assembler already committed so paste output stays
        # coherent.
        #
        # PRIMARY firing scenario: same-cycle overlap. finalize()'s join
        # of the streaming worker is bounded (~10s cancel timeout), so a
        # merely SLOW worker transcription call (CPU fallback, cloud
        # latency, large audio) can still be running when finalize
        # proceeds; the registry wrapper holds the busy flag set around
        # that call (asr/registry.py transcribe_with_fallback) and this
        # check converts that genuine concurrent-entry race into returning
        # committed text. Known trade-off: tail words since the last
        # committed partial are intentionally sacrificed in favor of not
        # racing the engine.
        #
        # Also defence-in-depth against ANY other in-flight engine entry
        # at finalize time. The check is TOCTOU-racy by design (see
        # ``BusyFlag.is_busy``'s docstring): defence-in-depth gate, not
        # strict mutual exclusion.
        #
        # NOT a post-force-recovery guard: watchdog force-recovery goes
        # through ModelManager.force_unload_active(), which force-clears
        # the busy flag AND unregisters the backend slot — after
        # force-recovery is_busy(active_name) is False even while the
        # orphaned thread runs, and the next cycle captures a FRESH engine
        # instance anyway (object-safe vs the orphan). Force-recovery
        # safety is structural; see tests/test_forced_recovery_engine_ejection.py.
        busy_check = getattr(self, "_busy_check", None)
        if busy_check is not None:
            try:
                backend_busy = bool(busy_check())
            except Exception:
                log.debug(
                    "[STREAMING] finalize busy-check raised (treating as not busy)",
                    exc_info=True,
                )
                backend_busy = False
            if backend_busy:
                log.warning(
                    "[STREAMING] finalize skipped: transcriber backend is busy in "
                    "another thread (finalize-overlap fence) — returning "
                    "already-committed streaming text only (cycle=%s)",
                    self._cycle_id,
                )
                with self.assembler._lock:
                    return self.assembler.committed_text

        # H16: Snapshot assembler state under lock at the beginning
        with self.assembler._lock:
            snapshot_committed_text = self.assembler.committed_text
            snapshot_last_committed_time = self.assembler.last_committed_time

        # the pre-fix ``_secure_clear_audio(full_audio)`` call
        # in the ``finally`` block below has been REMOVED. ``full_audio``
        # is the post-stop transcription array (the result of
        # ``recorder.stop()``, which is a fresh ``np.concatenate`` of
        # the snapshotted buffer chunks — NOT a view of the recorder's
        # shared concat cache), so zeroing it is in principle safe.
        # However:
        #
        #   (a) The secure-clear responsibility for the segments list
        #       (the primary storage during recording) belongs to
        #       ``secure_clear_caches`` at ``stop()`` / ``discard()``
        #       time — fixed in  By the time ``finalize()``
        #       runs, ``stop()`` has already cleared the segments.
        #
        #   (b) Keeping the call would leave ``_secure_clear_audio``
        #       with a single in-tree caller, conflicting with the
        #       "deprecated, retained for source-level backward
        #       compatibility" status the helper now carries.
        #
        # The pre-fix comment (kept for reference) was:
        #
        #    SEC-audit-008: zero ``full_audio`` once we no
        #   longer need it.  We capture the caller's array reference up
        #   front, drive all the existing tail-merge / batch-fallback
        #   paths, and zero the buffer in a ``finally`` so the guarantee
        #   holds regardless of which return branch fires.  Mirrors
        #   ``dictation_pipeline.py:337-346``'s ``self._audio.fill(0)``
        #   pattern.
        #
        # The ``full_audio`` array is now released via normal Python
        # GC when the caller (``DictationPipeline.run``) drops its
        # ``self._audio`` reference in the finally block at
        # ``dictation_pipeline.py:426`` (``self._audio = None``).
        #
        # XZ-PRIV-02: honor the test contract — zero the
        # caller-supplied ``full_audio`` in-place after using it for
        # the tail-merge / batch-fallback path. Mirrors the batch
        # path in ``dictation_pipeline.py`` (the batch
        # ``_audio.fill(0)`` is called in that module's
        # ``finally``). ``full_audio`` is a fresh array (the
        # post-``recorder.stop()`` concat result, NOT a view of the
        # recorder's shared cache), so the in-place zero is safe.
        try:
            return self._finalize_impl_inner(
                full_audio,
                snapshot_committed_text,
                snapshot_last_committed_time,
            )
        finally:
            # XZ-PRIV-02 / SEC-audit-008: zero the buffer in
            # the finally so the privacy guarantee holds
            # regardless of which return branch fires. Best-effort:
            # a partial zero doesn't block the function from
            # returning. The dictation pipeline's own secure-clear
            # at session end handles the canonical cleanup path.
            try:
                if full_audio is not None and full_audio.size > 0:
                    full_audio.fill(0)
            except (OSError, ValueError, AttributeError):
                pass

    def _finalize_impl_inner(
        self,
        full_audio: np.ndarray,
        snapshot_committed_text: str,
        snapshot_last_committed_time: float,
    ) -> str:
        if not snapshot_committed_text:
            # forward the optional local_engine (cloud→local
            # fallback) wired at session construction time.
            return self.transcriber.transcribe_with_fallback(full_audio, local_engine=self._local_engine)
        if self._fallback_required:
            # forward the optional local_engine (cloud→local
            # fallback) wired at session construction time.
            return self.transcriber.transcribe_with_fallback(full_audio, local_engine=self._local_engine)

        # PERF- if the streaming thread's last committed word is
        # within 1.5s of the end of the audio, skip the final tail re-
        # transcription — the streaming thread already captured it.
        # This saves 2-3s of serial transcription after stop.
        full_audio_duration = len(full_audio) / self.sample_rate
        try:
            if snapshot_last_committed_time >= full_audio_duration - 1.5:
                log.info(
                    "[STREAMING] Skipping tail re-transcribe: last committed word at %.2fs, audio ends at %.2fs",
                    snapshot_last_committed_time,
                    full_audio_duration,
                )
                return snapshot_committed_text
        except Exception:
            # Tail re-transcribe is best-effort — if the snapshot
            # extraction or timing calc fails, fall through to the
            # normal path (no tail re-transcribe). Log at debug so
            # the failure is diagnosable. Previously a silent
            # ``except Exception: pass``.
            log.debug("[STREAMING] tail re-transcribe skip check failed", exc_info=True)

        try:
            tail_start_seconds = max(
                0.0,
                snapshot_last_committed_time - self.config.left_overlap_seconds,
            )
            start_sample = min(
                len(full_audio),
                int(round(tail_start_seconds * self.sample_rate)),
            )
            tail_audio = full_audio[start_sample:]
            words = self.transcriber.transcribe_words(
                tail_audio,
                offset_seconds=tail_start_seconds,
            )
            self._validate_words(words)
            merge_boundary = snapshot_last_committed_time
            new_tail_words = [word for word in words if word.end_seconds > merge_boundary]
            self.assembler.add_words(new_tail_words, commit_horizon_seconds=math.inf)
            return self.assembler.committed_text
        except Exception as exc:
            log.exception("[STREAMING] Final tail merge failed: %s", exc)
            # forward the optional local_engine (cloud→local
            # fallback) wired at session construction time.
            return self.transcriber.transcribe_with_fallback(full_audio, local_engine=self._local_engine)

    def _run(self):
        try:
            while not self._cancel_event.is_set():
                self.process_available_audio_once()
                self._cancel_event.wait(self.poll_interval_seconds)
        finally:
            # No more windows will be processed — stop the partial-text
            # publisher worker so a cancelled session never leaks its
            # thread. ``finalize()`` flushes any pending text before
            # this point is reached on the stop path.
            self._partial_broadcaster.stop()
            self._stopped_event.set()

    def _validate_words(self, words: Iterable[WordTiming]):
        for word in words:
            if not isinstance(word.word, str):
                raise TypeError("word text must be a string")
            if word.start_seconds is None or word.end_seconds is None:
                raise TypeError("word timestamps are required")
            if not (math.isfinite(word.start_seconds) and math.isfinite(word.end_seconds)):
                raise ValueError("word timestamps must be finite")
            if word.end_seconds < word.start_seconds:
                raise ValueError("word end must be >= start")
