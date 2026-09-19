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

# Token-key normalizer: the MEMOIZED helper shared with text_cleanup is
from voice_typer.server.text_cleanup import _token_key

np = lazy_module("numpy")

log = logging.getLogger(__name__)


def _is_view_of_live_recorder_audio(recorder: Any, arr: Any) -> bool:
    """Provenance check: is ``arr`` a VIEW over audio the live
    recorder still owns?

    The recorder hands out zero-copy snapshot views over TWO backing
    stores, and a destructive ``fill(0)`` on such a view would corrupt the
    recording mid-session (silent transcription windows):

    1. ``recorder._cached_resampled``: the incremental resampled-stream
       cache (snapshot resample path);
    2. ``recorder._audio_pipeline._buffer.storage``, the contiguous raw
       recording buffer itself (the common no-resample path; the buffer
       object may also be a plain deque/list in tests and post-hot-swap
       windows, hence the defensive getattr chain).

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
    # defensive getattr chain (mock/fake recorders in tests may not
    pipeline = getattr(recorder, "_audio_pipeline", None)
    buf = getattr(pipeline, "_buffer", None)
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
        if self.start_seconds != other.start_seconds or self.end_seconds != other.end_seconds:
            return False
        # Same object or same underlying buffer → equal
        if self.audio is other.audio:
            return True
        # Different objects with same scalars, compare shapes then hash
        if self.audio.shape != other.audio.shape:
            return False
        return bool(np.array_equal(self.audio, other.audio))

    def __hash__(self) -> int:
        # hash on the scalar fields; audio is unhashable but
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
        # PROVENANCE INVARIANT, read before touching this window slice:
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
                best_index = index + len(frame)

        if best_index is None or best_rms > self.config.silence_threshold:
            return requested_end_seconds
        return (search_start + best_index) / sample_rate


@dataclass
class StreamingTextAssembler:
    """Commit timestamped words only after they are outside the unsafe tail."""

    # cap _words to prevent unbounded growth. Pre-fix this
    _MAX_WORDS = 10000
    # per-key bounded deque maxlen for _word_key_index. 8 entries
    _WORD_KEY_INDEX_MAXLEN = 8
    _words: collections.deque[WordTiming] = field(
        default_factory=lambda: collections.deque(maxlen=StreamingTextAssembler._MAX_WORDS)
    )
    # number of items evicted from the front of ``_words``.
    _base_offset: int = 0
    # RACE-031: the seen-timestamps set is the streaming dedup
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
        # RACE-031: batch-then-lock is the contention approximation.
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
        # Prune committed words that are well before the commit horizon
        if math.isfinite(commit_horizon_seconds):
            prune_threshold = commit_horizon_seconds - 5.0
            if prune_threshold > 0:
                self._prune_old_entries(prune_threshold)
        # when ``commit_horizon_seconds == math.inf`` (the
        return " ".join(committed)

    def _prune_old_entries(self, threshold: float) -> None:
        """Prune dedup structures for old entries; never remove from _words.

        _words is the output accumulator and must keep all committed entries.
        Only _seen_timestamps and _word_key_index are pruned to limit memory.

        previously rebuilt ``_word_key_index`` from scratch
        on every prune. With a 5-min session and 200+ words, this was
        O(n) every few seconds. We now remove only the indices that
        pointed to evicted timestamps, but since _words is never
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
        # do NOT rebuild _word_key_index, it's keyed on

    def _insert_word_unlocked(self, word: WordTiming):
        """Insert a word, maintaining sorted order.

        PERF- previously this did a linear scan + list.insert
        (O(n) per insert, O(n^2) per session) and then shifted all
        index entries.  Now we just append and defer sorting to
        commit time, the words are already approximately in order
        (streaming chunks arrive sequentially), so a full sort at
        commit is O(n log n) vs the O(n^2) insert pattern.

        enforce maxlen on _words. When the list exceeds
        _MAX_WORDS, evict the oldest entry and log a warning.
        """
        # detect imminent eviction BEFORE appending so we
        if self._words.maxlen is not None and len(self._words) >= self._words.maxlen:
            # Peek the leftmost item; deque.append will evict it.
            evicted_word = self._words[0]
            evicted_absolute_idx = self._base_offset  # current offset → 0 in deque
            #  do NOT log evicted_word.word at any level —
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
            self._base_offset += 1
            # Drop the index entry pointing at the evicted word. Other
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
            evicted_ts_key = (
                round(evicted_word.start_seconds, 3),
                round(evicted_word.end_seconds, 3),
            )
            self._seen_timestamps.discard(evicted_ts_key)

        key = _token_key(word.word)
        # Absolute index = base_offset + current deque length (before append).
        absolute_idx = self._base_offset + len(self._words)
        self._words.append(word)
        if key:
            # use a bounded deque (maxlen=_WORD_KEY_INDEX_MAXLEN)
            existing = self._word_key_index.get(key)
            if existing is None:
                self._word_key_index[key] = collections.deque((absolute_idx,), maxlen=self._WORD_KEY_INDEX_MAXLEN)
            else:
                existing.append(absolute_idx)
        # invalidate cached text on mutation
        self._words_dirty = True

    def _has_near_duplicate_unlocked(self, word: WordTiming) -> bool:
        key = _token_key(word.word)
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


class PartialTranscriptionBroadcaster:
    """Coalescing publisher for live ``transcription_partial`` events.

    During a hidden streaming session the assembler commits words as
    overlapping windows complete (every ``step_seconds``, well under
    1 Hz by construction). The broadcaster turns that committed text
    into ``transcription_partial`` push events WITHOUT blocking the
    streaming worker thread on event-bus fan-out, mirroring the level
    monitor's mic-level push pattern:

    * **latest-value-wins**, ``push(text)`` stores the text in a
      single pending slot; a newer push overwrites an older one.
      The worker drains the slot, so bursts collapse to one publish.
    * **throttled**, at most one publish per
      ``min_interval_seconds`` (default 0.25 s → ≤4 Hz), measured on
      an injectable monotonic clock so tests are deterministic.
    * **unchanged-text suppression**, identical consecutive texts
      are dropped (the committed prefix only grows when a window
      completes).
    * **empty-text suppression**, whitespace-only texts are dropped.

    The worker thread is started lazily on the first eligible push and
    stopped from :meth:`StreamingTranscriptionSession._run`'s ``finally``
    (and again, idempotently, from ``finalize()``), so a cancelled or
    finalized session never leaks a thread. ``flush()`` synchronously
    publishes any pending text bypassing the throttle, called from
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

        Cheap (lock + string compare), safe to call from the streaming
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
            # Too soon, put the text back (a later drain publishes the
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
        # (SEC-026, no python bridge inside the sandboxed bubble
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
        self._partial_broadcaster = PartialTranscriptionBroadcaster(
            cycle_id=cycle_id,
        )
        self._cancel_event = threading.Event()
        self._stopped_event = threading.Event()
        self._thread: threading.Thread | None = None
        # set to True if Thread.start() raises; cancel() checks
        self._thread_start_failed: bool = False
        self._fallback_required = False
        # guard _consecutive_failures with a lock, it's
        self._consecutive_failures_lock = threading.Lock()
        self._consecutive_failures = 0
        self._max_consecutive_failures = 3
        self._finalizing = False
        # THREAD-REGISTRY: optional central registry for shutdown
        self._thread_registry = thread_registry
        self._cycle_id = cycle_id
        # Residual fence: zero-arg callable returning True when the
        self._busy_check = busy_check
        # optional local engine forwarded to
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
            log.exception("[STREAMING] Failed to start worker thread: %s", exc)
            self._thread_start_failed = True
            self._thread = None
            # Signal cancelled so any pending cancel() / finalize()
            self._stopped_event.set()
            return
        # THREAD-REGISTRY: register the freshly-started worker so the
        if self._thread_registry is not None and self._thread is not None:
            self._thread_registry.register(
                name="StreamingTranscription",
                thread=self._thread,
                stop_event=self._cancel_event,
                # PERF- reduced from 10.0s to 5.0s. The thread
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
        cancel leaves the entry in place, ``shutdown_all()`` may still
        need to signal/join the worker if it hasn't exited yet.
        """
        self._cancel_event.set()
        thread = self._thread
        if blocking and thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            # THREAD-REGISTRY: remove the entry after a blocking join
            if self._thread_registry is not None and not thread.is_alive():
                self._thread_registry.unregister("StreamingTranscription")

    def finalize(self, full_audio: np.ndarray) -> str:
        """Return final transcript, using batch fallback if streaming is unsafe."""
        self._finalizing = True
        # Replaced with a short 1.0s defensive wait (covers the rare
        self.cancel(blocking=True)
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._stopped_event.wait(timeout=1.0)
        # Land the last pending partial BEFORE the final result is
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
        audio: np.ndarray | None = None
        window: AudioWindow | None = None
        try:
            # skip the snapshot allocation entirely when the
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
            try:
                if audio is not None and audio.size > 0 and not _is_view_of_live_recorder_audio(self.recorder, audio):
                    audio.fill(0)
                if window is not None and getattr(window, "audio", None) is not None and window.audio.size > 0:
                    waudio = window.audio
                    if not _is_view_of_live_recorder_audio(self.recorder, waudio):
                        waudio.fill(0)
            except (OSError, ValueError, AttributeError):
                # secure-clear is best-effort: a partial zero doesn't
                pass

    def _finalize_impl(self, full_audio: np.ndarray) -> str:
        # Residual fence: if the captured transcriber's backend is
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
                    "another thread (finalize-overlap fence), returning "
                    "already-committed streaming text only (cycle=%s)",
                    self._cycle_id,
                )
                with self.assembler._lock:
                    return self.assembler.committed_text

        # Snapshot assembler state under lock at the beginning
        with self.assembler._lock:
            snapshot_committed_text = self.assembler.committed_text
            snapshot_last_committed_time = self.assembler.last_committed_time

        # the pre-fix ``_secure_clear_audio(full_audio)`` call
        try:
            return self._finalize_impl_inner(
                full_audio,
                snapshot_committed_text,
                snapshot_last_committed_time,
            )
        finally:
            # Zero the buffer in
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
            return self.transcriber.transcribe_with_fallback(full_audio, local_engine=self._local_engine)
        if self._fallback_required:
            # forward the optional local_engine (cloud→local
            return self.transcriber.transcribe_with_fallback(full_audio, local_engine=self._local_engine)

        # PERF- if the streaming thread's last committed word is
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
            # Tail re-transcribe is best-effort, if the snapshot
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
            return self.transcriber.transcribe_with_fallback(full_audio, local_engine=self._local_engine)

    def _run(self):
        try:
            while not self._cancel_event.is_set():
                self.process_available_audio_once()
                self._cancel_event.wait(self.poll_interval_seconds)
        finally:
            # No more windows will be processed. Stop the partial-text
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
