"""Growable contiguous float32 recording storage for ``Recorder``.

Owns :class:`GrowableRecordingBuffer`, the ring/eviction backing store
behind ``recorder._audio_pipeline._buffer``, and the legacy-container
normalization helpers :func:`_fresh_recording_buffer_like` /
:func:`_ensure_growable_buffer` used by the snapshot and lifecycle
modules.
"""

from __future__ import annotations

import collections
import contextlib
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE
from voice_typer.server._lazy_import import lazy_module

if TYPE_CHECKING:
    from .recorder import Recorder

np = lazy_module("numpy")


# The recording buffer used to be ``collections.deque(maxlen=N)`` of
_GROWABLE_BUFFER_INITIAL_CAPACITY_SECONDS = 30
# Samples per device chunk used for the hard-cap estimate: two
_GROWABLE_BUFFER_HARD_CAP_SAMPLES_PER_CHUNK = 2 * _AUDIO_BLOCKSIZE


class GrowableRecordingBuffer:
    """Contiguous growable float32 recording storage with deque parity.

    Replaces ``collections.deque(maxlen=N)``-of-chunks as
    ``recorder._audio_pipeline._buffer``. Incoming chunks are COPIED into one pre-allocated
    ndarray (one small memcpy per chunk under ``recorder._audio_pipeline._lock``), so
    snapshots are O(1) views over the filled region instead of O(N)
    re-concatenations.

    Deque-compatibility surface (consumers that were not migrated keep
    working unchanged, notably :meth:`AudioPipeline.append_to_buffer_locked`,
    whose peek/evict-compensate arithmetic around ``maxlen`` is mirrored
    EXACTLY by :meth:`append`):

    - ``.maxlen``: chunk-count capacity, same semantics as ``deque.maxlen``.
      When ``None`` (never in production) no chunk-count eviction happens.
    - ``len(buf)`` / ``bool(buf)``: CHUNK count (deque parity; the sample
      count is :attr:`total_samples`).
    - ``buf[i]``: the i-th appended chunk as a 1-D float32 view (a copy
      only when a chunk physically straddles the ring wrap).
    - ``iter(buf)`` / ``list(buf)``: per-chunk views, oldest first.
    - ``.append`` / ``.appendleft`` / ``.extend`` / ``.clear``.

    Sample-level surface (used by the snapshot/stop/discard paths):

    - :attr:`storage`: the backing ndarray object (the provenance anchor
      streaming.py's zero-gate compares ``arr.base`` against). Stable until
      the next growth reallocation.
    - :attr:`total_samples` / :attr:`appended_samples_total` /
      :attr:`evicted_samples_total`: absolute sample bookkeeping.
    - :meth:`view`: contiguous filled region: a zero-copy view while the
      data is physically linear, an O(N) concatenated copy only past the
      ring-wrap point.
    - :meth:`sample_range` / :meth:`export_copy`.

    Thread-safety: NOT internally synchronized, exactly like the deque it
    replaces, every mutation happens under ``recorder._audio_pipeline._lock`` held by the
    caller (audio-worker append path, snapshot path, stop/discard swaps).
    """

    __slots__ = (
        "maxlen",
        "_storage",
        "_filled",
        "_n_chunks",
        "_lens",
        "_start",
        "_evicted_total",
        "_appended_total",
        "_initial_capacity_samples",
        "_hard_cap_samples",
        "_nominal_sample_rate",
        "_on_extra_eviction",
    )

    def __init__(
        self,
        maxlen: int | None = None,
        *,
        nominal_sample_rate: int = 16000,
        initial_capacity_samples: int | None = None,
        max_capacity_samples: int | None = None,
        on_extra_eviction: Any | None = None,
    ) -> None:
        if maxlen is None:
            # Defer to the canonical default (lazy import: recorder.py is
            try:
                from voice_typer.server.recording.recorder import DEFAULT_MAX_BUFFER_CHUNKS

                maxlen = DEFAULT_MAX_BUFFER_CHUNKS
            except ImportError:  # pragma: no cover - defensive
                maxlen = 30000
        self.maxlen = int(maxlen) if maxlen else None
        # Nominal stream rate used ONLY to translate the initial-capacity
        try:
            self._nominal_sample_rate = max(1, int(nominal_sample_rate))
        except (TypeError, ValueError):
            self._nominal_sample_rate = 16000
        if initial_capacity_samples is not None:
            try:
                initial = max(1, int(initial_capacity_samples))
            except (TypeError, ValueError):
                initial = self._default_initial_capacity()
        else:
            initial = self._default_initial_capacity()
        self._initial_capacity_samples = initial
        if max_capacity_samples is not None:
            self._hard_cap_samples = max(1, int(max_capacity_samples))
        elif self.maxlen is not None:
            self._hard_cap_samples = max(1, self.maxlen * _GROWABLE_BUFFER_HARD_CAP_SAMPLES_PER_CHUNK)
        else:
            # deque(maxlen=None) parity: unbounded storage, never evicts.
            self._hard_cap_samples = 1 << 62
        self._on_extra_eviction = on_extra_eviction
        # Backing ndarray; lazily allocated on first append (an idle
        self._storage: np.ndarray | None = None
        self._filled = 0
        self._n_chunks = 0
        self._lens: collections.deque[int] = collections.deque()
        # Physical offset of the logical window start within ``_storage``.
        self._start = 0
        self._evicted_total = 0
        self._appended_total = 0

    def _default_initial_capacity(self) -> int:
        return max(
            _AUDIO_BLOCKSIZE,
            _GROWABLE_BUFFER_INITIAL_CAPACITY_SECONDS * self._nominal_sample_rate,
        )

    @property
    def storage(self) -> Any:
        """The backing ndarray (provenance anchor for view-identity gates).

        ``None`` until the first append allocates it lazily, an idle
        recorder holds no buffer at all."""
        return self._storage

    @property
    def total_samples(self) -> int:
        return self._filled

    @property
    def chunk_count(self) -> int:
        return self._n_chunks

    @property
    def evicted_samples_total(self) -> int:
        """Absolute number of samples dropped from the front (ring phase)."""
        return self._evicted_total

    @property
    def appended_samples_total(self) -> int:
        """Absolute end position of the stored audio (= evicted + filled)."""
        return self._appended_total

    @property
    def first_dtype(self) -> str:
        """dtype string of the stored samples (chunks are cast on append)."""
        if self._storage is not None:
            return str(self._storage.dtype)
        return "float32"

    @property
    def is_physically_wrapped(self) -> bool:
        """True when the logical window straddles the end of the storage."""
        return self._storage is not None and self._start + self._filled > self._storage.shape[0]

    def __len__(self) -> int:
        return self._n_chunks

    def __bool__(self) -> bool:
        return self._n_chunks > 0

    def _chunk_bounds(self, index: int) -> tuple[int, int]:
        """Logical [begin, end) offsets of the i-th chunk within the window.

        Negative indices follow deque semantics; ``IndexError`` matches
        deque's out-of-range behaviour. Walking the boundary list from the
        front is O(i), acceptable because production only indexes ``[0]``
        (the backpressure peek) and tests use tiny indices.
        """
        n = self._n_chunks
        if index < 0:
            index += n
        if index < 0 or index >= n:
            raise IndexError("chunk index out of range")
        begin = 0
        for i in range(index):
            begin += self._lens[i]
        return begin, begin + self._lens[index]

    def _extract(self, begin: int, end: int) -> Any:
        """Filled-region [begin, end) as a view, or a copy across the wrap."""
        s = self._start
        b = s + begin
        e = s + end
        cap = self._storage.shape[0]
        if e <= cap:
            return self._storage[b:e]
        if b >= cap:
            return self._storage[b - cap : e - cap]
        # Chunk straddles the physical wrap, must join the two halves.
        return np.concatenate((self._storage[b:], self._storage[: e - cap]))

    def __getitem__(self, index: int) -> Any:
        begin, end = self._chunk_bounds(index)
        return self._extract(begin, end)

    def __iter__(self) -> Any:
        begin = 0
        for length in self._lens:
            yield self._extract(begin, begin + length)
            begin += length

    def _grown_storage(self, capacity: int) -> Any:
        """Return a fresh ndarray of ``capacity`` samples with the filled
        window copied to its front (linear phase invariant: data occupies
        ``[0, filled)``, so ``_start == 0`` holds before AND after)."""
        new_storage = np.empty(capacity, dtype=np.float32)
        if self._filled > 0:
            new_storage[: self._filled] = self._storage[: self._filled]
        return new_storage

    def set_hard_cap(self, max_capacity_samples: int) -> None:
        """Raise (never lower below the current allocation) the hard cap.

        Called from the dynamic sizing path once the device rate and
        ``max_recording_time_seconds`` are known."""
        try:
            wanted = max(1, int(max_capacity_samples))
        except (TypeError, ValueError):
            return
        current = self._storage.shape[0] if self._storage is not None else 0
        if wanted > self._hard_cap_samples or wanted > current:
            self._hard_cap_samples = max(wanted, self._hard_cap_samples)

    def _evict_oldest(self) -> int:
        """Drop the oldest chunk from the front of the window."""
        if not self._lens:
            return 0
        l0 = self._lens.popleft()
        self._n_chunks -= 1
        self._filled -= l0
        cap = self._storage.shape[0] if self._storage is not None else 0
        if cap:
            self._start = (self._start + l0) % cap
        else:
            self._start = 0
        self._evicted_total += l0
        return l0

    def append(self, chunk: Any) -> None:
        """Copy one chunk into the storage (caller holds ``recorder._audio_pipeline._lock``).

        Eviction mirrors ``collections.deque(maxlen=...)`` EXACTLY for the
        chunk-count rule: when ``len(self) >= maxlen``, exactly ONE oldest
        chunk is dropped per append, which is precisely the eviction the
        append-path caller already compensated its running sample counter
        for (it peeks ``buf[0]`` before calling this method). A secondary
        CAPACITY rule (only reachable with adversarially mixed chunk sizes
        that overflow the hard cap estimate) may evict additional chunks;
        those extra samples are reported through ``on_extra_eviction`` so
        the caller can keep its counter honest.
        """
        arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
        n = int(arr.shape[0])
        extra_evicted = 0
        # Rule 1, chunk-count eviction (deque maxlen parity, see docstring).
        if self.maxlen is not None and self._n_chunks >= self.maxlen:
            self._evict_oldest()
        # Rule 2, growth, then capacity.
        storage = self._storage
        if storage is None:
            # Lazy first allocation (30 s nominal capacity floor).
            storage = np.empty(max(self._initial_capacity_samples, n), dtype=np.float32)
            self._storage = storage
        elif self._filled + n > storage.shape[0]:
            if self._start == 0 and storage.shape[0] < self._hard_cap_samples:
                # Geometric doubling, clamped to the hard cap.
                storage = self._grown_storage(min(self._hard_cap_samples, max(2 * storage.shape[0], self._filled + n)))
            elif self._n_chunks > 0:
                # Ring phase: freeze the allocation, evict from the front.
                while self._filled + n > storage.shape[0] and self._n_chunks > 0:
                    extra_evicted += self._evict_oldest()
        # Last-resort correctness valve: a single chunk larger than the
        if self._filled + n > storage.shape[0]:
            storage = self._grown_storage(self._filled + n)
        self._storage = storage
        pos = (self._start + self._filled) % storage.shape[0]
        first = min(n, storage.shape[0] - pos)
        storage[pos : pos + first] = arr[:first]
        if n > first:
            storage[: n - first] = arr[first:]
        self._lens.append(n)
        self._n_chunks += 1
        self._filled += n
        self._appended_total += n
        if extra_evicted and self._on_extra_eviction is not None:
            # Samples evicted BEYOND the one maxlen-mirrored chunk the
            self._on_extra_eviction(extra_evicted)

    def appendleft(self, chunk: Any) -> None:
        """Prepend one chunk (pre-roll path; caller holds ``recorder._audio_pipeline._lock``).

        The pre-roll prepend runs once per session, BEFORE any live chunk
        has been processed (the buffer is empty in practice), so the
        O(filled) right-shift of existing content is bounded by ~1s of
        audio and paid once. Growth here ignores the hard cap: pre-roll
        volume is bounded by ``preroll_seconds × device_rate``.
        """
        arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
        n = int(arr.shape[0])
        storage = self._storage
        if storage is None or self._filled + n > storage.shape[0]:
            # Prepend growth ignores the hard cap: pre-roll volume is
            old_cap = storage.shape[0] if storage is not None else 0
            storage = self._grown_storage(max(2 * old_cap, self._initial_capacity_samples, self._filled + n))
            self._storage = storage
            self._start = 0
        elif self._start != 0:
            # Normalize to linear layout so the prepend is a single shift.
            self._relocate_window_to_front()
        if self._filled > 0:
            # Overlapping right-shift via slice assignment; numpy buffers
            storage[n : n + self._filled] = storage[: self._filled]
        storage[:n] = arr
        self._lens.appendleft(n)
        self._n_chunks += 1
        self._filled += n
        self._appended_total += n

    def extend(self, chunks: Any) -> None:
        for c in chunks:
            self.append(c)

    def _relocate_window_to_front(self) -> None:
        """Move the logical window to physical offset 0 (single copy)."""
        if self._start == 0 or self._filled == 0 or self._storage is None:
            self._start = 0
            return
        cap = self._storage.shape[0]
        if self._start + self._filled <= cap:
            self._storage[: self._filled] = self._storage[self._start : self._start + self._filled]
        else:
            tail = cap - self._start
            joined = np.concatenate((self._storage[self._start :], self._storage[: self._filled - tail]))
            self._storage[: self._filled] = joined
        self._start = 0

    def clear(self) -> None:
        """Zero every occupied byte and reset to empty (secure-clear sync
        contract). The ALLOCATION is kept so the next session reuses it
        without a fresh malloc."""
        if self._storage is not None and self._filled > 0:
            cap = self._storage.shape[0]
            s = self._start
            if s + self._filled <= cap:
                self._storage[s : s + self._filled].fill(0)
            else:
                self._storage[s:].fill(0)
                self._storage[: s + self._filled - cap].fill(0)
        self._filled = 0
        self._n_chunks = 0
        self._lens.clear()
        self._start = 0
        self._evicted_total = 0
        self._appended_total = 0

    def view(self) -> Any:
        """The filled region: a zero-copy VIEW while physically linear
        (``view().base is self.storage``), an owning concatenated COPY once
        the ring wraps. Callers treat the result as read-only."""
        if self._filled == 0 or self._storage is None:
            return np.empty(0, dtype=np.float32)
        s = self._start
        cap = self._storage.shape[0]
        if s + self._filled <= cap:
            return self._storage[s : s + self._filled]
        return np.concatenate((self._storage[s:], self._storage[: s + self._filled - cap]))

    def sample_range(self, abs_begin: int, abs_end: int) -> Any:
        """Absolute-positioned contiguous slice of the stored audio.

        Positions are absolute since session start (they survive front
        eviction); regions already evicted are clamped away. Returns a view
        when physically linear, a copy across the wrap."""
        floor = self._evicted_total
        ceiling = self._appended_total
        b = max(int(abs_begin), floor)
        e = min(int(abs_end), ceiling)
        if e <= b:
            return np.empty(0, dtype=np.float32)
        return self._extract(b - floor, e - floor)

    def export_copy(self) -> Any:
        """Fresh contiguous OWNING copy of the filled region (stop() handoff).

        Always allocates, the returned array is independent of the storage,
        so the background secure-clear of the old storage cannot touch it
        (preserves the stop()-race fix ordering: copy first, enqueue the
        zeroing after)."""
        v = self.view()
        if v.flags.owndata:
            return v
        return v.copy()


def _fresh_recording_buffer_like(recorder: Recorder, old_buffer: Any) -> GrowableRecordingBuffer:
    """Build an empty replacement buffer preserving the old container's
    ``maxlen`` (and wiring the capacity-eviction counter hook when the
    owner provides one)."""
    from voice_typer.server.recording.recorder import DEFAULT_MAX_BUFFER_CHUNKS

    maxlen = getattr(old_buffer, "maxlen", None) or DEFAULT_MAX_BUFFER_CHUNKS
    on_extra = getattr(recorder, "_note_buffer_capacity_eviction", None)
    new_buf = GrowableRecordingBuffer(maxlen=maxlen, on_extra_eviction=on_extra)
    nominal_sr = getattr(getattr(recorder, "config", None), "sample_rate", None)
    if nominal_sr is not None:
        with contextlib.suppress(TypeError, ValueError):
            new_buf._nominal_sample_rate = max(1, int(nominal_sr))
    return new_buf


def _ensure_growable_buffer(recorder: Recorder) -> None:
    """Normalize ``recorder._audio_pipeline._buffer`` to a :class:`GrowableRecordingBuffer`.

    MUST be called under ``recorder._audio_pipeline._lock``. Two legitimate cases produce a
    non-growable container:

    1. the mic hot-swap restart path (``disconnect_handler``) swaps in a
       plain fresh ``collections.deque`` mid-session;
    2. tests inject plain lists/deques directly.

    An EMPTY legacy container is replaced with a fresh buffer (O(1)); a
    populated one has its chunks migrated (one bulk copy, a rare event:
    hot-swap flushes the buffer before swapping, so production always hits
    the empty case). Subsequent appends land in the installed buffer
    because every consumer re-reads ``recorder._audio_pipeline._buffer`` per call.
    """
    buf = recorder._audio_pipeline._buffer
    if isinstance(buf, GrowableRecordingBuffer):
        return
    new_buf = _fresh_recording_buffer_like(recorder, buf)
    items = list(buf) if buf else []
    if items:
        for chunk in items:
            new_buf.append(chunk)
    recorder._audio_pipeline._buffer = new_buf
