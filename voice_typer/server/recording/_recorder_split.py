"""Extracted helpers for :mod:`.recorder` — partial split of the Recorder god-class.

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

  - ``recorder/capture.py`` — audio callback + worker loop
    (``_audio_callback_dispatch``, ``_audio_worker_loop``,
    ``_event_worker_loop``, ``_start_audio_worker``, ``_stop_audio_worker``,
    ``_start_event_worker``, ``_stop_event_worker``).
  - ``recorder/lifecycle.py`` — stream lifecycle
    (``start``, ``stop``, ``discard``, ``_teardown_stream``). ``discard``
    is extracted here as the first step.
  - ``recorder/device_management.py`` — the 12 device methods (most already
    delegated to :class:`.device_manager.DeviceManager`).
  - ``recorder/format.py`` — audio format helpers
    (``snapshot``, ``_resample_chunk``, ``_prepare_audio``,
    ``_resample_audio_impl``, ``_ensure_mono``). ``snapshot`` is extracted
    here as the first step.
  - ``recorder/worker_threads.py`` — worker-thread management.

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
# the SPSC ring buffer capacity to ~2s of headroom at the device's
# effective sample rate. Imported from ``_audio_constants`` (single
# source of truth, no circular import).
from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE
from voice_typer.server._lazy_import import lazy_module

# ── Contiguous recording storage (replaces the chunk deque + concat caches) ──
#
# The recording buffer used to be ``collections.deque(maxlen=N)`` of
# per-chunk float32 arrays, and ``take_snapshot`` maintained TWO derived
# copies of the same samples: a segment list (one entry per snapshot that
# saw new chunks) plus a lazily-materialized contiguous concatenation.
# Sustained footprint was therefore 2×N (chunks + contiguous cache), and
# the ~4 Hz streaming poll repeatedly rebuilt the contiguous cache,
# producing ~100 GB of allocation churn over a 15-minute dictation.
#
# The single-storage replacement below keeps ONE pre-allocated growable
# float32 ndarray per stream kind:
#
#   - the raw/device-kind storage lives on ``recorder._buffer``
#     (:class:`GrowableRecordingBuffer`);
#   - the resampled-16k kind (only used when the buffer holds native-rate
#     audio that differs from the target rate) is the incremental
#     ``_cached_resampled`` capacity array managed by :func:`take_snapshot`.
#
# Capacity policy (all numbers chosen once, here):
#   * INITIAL — 30 seconds of audio at the stream's nominal rate. Small
#     enough that short dictations never over-allocate, large enough that
#     a typical session never reallocates at all.
#   * GROWTH — exact doubling (``new = max(2×current, needed)``), clamped
#     to the hard cap. Amortized O(1) per appended sample; total copy
#     traffic over a whole session is ≤ 2× the final data size (a few
#     hundred MB worst case) versus the previous ~100 GB rebuild churn.
#   * HARD CAP — ``maxlen_chunks × 1024`` samples. ``maxlen`` is counted
#     in device-rate chunks (``_AUDIO_BLOCKSIZE`` = 512 samples); stored
#     audio is post-chain, typically downsampled (≤512 samples/chunk) but
#     up to 1024 for an upsampled chain (e.g. 8 kHz device → 16 kHz chain),
#     hence the 2× factor. This preserves the duration semantics of the old
#     ``DEFAULT_MAX_BUFFER_CHUNKS`` deque cap (~31 min at the default).
#   * SATURATION — once the hard cap is reached the storage stops growing
#     and behaves as a ring: further appends evict the oldest samples
#     (mirroring the old deque ``maxlen`` eviction). Snapshots taken while
#     the data physically wraps return an O(N) concatenated COPY — strictly
#     no worse than the old every-poll rebuild, and unreachable in normal
#     operation because auto-stop fires at ``max_recording_time_seconds``
#     (< the cap duration).
_GROWABLE_BUFFER_INITIAL_CAPACITY_SECONDS = 30
# Samples per device chunk used for the hard-cap estimate: two
# ``_AUDIO_BLOCKSIZE`` blocks (see rationale above).
_GROWABLE_BUFFER_HARD_CAP_SAMPLES_PER_CHUNK = 2 * _AUDIO_BLOCKSIZE

# The ``retune_audio_processor`` helper used to be imported here
# and called from ``start_recording``. The call was REMOVED —
# the per-chunk resample in ``AudioProcessor.process_chunk`` handles
# 48 kHz → 16 kHz on the worker thread, keeping the filter chain at
# WHISPER_SAMPLE_RATE (16 kHz). The helper definition still lives in
# ``disconnect_handler.py`` (other call site also removed; the function
# is kept for the direct unit tests in ``test_device_manager.py``).
from .exceptions import (  # noqa: E402 — post-threshold import kept for direct unit tests (see comment above)
    ResampleError,
)

if TYPE_CHECKING:
    from .recorder import Recorder

log = logging.getLogger("voice_typer.server.recording")


class GrowableRecordingBuffer:
    """Contiguous growable float32 recording storage with deque parity.

    Replaces ``collections.deque(maxlen=N)``-of-chunks as
    ``recorder._buffer``. Incoming chunks are COPIED into one pre-allocated
    ndarray (one small memcpy per chunk under ``recorder._lock``), so
    snapshots are O(1) views over the filled region instead of O(N)
    re-concatenations.

    Deque-compatibility surface (consumers that were not migrated keep
    working unchanged — notably :meth:`AudioPipeline.append_to_buffer_locked`,
    whose peek/evict-compensate arithmetic around ``maxlen`` is mirrored
    EXACTLY by :meth:`append`):

    - ``.maxlen`` — chunk-count capacity, same semantics as ``deque.maxlen``.
      When ``None`` (never in production) no chunk-count eviction happens.
    - ``len(buf)`` / ``bool(buf)`` — CHUNK count (deque parity; the sample
      count is :attr:`total_samples`).
    - ``buf[i]`` — the i-th appended chunk as a 1-D float32 view (a copy
      only when a chunk physically straddles the ring wrap).
    - ``iter(buf)`` / ``list(buf)`` — per-chunk views, oldest first.
    - ``.append`` / ``.appendleft`` / ``.extend`` / ``.clear``.

    Sample-level surface (used by the snapshot/stop/discard paths):

    - :attr:`storage` — the backing ndarray object (the provenance anchor
      streaming.py's zero-gate compares ``arr.base`` against). Stable until
      the next growth reallocation.
    - :attr:`total_samples` / :attr:`appended_samples_total` /
      :attr:`evicted_samples_total` — absolute sample bookkeeping.
    - :meth:`view` — contiguous filled region: a zero-copy view while the
      data is physically linear, an O(N) concatenated copy only past the
      ring-wrap point.
    - :meth:`sample_range` / :meth:`export_copy`.

    Thread-safety: NOT internally synchronized — exactly like the deque it
    replaces, every mutation happens under ``recorder._lock`` held by the
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
            # still loading when this module is first imported).
            try:
                from voice_typer.server.recording.recorder import DEFAULT_MAX_BUFFER_CHUNKS

                maxlen = DEFAULT_MAX_BUFFER_CHUNKS
            except ImportError:  # pragma: no cover - defensive
                maxlen = 30000
        self.maxlen = int(maxlen) if maxlen else None
        # Nominal stream rate used ONLY to translate the initial-capacity
        # duration into samples before the first real chunk reveals the
        # actual stored rate. Coerced defensively: MagicMock configs in
        # tests may carry a non-numeric ``sample_rate``.
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
        # recorder holds no buffer memory at all).
        self._storage: np.ndarray | None = None
        self._filled = 0
        self._n_chunks = 0
        self._lens: collections.deque[int] = collections.deque()
        # Physical offset of the logical window start within ``_storage``.
        # Stays 0 until chunk-count/capacity eviction begins (ring phase).
        self._start = 0
        self._evicted_total = 0
        self._appended_total = 0

    def _default_initial_capacity(self) -> int:
        return max(
            _AUDIO_BLOCKSIZE,
            _GROWABLE_BUFFER_INITIAL_CAPACITY_SECONDS * self._nominal_sample_rate,
        )

    # ── introspection ────────────────────────────────────────────────────

    @property
    def storage(self) -> Any:
        """The backing ndarray (provenance anchor for view-identity gates).

        ``None`` until the first append allocates it lazily — an idle
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

    # ── deque-parity protocol ────────────────────────────────────────────

    def __len__(self) -> int:
        return self._n_chunks

    def __bool__(self) -> bool:
        return self._n_chunks > 0

    def _chunk_bounds(self, index: int) -> tuple[int, int]:
        """Logical [begin, end) offsets of the i-th chunk within the window.

        Negative indices follow deque semantics; ``IndexError`` matches
        deque's out-of-range behaviour. Walking the boundary list from the
        front is O(i) — acceptable because production only indexes ``[0]``
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
        # Chunk straddles the physical wrap — must join the two halves.
        return np.concatenate((self._storage[b:], self._storage[: e - cap]))

    def __getitem__(self, index: int) -> Any:
        begin, end = self._chunk_bounds(index)
        return self._extract(begin, end)

    def __iter__(self) -> Any:
        begin = 0
        for length in self._lens:
            yield self._extract(begin, begin + length)
            begin += length

    # ── mutation ─────────────────────────────────────────────────────────

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
        """Copy one chunk into the storage (caller holds ``recorder._lock``).

        Eviction mirrors ``collections.deque(maxlen=...)`` EXACTLY for the
        chunk-count rule — when ``len(self) >= maxlen``, exactly ONE oldest
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
        # Rule 1 — chunk-count eviction (deque maxlen parity, see docstring).
        # This eviction is exactly the one the append caller peeked and
        # compensated its counter for, so it is NOT reported through the
        # extra-eviction hook.
        if self.maxlen is not None and self._n_chunks >= self.maxlen:
            self._evict_oldest()
        # Rule 2 — growth, then capacity.
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
        # whole hard cap must still be stored (the old deque accepted any
        # chunk size); grow past the cap rather than corrupting state.
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
            # caller already compensated for.
            self._on_extra_eviction(extra_evicted)

    def appendleft(self, chunk: Any) -> None:
        """Prepend one chunk (pre-roll path; caller holds ``recorder._lock``).

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
            # bounded by ``preroll_seconds × device_rate``.
            old_cap = storage.shape[0] if storage is not None else 0
            storage = self._grown_storage(max(2 * old_cap, self._initial_capacity_samples, self._filled + n))
            self._storage = storage
            self._start = 0
        elif self._start != 0:
            # Normalize to linear layout so the prepend is a single shift.
            self._relocate_window_to_front()
        if self._filled > 0:
            # Overlapping right-shift via slice assignment; numpy buffers
            # overlapping ranges correctly (bounded by `filled`).
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

    # ── sample-level reads ───────────────────────────────────────────────

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

        Always allocates — the returned array is independent of the storage,
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
    """Normalize ``recorder._buffer`` to a :class:`GrowableRecordingBuffer`.

    MUST be called under ``recorder._lock``. Two legitimate cases produce a
    non-growable container:

    1. the mic hot-swap restart path (``disconnect_handler``) swaps in a
       plain fresh ``collections.deque`` mid-session;
    2. tests inject plain lists/deques directly.

    An EMPTY legacy container is replaced with a fresh buffer (O(1)); a
    populated one has its chunks migrated (one bulk copy — a rare event:
    hot-swap flushes the buffer before swapping, so production always hits
    the empty case). Subsequent appends land in the installed buffer
    because every consumer re-reads ``recorder._buffer`` per call.
    """
    buf = recorder._buffer
    if isinstance(buf, GrowableRecordingBuffer):
        return
    new_buf = _fresh_recording_buffer_like(recorder, buf)
    items = list(buf) if buf else []
    if items:
        for chunk in items:
            new_buf.append(chunk)
    recorder._buffer = new_buf


def _invalidate_resampled_cache(recorder: Recorder, new_key: tuple) -> None:
    """Zero-and-reset the resampled-stream cache (dtype/src/dst change).

    Mirrors the historical invalidation block: the previous cache array is
    securely zeroed BEFORE being replaced (SEC-audit-008), the resample
    cursor returns to the start of the buffer, and the compat segment-list
    slots are reset. Caller holds ``recorder._lock``."""
    from voice_typer.server import recording as _recording_pkg

    cached = getattr(recorder, "_cached_resampled", None)
    if cached is not None and cached.size > 0:
        _recording_pkg._secure_clear_array(cached)
    recorder._cached_resampled = np.array([], dtype=np.float32)
    recorder._cached_resampled_len = 0
    recorder._cached_native_chunk_count = 0
    recorder._cached_resample_key = new_key
    # Compat slots retained (pinned by tests): the segment lists are no
    # longer populated by the incremental cache but stay reset here so
    # stale references from any foreign writer cannot survive invalidation.
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
    Caller holds ``recorder._lock``."""
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
    ``recorder._buffer``), so both snapshot flavors are O(1):

      - no-resample path (the COMMON production path — an AudioProcessor
        resamples each chunk to 16 kHz before it is appended, so
        ``_buffer_sr == target_sr``): return a zero-copy VIEW over the
        buffer's filled region. Zero allocation, zero memcpy per 4 Hz poll.
      - resample path (raw native-rate audio whose rate differs from the
        target): newly arrived samples are resampled ONCE and appended to
        the incremental ``_cached_resampled`` capacity array (geometric
        growth — no segment lists, no rebuild-on-demand concat); return a
        view of its filled prefix.

    View contract (pinned by ``tests/test_recorder_snapshot_view.py``):
    every non-empty snapshot is a numpy VIEW sharing memory with its
    backing store — ``recorder._cached_resampled`` on the resample path,
    ``recorder._buffer.storage`` on the no-resample path. The cache arrays
    are REPLACED (never mutated in place) on growth/invalidation, so
    previously-returned views remain valid until released. streaming.py's
    zero-gate (zero the audio buffer before release) relies on exactly
    this identity.

    Lock discipline: unchanged from the deque implementation — a lock-free
    empty fast path (avoids 4 Hz contention with the audio callback when
    idle), then everything else under ``recorder._lock``, which serializes
    against the audio worker's append path.
    """
    # lock-free fast path for the empty-buffer case.
    # Avoids 4 Hz lock contention with the audio callback thread when the
    # recorder isn't actively recording.
    if not recorder._buffer:
        return np.array([], dtype=np.float32)
    with recorder._lock:
        _ensure_growable_buffer(recorder)
        buf = recorder._buffer
        if not buf or buf.total_samples == 0:
            return np.array([], dtype=np.float32)
        # use ``_buffer_sr`` (the actual sample rate of the audio in
        # ``recorder._buffer``) instead of ``_effective_sr`` (the device's
        # native rate). When an AudioProcessor is active, ``process_chunk``
        # resamples to the chain's construction rate (typically 16 kHz)
        # before appending — so the buffer holds chain-rate audio. Using
        # ``_effective_sr`` here would cause ``_resample_chunk`` to
        # resample a second time (chain→target on top of the native→chain
        # resample already done by ``process_chunk``), which (a) wastes
        # CPU and (b) can introduce artifacts from the double resample.
        # Fall back to ``_effective_sr`` when ``_buffer_sr`` is 0/unset
        # (defensive — should never happen because ``__init__`` and
        # ``start()`` both initialize it).
        effective_sr = getattr(recorder, "_buffer_sr", None) or recorder._effective_sr
        # read the cached target_sr instead of
        # recorder.config.sample_rate to avoid attribute lookup under lock.
        target_sr = getattr(recorder, "_cached_target_sr", None) or recorder.config.sample_rate

        # invalidate the cache if any of the parameters that
        # affect the resampled output have changed since the last
        # snapshot. Without this, a dtype or sample-rate change
        # mid-session would return stale (and wrong-rate) cached audio.
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
        # Return a VIEW into it — the caller (streaming.py) only reads and
        # slices; the storage is replaced (not mutated) on growth, so the
        # view stays valid until its reference is released.
        return buf.view()


def _snapshot_resampled_locked(
    recorder: Recorder,
    buf: GrowableRecordingBuffer,
    effective_sr: int,
    target_sr: int,
) -> np.ndarray:
    """Incremental-resample branch of :func:`take_snapshot`.

    Resamples ONLY the samples that arrived since the previous call
    (absolute cursor ``_cached_native_chunk_count`` — repurposed from
    chunk-count to SAMPLE-count by the contiguous-storage change), appends
    them to the incremental cache, and returns a view over the cache's
    filled prefix. Caller holds ``recorder._lock``.
    """
    end_abs = buf.appended_samples_total
    start_abs = max(int(recorder._cached_native_chunk_count), buf.evicted_samples_total)
    cached_len = int(getattr(recorder, "_cached_resampled_len", 0))
    if end_abs <= start_abs:
        # Nothing new to resample — return the current prefix view.
        return recorder._cached_resampled[:cached_len]
    raw_new = buf.sample_range(start_abs, end_abs)
    # if resampling fails, drop the bad chunk rather
    # than appending native-rate audio that would corrupt the
    # streaming transcription.
    try:
        new_resampled = recorder._resample_chunk(raw_new, effective_sr, target_sr)
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
    # streaming.py's zero-gate keys on exactly this identity.
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
    # imported (it imports this module near the top, before the constants
    # are defined). By deferring the import to call time we read the
    # constants from the fully-loaded module.
    from voice_typer.server import recording as _recording_pkg
    from voice_typer.server.recording.recorder import (
        _AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S,
        _EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S,
    )

    recorder._recording_event.clear()
    # STREAM-FIX (Task 6): set _user_stop_pending before stream.stop() so
    # the audio callback's early-return guard suppresses the false "Stream
    # finished unexpectedly" warning. The stop() path sets this flag;
    # discard() was missing it, so cancelling a recording via the Cancel
    # button still fired the warning. This mirrors the stop() contract:
    # any code path that intentionally stops the stream must set
    # _user_stop_pending first so the callback knows the stream end is
    # expected, not a crash.
    recorder._user_stop_pending = True
    # 17-H-: increment stop_generation for symmetry with stop() so
    # any stale disconnect handler launched from the audio callback
    # (during discard's stream.stop()) bails out instead of racing with
    # the teardown — matching stop()'s HOTKEY-CRASH guard.
    recorder._stop_generation += 1
    # guard _effective_sr reset with the lock so a concurrent
    # snapshot() reader sees a consistent value.
    with recorder._lock:
        recorder._effective_sr = recorder.config.sample_rate
        # reset ``_buffer_sr`` to ``None`` so the next session
        # starts clean (matches the ``_effective_sr`` reset above, but
        # uses ``None`` so stop()/snapshot() fall back to ``_effective_sr``
        # via the ``_buffer_sr or _effective_sr`` idiom until
        # ``_process_audio_chunk`` updates it on the first chunk).
        # Without this, a subsequent ``stop()``/``snapshot()`` would use
        # the previous session's chain rate as the resample source rate —
        # causing a wrong-rate resample on the first chunk of the new
        # session.
        recorder._buffer_sr = None
    recorder._last_rms = 0.0
    recorder._silence_timer = 0.0
    recorder._silence_start_time = None
    recorder._silence_warning_count = 0
    recorder._silence_next_warning_wait = 10.0
    # securely zero cached audio arrays BEFORE reassignment
    # (previously this just dropped the references, leaving the discarded
    # session's voice data in process memory). Factored into
    # ``_secure_clear_caches`` (shared with stop()'s two paths).
    recorder._secure_clear_caches()
    # 17-H-: drain callback + stop + close via _teardown_stream()
    # (shared with stop()). The previous inline stream.stop()/close() here
    # had NO _is_in_audio_callback poll, risking use-after-free or deadlock
    # when ESC-cancel landed during a busy audio callback (which fires
    # ~16×/s). The helper polls for up to 300ms before close() and is
    # idempotent if the stream was already None.
    recorder._teardown_stream()
    # stop the audio worker thread. drain=False because
    # discard() doesn't need the in-flight audio — it's about to clear
    # recorder._buffer anyway. The worker clears the ring buffer and exits
    # after its current chunk (if any). Any chunk the worker appends to
    # recorder._buffer before exiting is cleared below.
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # stop the IPC event worker with drain=False — the recording was
    # cancelled, so queued IPC events (e.g. audio_clip from the discarded
    # audio) don't need to be published. The queue is cleared so the
    # worker exits promptly.
    recorder._stop_event_worker(timeout=_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # CPU-03: stop the device health checker thread (mirrors the event worker).
    # Fire-and-forget (timeout=0.0): the device-health checker is a daemon
    # that sleeps 30s between probes, so joining it almost always times out.
    # Worse, the underlying ``DeviceManager._start_device_health_checker``
    # (in a sibling module we don't own) assigns the thread reference BEFORE
    # calling ``Thread.start()`` without holding a lock — a concurrent
    # discard() that calls ``_stop_device_health_checker()`` can observe the
    # not-yet-started thread and raise
    # ``RuntimeError("cannot join thread before it is started")`` when the
    # timing is tight (the sentinel-driven fast event-worker exit in
    # ``_stop_event_worker`` widened the race window enough to surface the
    # bug under the  hammer). Signalling the stop event without joining
    # eliminates the race: the daemon exits on its next ``wait()`` return
    # (≤30s), and the next ``start()`` sees ``is_alive()==False`` and spawns
    # a fresh checker. Mirrors the ``stop()`` path's fire-and-forget call.
    recorder._stop_device_health_checker(timeout=0.0)
    with recorder._lock:
        # SEC-audit-008: defer buffer zeroing to background daemon
        # thread so discard() returns immediately (the secure clear happens
        # off the hot path).
        _ensure_growable_buffer(recorder)
        _old_buffer = recorder._buffer
        # Contiguous storage: data lives in ONE ndarray, so "swap in a
        # fresh container" is just a fresh empty buffer object (no
        # per-chunk arrays to abandon). The old buffer object is frozen
        # from here on; its backing array is zeroed by the background
        # worker below (``__iter__`` yields every occupied chunk region).
        recorder._buffer = _fresh_recording_buffer_like(recorder, _old_buffer)
        # PERF: zero the running buffered-samples counter — the fresh
        # buffer above is empty, so the counter must be 0 to match.
        # Without this, ``current_duration_seconds`` would continue
        # returning the discarded session's total until the next
        # ``start()`` reset (incorrect for any caller polling between
        # discard() and start()).
        recorder._total_buffered_samples = 0
        _recording_pkg._secure_clear_array_background(_old_buffer)


def start_recording(recorder: Recorder) -> None:
    """Body of :meth:`Recorder.start` (after the ``_start_lock`` permission-gate block).

    Phase 4.5 — extracted from :mod:`.recorder` to shrink the
        3772-LOC ``recorder.py`` god class. The ``with self._start_lock:``
        block (containing the recording-event check + microphone-permission
        pre-flight) stays on ``Recorder.start`` so the source-inspection
        test (``tests/test_recording.py::TestRec5StartLock``) continues to
        pin the lock contract.

        This function runs WITHOUT holding ``_start_lock`` — it is called
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
        conditional — the actual silence-warning state machine uses
        the integer counter ``_silence_warning_count`` (which IS read
        at recording.py:1109). The dead flags were misleading
        maintainers into thinking warning deduplication existed when
    it didn't — see FORENSIC_REVIEW_COMPLETE.md →

        SEC-audit-008: ``_secure_clear_array`` is now actually used
        here to zero cached audio arrays (``_cached_resampled`` and
        ``_cached_no_resample_arr``) before they're dropped. This
        prevents forensic recovery of audio data from process memory
        between sessions.

        CRITICAL — DO NOT RESTRUCTURE (2026-07-20)
        ========================================
        The device-enumeration block below (``last_error``,
        ``selected_device``, ``effective_sr``, the ``for candidate in
        candidates`` loop, the fallback loop, and the
        ``if recorder._stream is None:`` check) MUST stay at this
        function's body scope — OUTSIDE the ``callback`` closure built
        by ``recorder._build_audio_callback()`` above. A previous
        merge accidentally nested this block INSIDE the ``def
        callback()`` closure, which made ``last_error`` a local of
        ``callback`` instead of ``start()``, raising
        ``UnboundLocalError`` on every recording start.

        The device-loop bodies are extracted into
        ``recorder._open_stream_for_candidates`` /
        ``recorder._open_stream_fallback`` (both called from this
        function's scope, OUTSIDE the callback closure), so the
        structural contract above is preserved.

        DO NOT move device enumeration inside the callback closure.
        DO NOT re-add ``set_thread_registry`` — it was merge damage.
    """
    # Lazy import: ``recorder.py`` is still loading when this module
    # is first imported (it imports this module near the top, before
    # the constants are defined). By deferring the import to call
    # time we read the package namespace (mirrors ``discard_recording``).

    # SEC-audit-008: securely zero cached
    # audio arrays before clearing. The cache-clearance block is
    # extracted into ``_secure_clear_session_caches`` so the
    # source-string regression test
    # (``test_recorder_start_except_clause_does_not_swallow_nameerror``)
    # can pin the narrowed handler clause at the helper-method
    # granularity. See the helper's docstring for the full rationale.
    recorder._secure_clear_session_caches()

    # per-session state reset () ──
    recorder._reset_session_state()

    # ── cache config-derived scalars for the audio callback hot path ──
    max_rec = recorder._cache_session_config()

    device = recorder._resolve_device()
    candidates = recorder._same_physical_microphone_candidates(device)

    # build the PortAudio callback closure () ──
    callback = recorder._build_audio_callback()

    # =====================================================================
    # CRITICAL — DO NOT RESTRUCTURE (2026-07-20)
    # =====================================================================
    # The device-enumeration block below (last_error, selected_device,
    # effective_sr, ``for candidate in candidates``, the fallback loop,
    # and the ``if recorder._stream is None:`` check) MUST stay at
    # this function's body scope — this 4-space indent level, OUTSIDE
    # the ``callback`` closure defined above.
    #
    # A previous merge accidentally nested this block INSIDE the
    # ``def callback()`` closure. That made ``last_error`` a local of
    # ``callback``, not ``start()``. When ``start()`` checked ``if
    # last_error is not None:``, Python raised:
    #     UnboundLocalError: cannot access local variable 'last_error'
    #     where it is not associated with a value
    # → recording start crashed on every attempt.
    #
    # The fallback loop (``for candidate in all_candidates``) was also
    # misplaced — trapped inside the preroll-buffer block instead of
    # ``if recorder._stream is None and not used_fallback:``.
    #
    # DO NOT move device enumeration inside the callback closure.
    # The device-loop bodies are now extracted into
    # ``_open_stream_for_candidates`` / ``_open_stream_fallback`` (both
    # called from this function's scope, OUTSIDE the callback closure),
    # so the structural contract above is preserved.
    # DO NOT re-add ``set_thread_registry`` — it was merge damage, not
    # in the original codebase, and referenced a function that did not
    # exist. The ``recording/__init__.py`` stub for it is dead code.
    # =====================================================================
    last_error: Exception | None = None
    selected_device: Any = None
    effective_sr: int = recorder.config.sample_rate
    used_fallback = False

    selected_device, effective_sr, last_error = recorder._open_stream_for_candidates(
        candidates, callback, effective_sr, last_error
    )

    # If all same-name candidates failed, try ALL available input devices
    if recorder._stream is None and not used_fallback:
        selected_device, effective_sr, used_fallback, last_error = recorder._open_stream_fallback(
            candidates, callback, effective_sr, last_error
        )

    if recorder._stream is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("No input device could be opened")

    # ── dynamic buffer sizing (deferred until effective_sr known) ──
    recorder._resize_buffers_for_sample_rate(effective_sr, max_rec)

    # Scale the SPSC ring buffer to ~2s of headroom at the
    # device's effective sample rate. ``_resize_buffers_for_sample_rate``
    # (in ``session_state.py``) already resizes for ~1.0s with a floor
    # of 16 chunks — sufficient for VAD inference spikes (Silero ~1-5ms
    # per chunk on CPU) but tight when the audio worker briefly falls
    # behind on RNNoise (~50ms/chunk * 16 Hz = 800ms/sec of CPU). At
    # 48 kHz / 512-sample blocks, the 1.0s sizing gives only 93 chunks;
    # a 1s worker stall would evict ~93 chunks ~ 1s of speech.
    #
    # Override to 2.0s headroom (floor 64 chunks so a 16 kHz device
    # still gets ~2s — 64 * 512 / 16000 = 2.048s). The per-chunk
    # resample cost (after the retune removal) runs on the worker
    # thread, not the RT callback, so the larger capacity absorbs the
    # extra per-chunk latency without dropping audio.
    #
    # SEC-audit-008: zero each chunk's numpy array BEFORE reassignment
    # so the previous session's audio data doesn't linger in process
    # memory after the deque reference is dropped (mirrors the
    # preroll-buffer / disconnect-handler pattern). Ring buffer items
    # are 5-tuples ``(chunk_copy, frames, time_info, status, perf_ts)``
    # — the numpy array is the first element. Defensive against
    # direct-array items too.
    #
    # ``start_audio_worker_body`` (capture.py:380-384) re-zeros and
    # clears the deque immediately after this, so the reassignment here
    # is the capacity-change vehicle (the clear is redundant but the
    # zeroing is not — once we drop the reference, the underlying
    # float32 arrays survive until GC).
    _uu36_sizing_sr = effective_sr if effective_sr > 0 else recorder.config.sample_rate
    if _uu36_sizing_sr > 0:
        _uu36_new_ring_capacity = max(64, int(_uu36_sizing_sr / _AUDIO_BLOCKSIZE * 2.0))
        for _payload in recorder._ring_buffer:
            _arr = _payload[0] if isinstance(_payload, tuple) else _payload
            if isinstance(_arr, np.ndarray):
                _arr.fill(0)
        recorder._ring_buffer = collections.deque(maxlen=_uu36_new_ring_capacity)

    if selected_device != device and isinstance(selected_device, int):
        # Session-local fallback ONLY: the opened stream uses the
        # fallback device for this recording, but the persisted
        # ``config.microphone`` is left untouched. Auto-writing the
        # fallback here silently replaced the user's selection (or a
        # None "System Default") with an arbitrary concrete device id,
        # which then surfaced as a stale selection after restart.
        # Re-selection happens either by explicit user action or via the
        # renderer's hot-swap fallback (set_config microphone:null).
        log.info(
            "[RECORDING] Selected microphone [%s] failed; using device [%s] "
            "for this session (saved selection unchanged)",
            device,
            selected_device,
        )

    recorder._recording_event.set()

    # the pre-roll filter-chain prepend has been MOVED off the
    # start() thread. Pre-fix, ``_prepend_preroll_to_buffer()`` ran
    # synchronously here (between ``_recording_event.set()`` and
    # ``_start_audio_worker()``), blocking start() for 465ms-4.65s
    # (1s pre-roll × ~93 chunks × 5-50ms RNNoise per chunk). Live
    # audio chunks accumulated in the ring buffer unprocessed for the
    # entire prepend duration. The prepend now runs as a "phase 0"
    # inside ``AudioCallbackDispatcher.audio_worker_loop`` (capture.py)
    # BEFORE the main drain loop — so start() returns immediately
    # after ``_start_audio_worker()``. The ring buffer (sized for 2.0s
    # of headroom per the headroom requirement) absorbs the prepend duration; the worker
    # drains the live backlog as soon as the prepend finishes.

    target_sr = recorder.config.sample_rate
    # The mutable resampler state lives on the .resampling submodule;
    # read it there at call time so tests patching
    # ``voice_typer.server.recording.resampling._resample_poly`` (and
    # ``_resample_poly_error``) are honored.
    from voice_typer.server.recording import resampling as _recording_resampling

    if (
        effective_sr != target_sr
        and _recording_resampling._resample_poly is None
        and _recording_resampling._resample_poly_error is None
    ):
        # Skip the synchronous warm-up when the scipy preloader daemon
        # spawned by ``Recorder.__init__`` (``_register_scipy_preloader``)
        # is still in flight — that thread already owns the import, so
        # blocking the hotkey path here would pay the 1-2s scipy cost a
        # second time. Correctness is unaffected: the resample helpers
        # (``resample_audio`` → ``_get_resample_poly``) load scipy
        # on demand and under a lock if a resample lands before the
        # preloader finishes, so the output bytes are identical either
        # way — only the first-start latency moves off the hotkey thread
        # (worst case it resurfaces once inside a very early stop(), the
        # same place the import cost landed before the preloader existed).
        # The isinstance guard keeps the check deterministic for test
        # doubles: a ``MagicMock`` recorder auto-creates a non-Thread
        # attribute, which falls through to the synchronous warm-up and
        # preserves the historical contract for those tests. When the
        # preloader already exited (``_resample_poly`` still ``None`` —
        # the import failed), warm up synchronously exactly as before so
        # the failure is logged once at start time.
        _preloader = getattr(recorder, "_scipy_preloader_thread", None)
        if isinstance(_preloader, threading.Thread) and _preloader.is_alive():
            log.debug("[RECORDING] scipy preloader in flight — resampler warm-up left to the background thread")
        else:
            # Warm up synchronously when no background preloader is
            # running, so the first stop()/snapshot() resample never
            # pays the scipy import cost.
            recorder.warm_up_resampler()

    # best-effort retune of the AudioProcessor's filter chain
    # to the device's native sample rate. Pre-fix, the start() path
    # REMOVED this retune (deliberately, because the old call could
    # fail silently and leave the chain mistuned on the start()
    # critical path). The per-chunk resample in
    # ``AudioProcessor.process_chunk`` (called from
    # ``audio_pipeline.process_audio_chunk`` with
    # ``input_sample_rate=recorder._effective_sr``) handles the
    # 48 kHz → 16 kHz downsample on the worker thread as the robust
    # fallback. We now re-add the retune inside a try/except that
    # logs-but-continues on failure — unifying the start() and
    # hot-plug paths (``disconnect_handler.retune_audio_processor``)
    # and eliminating the 3× RNNoise resample roundtrip per chunk on
    # 48 kHz devices. The per-chunk resample remains as the fallback
    # if ``set_sample_rate`` raises.
    #
    # Filter-chain correctness is preserved either way:
    #   - Retune succeeds: chain built at ``effective_sr``; live chunks
    #     fed at ``effective_sr`` → no per-chunk resample needed ✓
    #   - Retune fails: chain stays at WHISPER_SAMPLE_RATE (16 kHz);
    #     ``process_chunk`` resamples each chunk 48k→16k before
    #     filtering ✓
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
            "[RECORDING] retune_audio_processor failed on start — per-chunk resample will run on the worker thread",
            exc_info=True,
        )

    # refresh the per-chunk VAD property cache now that
    # ``_effective_sr`` is finalized. The cache lets the
    # 16 Hz audio worker hot path read scalars instead of
    # dispatching 3 property lookups per chunk × 16 Hz = 48/sec.
    recorder._refresh_vad_caches()

    # Start the audio worker thread AFTER ``_recording_event.set()``
    # (so the callback will actually push to the ring buffer). The
    # worker drains the ring buffer and runs the heavy processing
    # pipeline (filter chain, VAD, resample, state machine) off the
    # real-time audio thread. As of  the worker ALSO drains
    # ``_preroll_buffer`` and prepends it to ``_buffer`` as a
    # "phase 0" before entering the main drain loop — so start()
    # returns immediately after this call (no synchronous prepend
    # on the start() thread).
    #
    # REC-2 contract: if ``_start_audio_worker`` OR
    # ``_start_event_worker`` raises, the PortAudio stream we just
    # opened must be rolled back so it doesn't leak. Pre-refactor,
    # the body lived inline in ``Recorder.start`` with a try/except
    # that called ``_teardown_stream`` on any BaseException. The
    # extraction lost the rollback path. The wrapper below restores
    # it: catch BaseException (so ``MemoryError`` / ``KeyboardInterrupt``
    # propagate after cleanup), bump ``_stop_generation`` (so any
    # in-flight disconnect handler bails out instead of racing with
    # the teardown — mirroring ``discard()``'s HOTKEY-CRASH guard),
    # clear the ``_recording_event`` flag (set earlier in this
    # function so the audio callback would push to the ring buffer —
    # without this clear, a failed start leaves the event set and
    # the next ``start()``'s ``is_set()`` early-return fires,
    # masking the retry), tear down the stream, optionally stop the
    # audio worker (only if it was started — i.e. the failure is in
    # ``_start_event_worker``, not ``_start_audio_worker``), then
    # re-raise so the caller sees the original error.
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
    # so the audio worker can enqueue IPC events (e.g. audio_clip)
    # as soon as it begins processing chunks. The event worker is
    # stopped by stop()/discard() — see _stop_event_worker.
    try:
        recorder._start_event_worker()
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
    # worker) so device-disconnect detection doesn't block the hot path.
    recorder._start_device_health_checker()

    # Wire the idle-recording gate. ``Recorder.start`` is the
    # production caller that toggles
    # ``MicrophoneDeviceWatcher.set_idle`` — pre-fix, ``set_idle`` was
    # defined and consumed by the macOS/Linux polling paths but had
    # ZERO production callers, so ``_is_idle`` stayed ``True`` forever
    # and the active 3 s poll cadence never engaged during recording
    # (the watcher idled at 12 s always). The call is placed at the
    # very end of ``start_recording`` so any earlier failure (stream-open,
    # worker spawn, retune) prevents the toggle — matching the
    # ``stop_recording`` contract that ``set_idle(True)`` only runs after
    # a successful stop. The ``None`` guard covers the
    # macOS-without-pyobjc fall-back (``_mic_watcher`` is ``None`` when
    # the watcher failed to start — see ``DeviceManager.__init__``) so
    # this branch is a no-op on hosts where the watcher never came up.
    _mic_watcher = recorder._mic_watcher
    if _mic_watcher is not None:
        _mic_watcher.set_idle(False)


def stop_recording(recorder: Recorder) -> np.ndarray:
    """Stop recording and return the complete audio array.

        Body of :meth:`Recorder.stop` — extracted verbatim (with ``self.X``
    rewritten to ``recorder.X``) by  / Phase 4.5 to shrink the
        ~2748-LOC ``recorder.py`` god class. ``Recorder.stop`` becomes a
        1-line delegator so existing call sites, subclass overrides, and
        ``inspect.getsource`` checks that look for the method on the
        ``Recorder`` class continue to work. There is NO source-inspection
        test contract pinning ``Recorder.stop`` source (verified via
        ``rg "inspect.getsource.*Recorder\\.stop\\b" tests/`` — the matches
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
          5. ``_teardown_stream()`` — 300 ms callback-drain poll, then
             ``stream.stop()`` + ``stream.close()`` (shared with
             ``discard_recording``; see the helper's docstring for the
    PERF- history).
          6. Clear ``_user_stop_pending`` (any future
             ``_stream_finished_callback`` is now a genuine disconnect).
          7. ``_stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S,
             drain=True)`` — drain=True so the last few hundred ms of
             audio (chunks still in the ring buffer) end up in
             ``recorder._buffer`` and are concatenated below
    ().
          8. ``_stop_event_worker(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S,
             drain=True)`` — drains the IPC event queue.
          9. ``_stop_device_health_checker(timeout=0.0)`` — fire-and-
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
         12. ``_prepare_audio(audio, effective_sr)`` — H15: resample from
             scratch (no cache) for the full audio.
         13. ``log.info`` the stop summary (duration, sr, samples, RMS,
             peak, silence_pct, stream/concat/resample/total ms); near-
             silence warning when ``rms < 0.001``.

        The empty-buffer fast path zeros the cached audio arrays
        (``_secure_clear_caches``), resets ``_chunk_count = 0``, and
    returns an empty ``float32`` array —  secure-clear
        contract.

        Critical: ``_buffer_sr`` (the actual rate of the audio in
        ``recorder._buffer``) is captured into a local BEFORE
        ``_secure_clear_caches`` resets it to ``None``. The local is the
        authoritative source rate for the snapshotted audio — the chunks
        in ``_captured_chunks`` were appended at this rate by
        ``_process_audio_chunk``. Using ``_effective_sr`` here (the
        device's native rate) would cause ``_prepare_audio`` to resample
        the already-16 kHz audio a second time (chipmunk voice).
    """
    # Lazy import: ``recorder.py`` is still loading when this module
    # is first imported (it imports this module near the top, before
    # the constants are defined). By deferring the import to call time
    # we read the constants from the fully-loaded module — mirrors
    # ``discard_recording`` and ``start_recording``.
    from voice_typer.server import recording as _recording_pkg
    from voice_typer.server.recording.recorder import (
        _AUDIO_WORKER_JOIN_TIMEOUT_S,
        _EVENT_WORKER_JOIN_TIMEOUT_S,
    )

    # Fast-path ONLY when no worker refs exist. A
    # start()/discard() race can leave ``_recording_event`` cleared but
    # a live worker (a start() spawned it after a concurrent discard
    # already cleared the event). In that state stop() must still stop
    # the worker — otherwise the daemon leaks until process exit (the
    # recorder worker-lifecycle guard surfaces it as a timeout).
    if (
        not recorder._recording_event.is_set()
        and recorder._worker_thread is None
        and recorder._event_worker_thread is None
    ):
        return np.array([], dtype=np.float32)

    stop_started = time.perf_counter()
    recorder._recording_event.clear()

    # HOTKEY-CRASH: increment stop_generation so any stale disconnect
    # handlers from the audio callback know to bail out.
    recorder._stop_generation += 1

    # STREAM-FIX: mark that we're about to call stream.stop()
    # intentionally, so _stream_finished_callback doesn't warn about
    # an "unexpected" disconnect. Cleared after stream.close() below.
    recorder._user_stop_pending = True

    # 17-H-: drain callback + stop + close via _teardown_stream()
    # (shared with discard()). The 300ms callback poll is preserved
    # verbatim — see the helper's docstring/comments for the
    # PERF- history.
    recorder._teardown_stream()
    # STREAM-FIX: clear the user-stop-pending flag now
    # that stream.close() has completed. Any future
    # _stream_finished_callback invocation is now genuinely
    # unexpected (device disconnect).
    recorder._user_stop_pending = False
    stream_ms = (time.perf_counter() - stop_started) * 1000

    # stop the audio worker thread. drain=True so the
    # worker finishes processing any chunks still in the ring buffer
    # -- those chunks end up in recorder._buffer, which we concatenate
    # below. Without this drain, the last few hundred ms of audio
    # (chunks pushed to the ring buffer but not yet processed by the
    # worker) would be lost.
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S, drain=True)

    # cut the worst-case stop() latency from ~5.8s to ~2.4s
    # by using a short 0.1s join for the event worker (down from 2.0s)
    # and fire-and-forget for the device health checker (down from
    # 1.0s join). The event worker drains its tiny queue in <10ms, so
    # 0.1s is generous; the device health checker sleeps 30s between
    # probes so a 1.0s join almost always timed out anyway. Both are
    # daemon threads, so even if they don't exit within the timeout,
    # they cannot block process exit. The audio worker join (2.0s) is
    # unchanged because it must drain up to 64 ring-buffer chunks of
    # in-flight audio (drain=True) to avoid losing the last few
    # hundred ms of the recording.
    # Use the full _EVENT_WORKER_JOIN_TIMEOUT_S (2.0s) so a slow
    # event_bus.publish (e.g. a backed-up TCP subscriber) has time
    # to drain. Pre-fix this was 0.1s which was too short for any
    # publish > 100ms — the daemon was left running and the test
    # contract (drain completes within stop()) was violated.
    recorder._stop_event_worker(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S, drain=True)

    # device health checker fire-and-forget. Pass timeout=0.0
    # so ``_stop_device_health_checker`` only signals the stop event
    # without joining (the daemon thread exits on its next 30s wait()
    # return).
    recorder._stop_device_health_checker(timeout=0.0)

    # snapshot the buffer under the lock, then release the lock BEFORE
    # materializing the returned audio. With contiguous storage there is
    # nothing to concatenate — the recording already IS one contiguous
    # array — so the outside-the-lock step is a single O(N) copy of the
    # filled region (same cost class as the old np.concatenate, but paid
    # once per SESSION instead of once per 250 ms poll). The lock scope
    # stays minimal exactly as before: inside the lock, swap in a fresh
    # empty buffer + capture ``_buffer_sr`` + ``_secure_clear_caches``;
    # outside, export the copy so the audio worker's append path (which
    # acquires the same lock) is never blocked.
    concat_started = time.perf_counter()
    with recorder._lock:
        if not recorder._buffer:
            # securely zero cached audio arrays BEFORE
            # reassignment (previously this just dropped the
            # references, leaving the previous session's voice data
            # in process memory until the numpy allocator reused
            # the block).
            recorder._secure_clear_caches()
            recorder._chunk_count = 0
            # PERF: zero the running buffered-samples counter alongside
            # ``_chunk_count`` so ``current_duration_seconds`` returns
            # 0.0 after the empty-buffer stop() path (the counter
            # would otherwise retain the previous session's total until
            # the next ``start()`` reset).
            recorder._total_buffered_samples = 0
            # idle-recording gate — return to the 12 s idle
            # cadence even on the empty-buffer early-return path so the
            # watcher doesn't stay in the active 3 s mode after a
            # successful stop with no audio captured. Mirrors the
            # ``set_idle(True)`` call before the normal ``return audio``
            # at the end of this function. ``None`` guard for hosts
            # where the watcher never came up.
            _mic_watcher = recorder._mic_watcher
            if _mic_watcher is not None:
                _mic_watcher.set_idle(True)
            return np.array([], dtype=np.float32)
        # Non-empty: normalize legacy containers (hot-swap deque / test
        # injections) to contiguous storage, then swap in a fresh empty
        # buffer INSIDE the lock (O(1)). The old buffer object is captured
        # and frozen — nobody mutates it after this point, so the export
        # below can read it without the lock.
        _ensure_growable_buffer(recorder)
        _old_buffer = recorder._buffer
        recorder._buffer = _fresh_recording_buffer_like(recorder, _old_buffer)
        # PERF: zero the running buffered-samples counter — the fresh
        # buffer above is empty, so the counter must be 0 to match.
        # Without this, ``current_duration_seconds`` would continue
        # returning the snapshot session's total duration until the
        # next ``start()`` reset (incorrect for any caller polling
        # between stop() and start()).
        recorder._total_buffered_samples = 0
        # Critical: capture ``_buffer_sr`` into a local
        # BEFORE ``_secure_clear_caches`` resets it to ``None``.
        # The local is the authoritative source rate for the
        # audio we just snapshotted — the samples in
        # ``_old_buffer`` were appended at this rate by
        # ``_process_audio_chunk``.
        _captured_buffer_sr = recorder._buffer_sr
        # securely zero cached audio arrays BEFORE
        # reassignment (same rationale as the empty-buffer path
        # above; factored into ``_secure_clear_caches`` to avoid
        # duplication across stop()'s two paths and discard()).
        recorder._secure_clear_caches()
    # materialize the contiguous result OUTSIDE the lock so the
    # audio worker (and any other ``recorder._lock`` acquirer) is not
    # blocked. One O(N) copy replaces the old np.concatenate.
    audio = _old_buffer.export_copy()
    concat_ms = (time.perf_counter() - concat_started) * 1000

    # SEC-audit-008 (race fix): enqueue the OLD storage for background
    # zeroing AFTER the copy above has captured its data. Pre-fix, the
    # buffer-clear worker could ``fill(0)`` a chunk between
    # ``list(_old_buffer)`` and the concat, silently truncating the
    # returned audio; the same hazard would exist here if the zeroing was
    # enqueued before ``export_copy()`` read the storage.
    # ``export_copy()`` allocates fresh memory, so zeroing the old
    # storage afterwards cannot affect the returned audio.
    _recording_pkg._secure_clear_array_background(_old_buffer)

    # Log audio statistics for diagnostics
    # Critical: prefer the captured ``_buffer_sr`` (the
    # actual sample rate of the audio that was just concatenated
    # out of ``_buffer``) over ``_effective_sr`` (the device's
    # native rate). When an AudioProcessor is active,
    # ``process_chunk`` resamples each chunk to the chain's
    # construction rate (16 kHz) before appending, so
    # ``_buffer_sr == 16000`` regardless of the device's native
    # rate. Pre-fix, ``stop()`` read ``_effective_sr`` (e.g.
    # 48000) and the subsequent ``_prepare_audio`` call did
    # ``resample_poly(audio, 1, 3)`` — decimating the already-16
    # kHz audio 3:1 → ~5333 samples presented as "16 kHz" →
    # pitched up 3× (chipmunk voice). The captured local is the
    # snapshot taken inside the lock above (before
    # ``_secure_clear_caches`` reset it to ``None``); the
    # ``or recorder._effective_sr`` fallback covers the
    # ``_buffer_sr is None`` case.
    effective_sr = _captured_buffer_sr if _captured_buffer_sr is not None else recorder._effective_sr

    # Pipeline ``_prepare_audio`` with the stats computation below.
    # The resample (``_prepare_audio`` calls ``_resample_audio_impl``
    # → ``resample_poly``) is the most expensive single step in
    # stop() — ~200 ms for 30 s of 16 kHz mono audio, and proportionally
    # more for longer recordings. The stats computation (np.dot for RMS,
    # np.abs + np.sum for silence_pct) is also non-trivial for large
    # buffers (~150 ms for 30-min 16 kHz mono). Running them in parallel
    # (instead of sequentially) cuts the worst-case stop() tail latency
    # by the smaller of the two — typically the stats duration.
    #
    # Safety: ``_prepare_audio`` returns a NEW ndarray (the resampled
    # audio); it does NOT mutate the input. The stats below read
    # ``audio`` (the original, pre-resample array) without writing to
    # it. NumPy operations release the GIL for compute-heavy kernels,
    # so the two threads actually run in parallel on multi-core hosts.
    # The method-call order contract (``secure_clear_caches`` →
    # ``prepare_audio``) is preserved: the thread STARTS
    # ``_prepare_audio`` immediately after the concat (the
    # ``secure_clear_caches`` call inside the lock above already ran).
    # H15: stop() should NOT use cache — resample from scratch for
    # the full audio (the resample thread does this directly).
    resample_started = time.perf_counter()
    _resample_result: dict[str, Any] = {"audio": None, "exc": None}

    def _run_prepare_audio() -> None:
        try:
            _resample_result["audio"] = recorder._prepare_audio(audio, effective_sr)
        except BaseException as exc:  # noqa: BLE001 — re-raised after join
            _resample_result["exc"] = exc

    _resample_thread = threading.Thread(
        target=_run_prepare_audio,
        name="stop-prepare-audio",
        daemon=True,
    )
    _resample_thread.start()

    duration = len(audio) / effective_sr if len(audio) > 0 else 0
    # initialize ``rms``/``peak``/``silence_pct`` BEFORE
    # the conditional below so the later ``log.info(... rms, peak,
    # silence_pct, ...)`` call site (which is also gated by
    # ``len(audio) > 0``) cannot reference an unbound name when
    # pyrefly analyses control flow.
    rms: float = 0.0
    peak: float = 0.0
    silence_pct: float = 0.0
    if len(audio) > 0:
        # AUDIO-NP: use np.dot for RMS in stop() too
        if audio.size:
            flat = audio.reshape(-1)
            rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
            # PERF: allocation-free peak — ``max(|x|) == max(max(x),
            # -min(x))`` — two reductions on the existing ``flat``
            # view, no intermediate ``np.abs(flat)`` array allocated.
            # Mirrors the per-chunk peak in
            # ``AudioPipeline.compute_rms_and_peak``. Pre-fix, this
            # line allocated a ~115 MB ``np.abs(flat)`` transient for
            # a 30-min 16 kHz mono dictation (28.8M float32 samples).
            peak = max(float(flat.max()), -float(flat.min()))
        else:
            peak = 0.0
            rms = 0.0
        # PERF: compute ``np.abs(flat)`` ONCE and reuse it for
        # silence_pct — pre-fix this line allocated a SECOND ~115 MB
        # transient (``np.abs(audio)``) for the silence mask, on top
        # of the peak's ~115 MB allocation above. Combined transient
        # was ~230 MB; now reduced to a single ~115 MB allocation
        # (only the silence mask — peak is allocation-free). ``flat``
        # is a 1-D view of ``audio`` so ``np.abs(flat)`` and
        # ``np.abs(audio)`` are elementwise-identical.
        abs_flat = np.abs(flat) if audio.size else None
        silence_pct = float(np.sum(abs_flat < 0.001) / audio.size * 100) if abs_flat is not None else 0.0
        recorder._last_rms = rms
        # store the full-recording stats so the
        # transcription engine can reuse them instead of recomputing
        # the same RMS/peak/silence_pct on the same audio array
        # (saves 1-3 ms + 3× 1.9 MB transient memory per dictation).
        recorder._last_audio_stats = (rms, peak, silence_pct)
    else:
        recorder._last_rms = 0.0
        recorder._last_audio_stats = (0.0, 0.0, 0.0)
        log.warning("[RECORDING] No audio data captured!")

    # Wait for the resample thread to complete and propagate any
    # exception it raised. The join is bounded by the resample's own
    # runtime (the thread is already running); if the stats above took
    # longer than the resample, the join returns immediately. The
    # ``daemon=True`` flag is a safety net for the case where stop()
    # is interrupted (e.g. Ctrl-C) mid-resample — the daemon thread
    # will not block process exit.
    _resample_thread.join()
    if _resample_result["exc"] is not None:
        raise _resample_result["exc"]
    audio = _resample_result["audio"]
    resample_ms = (time.perf_counter() - resample_started) * 1000

    # AUDIO-PROC: post-capture spectral noise reduction (offline,
    # safe to block).  Runs AFTER resampling so noisereduce
    # operates on the final 16 kHz audio.  ~200 ms for 30 s audio.
    # ADR 0007 §3.8: post-capture noisereduce removed. The real-time
    # NoiseSuppressor filter in the chain handles denoising. The
    # old process_full_audio() call is removed because:
    # 1. It only ran in stop(), so the streaming path missed it.
    # 2. The "first 0.5s is silence" assumption was fragile.
    # 3. noisereduce is no longer a dependency.

    total_ms = (time.perf_counter() - stop_started) * 1000
    if len(audio) > 0:
        log.info(
            "[RECORDING] Audio stopped: duration=%.1fs, sr=%d, samples=%d, "
            "RMS=%.6f, peak=%.6f, silence=%.1f%% | "
            "stream=%.0fms concat=%.0fms resample=%.0fms total=%.0fms",
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

    # idle-recording gate — return to the 12 s idle cadence
    # now that the recording has been stopped (stream torn down,
    # workers joined, buffer snapshotted). Mirrors the
    # ``set_idle(False)`` call at the end of ``start_recording`` so
    # the watcher's macOS/Linux poll cadence widens from the active
    # 3 s back to the idle 12 s between recordings — saving the
    # 10–50 ms CoreAudio round trip per poll when no recording is
    # in flight. The ``None`` guard covers hosts where the watcher
    # never came up (macOS-without-pyobjc fall-back).
    _mic_watcher = recorder._mic_watcher
    if _mic_watcher is not None:
        _mic_watcher.set_idle(True)

    return audio


np = lazy_module("numpy")
