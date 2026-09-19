"""Extracted helpers for :mod:`.recorder`: partial split of the Recorder god-class.

This module is the first step of the  / god-class decomposition. The
``Recorder`` class in :mod:`.recorder` mixed 7+ disjoint concerns in a single
~3000-line class. Phase 4.5 /  had already extracted
:mod:`.device_manager`, :mod:`.resampling`, :mod:`.exceptions`, and
:mod:`.buffer`; this module continues the split by moving the two largest
tail-of-file methods (``snapshot`` and ``discard``) out of ``recorder.py``
into focused helper functions here.

The functions take the ``Recorder`` instance as their first argument (named
``recorder``) and access its private attributes / methods directly. This
preserves the existing runtime contract (same attributes read, same methods
called, same lock acquired) while shrinking ``recorder.py`` by ~200 lines.
``Recorder.snapshot`` and ``Recorder.discard`` become 1-line delegators so
existing call sites, subclass overrides, and ``inspect.getsource`` checks
that look for the method on the ``Recorder`` class continue to work.

The full split plan (to be completed once parallel surgical fixes to
``recorder.py`` land) is:

  - ``recorder/capture.py``: audio callback + worker loop
    (``_audio_callback_dispatch``, ``_audio_worker_loop``,
    ``_event_worker_loop``, ``_start_audio_worker``, ``_stop_audio_worker``,
    ``_start_event_worker``, ``_stop_event_worker``).
  - ``recorder/lifecycle.py``: stream lifecycle
    (``start``, ``stop``, ``discard``, ``_teardown_stream``). ``discard``
    is extracted here as the first step.
  - ``recorder/device_management.py``: the 12 device methods (most already
    delegated to :class:`.device_manager.DeviceManager`).
  - ``recorder/format.py``: audio format helpers
    (``resample_chunk``, ``prepare_audio``, ``ensure_mono``), backed by
    :func:`.resampling.resample_audio`. ``snapshot`` remains a 1-line
    public-API delegator on ``Recorder``.
  - ``recorder/worker_threads.py``: worker-thread management.

The conversion from ``recorder.py`` (module) to ``recorder/`` (package) is
deferred until all in-flight surgical fixes to specific ``recorder.py``
line ranges have landed, to avoid line-number conflicts with parallel
sub-agents.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

# ``_AUDIO_BLOCKSIZE`` is used in ``start_recording`` to scale
from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE, scaled_audio_blocksize
from voice_typer.server._lazy_import import lazy_module

# The recording buffer used to be ``collections.deque(maxlen=N)`` of
_GROWABLE_BUFFER_INITIAL_CAPACITY_SECONDS = 30
# Samples per device chunk used for the hard-cap estimate: two
_GROWABLE_BUFFER_HARD_CAP_SAMPLES_PER_CHUNK = 2 * _AUDIO_BLOCKSIZE

# The ``retune_audio_processor`` helper used to be imported here
from . import buffer as _buffer_mod  # noqa: E402, owning module of the secure-clear helpers (C-ARCH-2)
from .exceptions import (  # noqa: E402, post-threshold import kept for direct unit tests (see comment above)
    ResampleError,
)
from .format import prepare_audio, resample_chunk  # noqa: E402

# VAD per-chunk cache refresh (body of the removed
from .vad_helpers import refresh_vad_caches  # noqa: E402

if TYPE_CHECKING:
    from .recorder import Recorder

log = logging.getLogger("voice_typer.server.recording")


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


def _invalidate_resampled_cache(recorder: Recorder, new_key: tuple) -> None:
    """Zero-and-reset the resampled-stream cache (dtype/src/dst change).

    Mirrors the historical invalidation block: the previous cache array is
    securely zeroed BEFORE being replaced (SEC-audit-008), the resample
    cursor returns to the start of the buffer, and the compat segment-list
    slots are reset. Caller holds ``recorder._audio_pipeline._lock``."""
    cached = getattr(recorder, "_cached_resampled", None)
    if cached is not None and cached.size > 0:
        _buffer_mod._secure_clear_array(cached)
    recorder._cached_resampled = np.array([], dtype=np.float32)
    recorder._cached_resampled_len = 0
    recorder._cached_native_chunk_count = 0
    recorder._cached_resample_key = new_key
    # Compat slots retained (pinned by tests): the segment lists are no
    recorder._cached_no_resample_len = -1
    recorder._cached_no_resample_arr = None
    recorder._cached_resampled_segments = []
    recorder._cached_resampled_concat_dirty = False
    recorder._cached_no_resample_segments = []
    recorder._cached_no_resample_concat_dirty = False


def _append_resampled_samples(recorder: Recorder, samples: np.ndarray) -> None:
    """Append freshly resampled samples to the incremental resampled cache.

    The cache array (``_cached_resampled``) OWNS a geometrically grown
    capacity buffer; ``_cached_resampled_len`` tracks the filled prefix.
    Growth reallocates by doubling and copies only the filled prefix —
    amortized O(1) per sample versus the historical rebuild-on-demand.
    Caller holds ``recorder._audio_pipeline._lock``."""
    m = int(samples.shape[0])
    if m == 0:
        return
    cur = recorder._cached_resampled
    k = int(recorder._cached_resampled_len)
    cap = cur.size if cur is not None else 0
    need = k + m
    if need > cap:
        initial = max(
            _AUDIO_BLOCKSIZE,
            _GROWABLE_BUFFER_INITIAL_CAPACITY_SECONDS * 16000,
        )
        new_cap = max(initial, 2 * cap, need)
        grown = np.empty(new_cap, dtype=np.float32)
        if k > 0:
            grown[:k] = cur[:k]
        grown[k:need] = samples
        recorder._cached_resampled = grown
    elif cur is not None:
        cur[k:need] = samples
    recorder._cached_resampled_len = need


def take_snapshot(recorder: Recorder) -> np.ndarray:
    """Return current recorded audio without clearing the active buffer.

    Contiguous-storage implementation: the recording lives in ONE growable
    float32 ndarray (:class:`GrowableRecordingBuffer` on
    ``recorder._audio_pipeline._buffer``), so both snapshot flavors are O(1):

      - no-resample path (the COMMON production path, an AudioProcessor
        resamples each chunk to 16 kHz before it is appended, so
        ``_buffer_sr == target_sr``): return a zero-copy VIEW over the
        buffer's filled region. Zero allocation, zero memcpy per 4 Hz poll.
      - resample path (raw native-rate audio whose rate differs from the
        target): newly arrived samples are resampled ONCE and appended to
        the incremental ``_cached_resampled`` capacity array (geometric
        growth, no segment lists, no rebuild-on-demand concat); return a
        view of its filled prefix.

    View contract (pinned by ``tests/test_recorder_snapshot_view.py``):
    every non-empty snapshot is a numpy VIEW sharing memory with its
    backing store, ``recorder._cached_resampled`` on the resample path,
    ``recorder._audio_pipeline._buffer.storage`` on the no-resample path. The cache arrays
    are REPLACED (never mutated in place) on growth/invalidation, so
    previously-returned views remain valid until released. streaming.py's
    zero-gate (zero the audio buffer before release) relies on exactly
    this identity.

    Lock discipline: unchanged from the deque implementation, a lock-free
    empty fast path (avoids 4 Hz contention with the audio callback when
    idle), then everything else under ``recorder._audio_pipeline._lock``, which serializes
    against the audio worker's append path.
    """
    # lock-free fast path for the empty-buffer case.
    if not recorder._audio_pipeline._buffer:
        return np.array([], dtype=np.float32)
    with recorder._audio_pipeline._lock:
        _ensure_growable_buffer(recorder)
        buf = recorder._audio_pipeline._buffer
        if not buf or buf.total_samples == 0:
            return np.array([], dtype=np.float32)
        # (defensive, should never happen because ``__init__`` and
        effective_sr = recorder._audio_pipeline._buffer_sr or recorder._effective_sr
        # read the cached target_sr instead of
        target_sr = getattr(recorder, "_cached_target_sr", None) or recorder.config.sample_rate

        # invalidate the cache if any of the parameters that
        new_key = (
            buf.first_dtype,
            effective_sr,
            target_sr,
        )
        if recorder._cached_resample_key != new_key:
            _invalidate_resampled_cache(recorder, new_key)

        if effective_sr != target_sr:
            return _snapshot_resampled_locked(recorder, buf, effective_sr, target_sr)
        # No resampling needed: the contiguous storage IS the snapshot.
        return buf.view()


def _snapshot_resampled_locked(
    recorder: Recorder,
    buf: GrowableRecordingBuffer,
    effective_sr: int,
    target_sr: int,
) -> np.ndarray:
    """Incremental-resample branch of :func:`take_snapshot`.

    Resamples ONLY the samples that arrived since the previous call
    (absolute cursor ``_cached_native_chunk_count``, repurposed from
    chunk-count to SAMPLE-count by the contiguous-storage change), appends
    them to the incremental cache, and returns a view over the cache's
    filled prefix. Caller holds ``recorder._audio_pipeline._lock``.
    """
    end_abs = buf.appended_samples_total
    start_abs = max(int(recorder._cached_native_chunk_count), buf.evicted_samples_total)
    cached_len = int(getattr(recorder, "_cached_resampled_len", 0))
    if end_abs <= start_abs:
        # Nothing new to resample. Return the current prefix view.
        return recorder._cached_resampled[:cached_len]
    raw_new = buf.sample_range(start_abs, end_abs)
    # if resampling fails, drop the bad chunk rather
    try:
        new_resampled = resample_chunk(recorder, raw_new, effective_sr, target_sr)
    except ResampleError as e:
        log.warning(
            "[RECORDING] Snapshot resample failed; dropping %d native samples: %s",
            len(raw_new),
            e,
        )
        recorder._cached_native_chunk_count = end_abs
        # return a view of the unchanged cached prefix.
        return recorder._cached_resampled[:cached_len]
    _append_resampled_samples(recorder, np.asarray(new_resampled, dtype=np.float32).reshape(-1))
    recorder._cached_native_chunk_count = end_abs
    # return a VIEW into the cache (``.base is _cached_resampled``) —
    return recorder._cached_resampled[: recorder._cached_resampled_len]


def discard_recording(recorder: Recorder) -> None:
    """Discard current recording without processing.

    Extracted verbatim from ``Recorder.discard`` ( split). The
        timeout constants ``_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S`` and
        ``_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S`` are imported lazily from
        :mod:`.recorder` to avoid a circular import (recorder.py imports this
        module at the top of its class body).
    """
    # Lazy import: recorder.py is still loading when this module is first
    from voice_typer.server.recording.recorder import (
        _AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S,
        _EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S,
    )

    recorder._recording_event.clear()
    # STREAM-FIX (Task 6): set _user_stop_pending before stream.stop() so
    recorder._user_stop_pending = True
    # 17-H-: increment stop_generation for symmetry with stop() so
    recorder._stop_generation += 1
    # guard _effective_sr reset with the lock so a concurrent
    with recorder._audio_pipeline._lock:
        recorder._effective_sr = recorder.config.sample_rate
        # reset ``_buffer_sr`` to ``None`` so the next session
        recorder._audio_pipeline._buffer_sr = None
    recorder._last_rms = 0.0
    recorder._silence_timer = 0.0
    recorder._silence_start_time = None
    recorder._silence_warning_count = 0
    recorder._silence_next_warning_wait = 10.0
    # securely zero cached audio arrays BEFORE reassignment
    recorder._session_state.secure_clear_caches(recorder)
    # 17-H-: drain callback + stop + close via _teardown_stream()
    recorder._teardown_stream()
    # stop the audio worker thread. drain=False because
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # stop the IPC event worker with drain=False, the recording was
    with recorder._worker_lifecycle_lock:
        recorder._capture.stop_event_worker_body(recorder, timeout=_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # CPU-03: stop the device health checker thread (mirrors the event worker).
    recorder._stop_device_health_checker(timeout=0.0)
    with recorder._audio_pipeline._lock:
        # SEC-audit-008: defer buffer zeroing to background daemon
        _ensure_growable_buffer(recorder)
        _old_buffer = recorder._audio_pipeline._buffer
        # Contiguous storage: data lives in ONE ndarray, so "swap in a
        recorder._audio_pipeline._buffer = _fresh_recording_buffer_like(recorder, _old_buffer)
        # PERF: zero the running buffered-samples counter, the fresh
        recorder._audio_pipeline._total_buffered_samples = 0
        _buffer_mod._secure_clear_array_background(_old_buffer)


def start_recording(recorder: Recorder) -> None:
    """Body of :meth:`Recorder.start` (after the ``_start_lock`` permission-gate block).

    Phase 4.5, extracted from :mod:`.recorder` to shrink the
        3772-LOC ``recorder.py`` god class. The ``with self._start_lock:``
        block (containing the recording-event check + microphone-permission
        pre-flight) stays on ``Recorder.start`` so the source-inspection
        test (``tests/test_recording.py::TestRec5StartLock``) continues to
        pin the lock contract.

        This function runs WITHOUT holding ``_start_lock``, it is called
        after ``Recorder.start`` releases the lock. The lock contract is
        that ``start()`` and ``discard()`` cannot both pass the
        ``_recording_event.is_set()`` check at the same time, which the
        lock guarantees by serializing the gate.

    reset ALL per-session state here, not just the buffer.
        Previously some flags (_max_duration_warning_sent,
        _silence_warning_sent, etc.) persisted across recordings,
        causing stale state to suppress warnings on the next session.

    (revised): The dead ``_silence_warning_sent`` and
        ``_max_duration_warning_sent`` boolean flags have been REMOVED.
        They were declared and reset here but NEVER read in any
        conditional, the actual silence-warning state machine uses
        the integer counter ``_silence_warning_count`` (which IS read
        at recording.py:1109). The dead flags were misleading
        maintainers into thinking warning deduplication existed when
    it didn't: see FORENSIC_REVIEW_COMPLETE.md →

        SEC-audit-008: ``_secure_clear_array`` is now actually used
        here to zero cached audio arrays (``_cached_resampled`` and
        ``_cached_no_resample_arr``) before they're dropped. This
        prevents forensic recovery of audio data from process memory
        between sessions.

        CRITICAL, DO NOT RESTRUCTURE (2026-07-20)
        ========================================
        The device-enumeration block below (``last_error``,
        ``selected_device``, ``effective_sr``, the ``for candidate in
        candidates`` loop, the fallback loop, and the
        ``if recorder._stream is None:`` check) MUST stay at this
        function's body scope. OUTSIDE the ``callback`` closure built
        by ``recorder._stream_lifecycle.build_audio_callback(recorder)``
        above. A previous
        merge accidentally nested this block INSIDE the ``def
        callback()`` closure, which made ``last_error`` a local of
        ``callback`` instead of ``start()``, raising
        ``UnboundLocalError`` on every recording start.

        The device-loop bodies live on :class:`.stream_lifecycle.StreamLifecycle`
        (``open_stream_for_candidates`` / ``open_stream_fallback``, invoked
        from this function's scope via ``recorder._stream_lifecycle``, OUTSIDE
        the callback closure), so the
        structural contract above is preserved.

        DO NOT move device enumeration inside the callback closure.
        DO NOT re-add ``set_thread_registry``: it was merge damage.
    """
    # Lazy import: ``recorder.py`` is still loading when this module

    # SEC-audit-008: securely zero cached
    recorder._secure_clear_session_caches()

    # per-session state reset () ──
    recorder._session_state.reset_session_state(recorder)

    max_rec = recorder._session_state.cache_session_config(recorder)

    device = recorder._devices._resolve_device()
    candidates = recorder._devices._same_physical_microphone_candidates(device)

    # build the PortAudio callback closure () ──
    callback = recorder._stream_lifecycle.build_audio_callback(recorder)

    # CRITICAL, DO NOT RESTRUCTURE (2026-07-20)
    last_error: Exception | None = None
    selected_device: Any = None
    effective_sr: int = recorder.config.sample_rate
    used_fallback = False

    selected_device, effective_sr, last_error = recorder._stream_lifecycle.open_stream_for_candidates(
        recorder, candidates, callback, effective_sr, last_error
    )

    # If all same-name candidates failed, try ALL available input devices
    if recorder._stream_lifecycle._stream is None and not used_fallback:
        selected_device, effective_sr, used_fallback, last_error = recorder._stream_lifecycle.open_stream_fallback(
            recorder, candidates, callback, effective_sr, last_error
        )

    if recorder._stream_lifecycle._stream is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("No input device could be opened")

    recorder._session_state.resize_buffers_for_sample_rate(recorder, effective_sr, max_rec)

    # Scale the SPSC ring buffer to ~2s of headroom at the
    _uu36_sizing_sr = effective_sr if effective_sr > 0 else recorder.config.sample_rate
    if _uu36_sizing_sr > 0:
        _uu36_blocksize = scaled_audio_blocksize(_uu36_sizing_sr)
        _uu36_new_ring_capacity = max(64, int(_uu36_sizing_sr / _uu36_blocksize * 2.0))
        for _payload in recorder._ring_buffer:
            _arr = _payload[0] if isinstance(_payload, tuple) else _payload
            if isinstance(_arr, np.ndarray):
                _arr.fill(0)
        recorder._ring_buffer = collections.deque(maxlen=_uu36_new_ring_capacity)

    if selected_device != device and isinstance(selected_device, int):
        # Session-local fallback ONLY: the opened stream uses the
        if device is None and not used_fallback:
            log.debug(
                "[RECORDING] System Default resolved to device [%s] for this session",
                selected_device,
            )
        else:
            _label = "System Default" if device is None else device
            log.info(
                "[RECORDING] Selected microphone [%s] unavailable; using device [%s] "
                "for this session (saved selection unchanged)",
                _label,
                selected_device,
            )

    recorder._recording_event.set()

    # inside ``AudioCallbackDispatcher.audio_worker_loop`` (capture.py)

    target_sr = recorder.config.sample_rate
    # The mutable resampler state lives on the .resampling submodule;
    from voice_typer.server.recording import resampling as _recording_resampling

    if (
        effective_sr != target_sr
        and _recording_resampling._resample_poly is None
        and _recording_resampling._resample_poly_error is None
    ):
        # Skip the synchronous warm-up when the scipy preloader daemon
        _preloader = getattr(recorder, "_scipy_preloader_thread", None)
        if isinstance(_preloader, threading.Thread) and _preloader.is_alive():
            log.debug("[RECORDING] scipy preloader in flight, resampler warm-up left to the background thread")
        else:
            # Warm up synchronously when no background preloader is
            recorder.warm_up_resampler()

    # best-effort retune of the AudioProcessor's filter chain
    try:
        from .disconnect_handler import retune_audio_processor

        retune_audio_processor(
            recorder._audio_processor,
            effective_sr,
            recorder.config,
            context="on start",
        )
    except Exception:
        log.warning(
            "[RECORDING] retune_audio_processor failed on start, per-chunk resample will run on the worker thread",
            exc_info=True,
        )

    # refresh the per-chunk VAD property cache now that
    refresh_vad_caches(recorder)

    # Start the audio worker thread AFTER ``_recording_event.set()``
    try:
        recorder._start_audio_worker()
        audio_worker_started = True
    except BaseException:
        audio_worker_started = False
        recorder._stop_generation += 1
        recorder._recording_event.clear()
        with contextlib.suppress(Exception):
            recorder._teardown_stream()
        raise

    # Start the IPC event worker thread AFTER the audio worker
    try:
        with recorder._worker_lifecycle_lock:
            recorder._capture.start_event_worker_body(recorder)
    except BaseException:
        recorder._stop_generation += 1
        recorder._recording_event.clear()
        with contextlib.suppress(Exception):
            recorder._teardown_stream()
        if audio_worker_started:
            with contextlib.suppress(Exception):
                recorder._stop_audio_worker(timeout=0.5, drain=False)
        raise

    # CPU-03: start the device health checker thread (off the audio
    recorder._devices._start_device_health_checker()

    # Wire the idle-recording gate. ``Recorder.start`` is the
    _mic_watcher = recorder._devices._mic_watcher
    if _mic_watcher is not None:
        _mic_watcher.set_idle(False)


def stop_recording(recorder: Recorder) -> np.ndarray:
    """Stop recording and return the complete audio array.

        Body of :meth:`Recorder.stop`: extracted verbatim (with ``self.X``
    rewritten to ``recorder.X``) by  / Phase 4.5 to shrink the
        ~2748-LOC ``recorder.py`` god class. ``Recorder.stop`` becomes a
        1-line delegator so existing call sites, subclass overrides, and
        ``inspect.getsource`` checks that look for the method on the
        ``Recorder`` class continue to work. There is NO source-inspection
        test contract pinning ``Recorder.stop`` source (verified via
        ``rg "inspect.getsource.*Recorder\\.stop\\b" tests/``, the matches
        on ``IPCServer.stop`` are unrelated); the simple Option B delegate
        is sufficient.

        Step ordering (preserved verbatim):

          1. ``_recording_event`` early-out fast path (returns empty
             ``float32`` array when not recording).
          2. Clear ``_recording_event`` (the gate the audio callback and
             streaming thread poll).
          3. Increment ``_stop_generation`` (HOTKEY-CRASH: stale
             disconnect handlers from the audio callback bail out).
          4. Set ``_user_stop_pending = True`` (STREAM-FIX:
             ``_stream_finished_callback`` suppresses the false "Stream
             finished unexpectedly" warning during the intentional stop).
          5. ``_teardown_stream()``: 300 ms callback-drain poll, then
             ``stream.stop()`` + ``stream.close()`` (shared with
             ``discard_recording``; see the helper's docstring for the
    PERF- history).
          6. Clear ``_user_stop_pending`` (any future
             ``_stream_finished_callback`` is now a genuine disconnect).
          7. ``_stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S,
             drain=True)``: drain=True so the last few hundred ms of
             audio (chunks still in the ring buffer) end up in
             ``recorder._audio_pipeline._buffer`` and are concatenated below
    ().
          8. ``_stop_event_worker(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S,
             drain=True)``: drains the IPC event queue.
          9. ``_stop_device_health_checker(timeout=0.0)``, fire-and-
             forget (the daemon exits on its next 30 s wait() return).
         10. Snapshot ``_buffer`` under ``_lock``: swap the deque for a
             fresh empty one + capture the chunk list + capture
             ``_buffer_sr`` local + ``_secure_clear_caches``; then
             release the lock and ``np.concatenate`` the captured chunks
             OUTSIDE the lock so the audio worker's append path (which
             acquires the same lock) is not blocked for the 50–300 ms
             concat duration.
         11. Compute audio stats (RMS via ``np.dot``, peak, silence
             percentage) and store them in ``_last_audio_stats`` so the
    transcription engine can reuse them ().
         12. ``_prepare_audio(audio, effective_sr)``: H15: resample from
             scratch (no cache) for the full audio.
         13. ``log.info`` the stop summary (duration, sr, samples, RMS,
             peak, silence_pct, stream/concat/resample/total ms); near-
             silence warning when ``rms < 0.001``.

        The empty-buffer fast path zeros the cached audio arrays
        (``_secure_clear_caches``), resets ``_chunk_count = 0``, and
    returns an empty ``float32`` array,  secure-clear
        contract.

        Critical: ``_buffer_sr`` (the actual rate of the audio in
        ``recorder._audio_pipeline._buffer``) is captured into a local BEFORE
        ``_secure_clear_caches`` resets it to ``None``. The local is the
        authoritative source rate for the snapshotted audio, the chunks
        in ``_captured_chunks`` were appended at this rate by
        ``_process_audio_chunk``. Using ``_effective_sr`` here (the
        device's native rate) would cause ``_prepare_audio`` to resample
        the already-16 kHz audio a second time (chipmunk voice).
    """
    # Lazy import: ``recorder.py`` is still loading when this module
    from voice_typer.server.recording.recorder import (
        _AUDIO_WORKER_JOIN_TIMEOUT_S,
        _EVENT_WORKER_JOIN_TIMEOUT_S,
    )

    # Fast-path ONLY when no worker refs exist. A
    if (
        not recorder._recording_event.is_set()
        and recorder._worker_thread is None
        and recorder._event_worker_thread is None
    ):
        return np.array([], dtype=np.float32)

    stop_started = time.perf_counter()
    recorder._recording_event.clear()

    # HOTKEY-CRASH: increment stop_generation so any stale disconnect
    recorder._stop_generation += 1

    # STREAM-FIX: mark that we're about to call stream.stop()
    recorder._user_stop_pending = True

    # 17-H-: drain callback + stop + close via _teardown_stream()
    recorder._teardown_stream()
    # STREAM-FIX: clear the user-stop-pending flag now
    recorder._user_stop_pending = False
    stream_ms = (time.perf_counter() - stop_started) * 1000

    # stop the audio worker thread. drain=True so the
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S, drain=True)

    # cut the worst-case stop() latency from ~5.8s to ~2.4s
    with recorder._worker_lifecycle_lock:
        recorder._capture.stop_event_worker_body(recorder, timeout=_EVENT_WORKER_JOIN_TIMEOUT_S, drain=True)

    # device health checker fire-and-forget. Pass timeout=0.0
    recorder._stop_device_health_checker(timeout=0.0)

    # snapshot the buffer under the lock, then release the lock BEFORE
    concat_started = time.perf_counter()
    with recorder._audio_pipeline._lock:
        if not recorder._audio_pipeline._buffer:
            # securely zero cached audio arrays BEFORE
            recorder._session_state.secure_clear_caches(recorder)
            recorder._audio_pipeline._chunk_count = 0
            # PERF: zero the running buffered-samples counter alongside
            recorder._audio_pipeline._total_buffered_samples = 0
            # idle-recording gate. Return to the 12 s idle
            _mic_watcher = recorder._devices._mic_watcher
            if _mic_watcher is not None:
                _mic_watcher.set_idle(True)
            return np.array([], dtype=np.float32)
        # Non-empty: normalize legacy containers (hot-swap deque / test
        _ensure_growable_buffer(recorder)
        _old_buffer = recorder._audio_pipeline._buffer
        recorder._audio_pipeline._buffer = _fresh_recording_buffer_like(recorder, _old_buffer)
        # PERF: zero the running buffered-samples counter, the fresh
        recorder._audio_pipeline._total_buffered_samples = 0
        # Critical: capture ``_buffer_sr`` into a local
        _captured_buffer_sr = recorder._audio_pipeline._buffer_sr
        # securely zero cached audio arrays BEFORE
        recorder._session_state.secure_clear_caches(recorder)
    # materialize the contiguous result OUTSIDE the lock so the
    audio = _old_buffer.export_copy()
    concat_ms = (time.perf_counter() - concat_started) * 1000

    # SEC-audit-008 (race fix): enqueue the OLD storage for background
    _buffer_mod._secure_clear_array_background(_old_buffer)

    # Log audio statistics for diagnostics
    effective_sr = _captured_buffer_sr if _captured_buffer_sr is not None else recorder._effective_sr

    # Pipeline ``prepare_audio`` with the stats computation below.
    resample_started = time.perf_counter()
    _resample_result: dict[str, Any] = {"audio": None, "exc": None}

    def _run_prepare_audio() -> None:
        try:
            _resample_result["audio"] = prepare_audio(recorder, audio, effective_sr)
        except BaseException as exc:  # noqa: BLE001, re-raised after join
            _resample_result["exc"] = exc

    _resample_thread = threading.Thread(
        target=_run_prepare_audio,
        name="stop-prepare-audio",
        daemon=True,
    )
    _resample_thread.start()

    duration = len(audio) / effective_sr if len(audio) > 0 else 0
    # initialize ``rms``/``peak``/``silence_pct`` BEFORE
    rms: float = 0.0
    peak: float = 0.0
    silence_pct: float = 0.0
    if len(audio) > 0:
        # AUDIO-NP: use np.dot for RMS in stop() too
        if audio.size:
            flat = audio.reshape(-1)
            rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
            # PERF: allocation-free peak, ``max(|x|) == max(max(x),
            peak = max(float(flat.max()), -float(flat.min()))
        else:
            peak = 0.0
            rms = 0.0
        # PERF: compute ``np.abs(flat)`` ONCE and reuse it for
        abs_flat = np.abs(flat) if audio.size else None
        silence_pct = float(np.sum(abs_flat < 0.001) / audio.size * 100) if abs_flat is not None else 0.0
        recorder._last_rms = rms
        # store the full-recording stats so the
        recorder._last_audio_stats = (rms, peak, silence_pct)
    else:
        recorder._last_rms = 0.0
        recorder._last_audio_stats = (0.0, 0.0, 0.0)
        log.warning("[RECORDING] No audio data captured!")

    # Wait for the resample thread to complete and propagate any
    _resample_thread.join()
    if _resample_result["exc"] is not None:
        raise _resample_result["exc"]
    audio = _resample_result["audio"]
    resample_ms = (time.perf_counter() - resample_started) * 1000

    # AUDIO-PROC: post-capture spectral noise reduction (offline,

    total_ms = (time.perf_counter() - stop_started) * 1000
    if len(audio) > 0:
        log.info(
            "[RECORDING] Audio stopped: duration=%.1fs, sr=%d, samples=%d | "
            "RMS=%.6f, peak=%.6f, silence=%.1f%% | "
            "stream=%.0fms, concat=%.0fms, resample=%.0fms, total=%.0fms",
            duration,
            effective_sr,
            len(audio),
            rms,
            peak,
            silence_pct,
            stream_ms,
            concat_ms,
            resample_ms,
            total_ms,
        )
        if rms < 0.001:
            log.warning(
                "[RECORDING] Near-silence detected! (RMS=%.6f) Microphone may not be capturing audio.",
                rms,
            )
    else:
        # Warning already emitted above when len(audio) == 0
        pass

    # idle-recording gate. Return to the 12 s idle cadence
    _mic_watcher = recorder._devices._mic_watcher
    if _mic_watcher is not None:
        _mic_watcher.set_idle(True)

    return audio


np = lazy_module("numpy")
