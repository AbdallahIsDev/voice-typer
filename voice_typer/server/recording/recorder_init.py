"""``RecorderInitMixin`` — owns ``Recorder``'s construction helpers.

Phase 4.5 further-split: ``Recorder.__init__`` was 370 LOC
declaring 60+ instance attributes. The device / disconnect-handler /
collaborator-construction block (~88 LOC) is extracted here as
:meth:`RecorderInitMixin._setup_device_state_and_collaborators`.
``Recorder.__init__`` calls the mixin method at the appropriate point
(after the basic Recorder state — ``_recording_event``, ``_stream``,
``config``, ``_lock``, ``_worker_thread`` etc. — is initialized, since
the collaborators' ``__init__`` methods reference those attributes
through the back-reference).

Further-split: the focused ``_init_*`` construction helpers (core
session state, locks and flags, XRUN tracking, sample-rate/chunk
scalars, snapshot caches, error counters, VAD, preroll, ring buffer +
worker handles, event queue, stream format state, silence detection,
scipy-preloader registration) also live on this mixin. The mixin
pattern keeps ``inspect.getsource(Recorder._init_locks_and_flags)``-
style checks working (``getsource`` reads the module where the
function is DEFINED, and the bound name still resolves on ``Recorder``
through inheritance), while ``recorder.py`` keeps only the compact
``__init__`` that sequences the calls in the original construction
order.

What is NOT moved here
----------------------
The scipy-preloader registration block at the tail of ``__init__`` is
NOT moved: it depends on ``self._thread_registry`` (already set earlier
in ``__init__``) and the package-level
``_recording_pkg._scipy_preloader_thread`` / ``_start_scipy_preloader``
helpers, but it is logically a performance optimization (not device /
health-checker state). Keeping it in ``__init__`` also keeps the
``__init__`` source-inspection contract stable.

Source-inspection compatibility
-------------------------------
The ``__init__`` source-inspection regression tests
(``test_cache_key_includes_dtype_and_sample_rates``,
``test_start_lock_exists``, ``test_init_declares_warning_bookkeeping_attrs``,
``test_init_does_not_declare_dead_in_callback``,
``test_is_in_audio_callback_still_exists``) check for literals that live
in the EARLY part of ``__init__`` (``_cached_resample_key``,
``_start_lock``, ``_is_in_audio_callback``, ``_in_callback`` absence,
``_last_seen_dropped_ring_chunks``, ``_ring_overflow_warn_ts``) — none
of which are in the extracted section. The literals that ARE in the
extracted section (``_stop_generation``, ``_user_stop_pending``,
``_disconnect_handler_lock``, ``_disconnect_handler_running``,
``DeviceManager(self)``, ``DisconnectHandler(self)``,
``AudioPipeline(self)``, ``AudioCallbackDispatcher(self)``,
``StreamLifecycle(self)``, ``SessionState(self)``,
``_prewarm_device_cache()``) are NOT pinned by any source-inspection
test, so moving them out of ``__init__``'s source is safe.

Patch-path compatibility
------------------------
Tests that access these as instance attributes
(``recorder._stop_generation = 0`` /
``isinstance(recorder._disconnect_handler_lock, type(threading.Lock()))``
etc.) keep working unchanged — the mixin methods run during
``__init__`` and set the same attributes on the same ``self``.

Cross-module name resolution
----------------------------
Recorder-owned module constants (``DEFAULT_MAX_BUFFER_CHUNKS``,
``_XRUN_WINDOW_MAXLEN``, ``_AUDIO_RING_BUFFER_CAPACITY``) stay defined
in :mod:`.recorder` (tests import them from there) and are read here at
CALL time via function-level imports — by the time any of these
helpers runs, ``recorder`` is fully loaded (they only run from
``Recorder.__init__``).
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE, WHISPER_SAMPLE_RATE
from voice_typer.server._lazy_import import lazy_module

from . import _recorder_split

if TYPE_CHECKING:
    from collections.abc import Callable

    from voice_typer.server.config import Config

    from .recorder import _EventWorkerStopSentinel

# Lazy proxies — same cold-start pattern as recorder.py / format.py:
# importing this module must NOT pull numpy / sounddevice into
# sys.modules; the real import happens on first attribute access
# (first Recorder construction).
np = lazy_module("numpy")
sd = lazy_module("sounddevice")

# All submodules use the package-level logger so log records propagate
# to ``caplog.at_level(..., logger="voice_typer.server.recording")`` in
# tests.
log = logging.getLogger("voice_typer.server.recording")


class RecorderInitMixin:
    """Mixin: extract ``Recorder.__init__``'s device / collaborator setup.

    ``Recorder`` inherits from this mixin so the device / disconnect-
    handler state declarations + collaborator constructions
    (``DeviceManager`` / ``DisconnectHandler`` / ``AudioPipeline`` /
    ``AudioCallbackDispatcher`` / ``StreamLifecycle`` / ``SessionState``)
    + device-cache prewarm can live in a focused helper method instead
    of bloating ``__init__``. The mixin assumes the host class has
    already initialized the basic Recorder state (``config``,
    ``_recording_event``, ``_stream``, ``_lock``, ``_thread_registry``,
    etc.) — the contract is documented on
    :meth:`_setup_device_state_and_collaborators`.
    """

    def _setup_device_state_and_collaborators(self) -> None:
        """Initialize device / disconnect-handler state + construct collaborators.

        Called from ``Recorder.__init__`` AFTER the basic Recorder
        state (``config``, ``_recording_event``, ``_stream``,
        ``_lock``, ``_thread_registry``, ``_effective_sr``,
        ``_buffer_sr``, VAD caches, preroll buffer, ring buffer,
        worker thread state, ``_actual_channels``, ``_mono_scratch_local``)
        is initialized. The collaborators' ``__init__`` methods store a
        back-reference to ``self`` and do NOT touch recorder state at
        construction time, so they can be instantiated as soon as the
        basic state is ready.

        Sets up:
          - ``_stop_generation`` / ``_user_stop_pending`` — disconnect
            handler bouncer + stream-finished-callback disambiguation.
          - ``_disconnect_handler_lock`` / ``_disconnect_handler_running``
            — single-flight guard for disconnect-handler thread spawns.
          - ``_devices`` (``DeviceManager``) — owns device enumeration,
            hot-swap, mic-watcher, health-checker thread.
          - ``_disconnect_handler`` (``DisconnectHandler``) — owns the
            ~175-LOC stream-restart block (called from
            ``_handle_device_disconnect`` under
            ``_stream_lifecycle_lock``).
          - ``_audio_pipeline`` (``AudioPipeline``) — owns the six
            named helpers split out of ``_process_audio_chunk``.
          - ``_capture`` (``AudioCallbackDispatcher``) — owns the
            audio worker main loop + the RT callback dispatch body.
          - ``_stream_lifecycle`` (``StreamLifecycle``) — owns the
            PortAudio stream-open candidate loop + teardown body.
          - ``_session_state`` (``SessionState``) — owns per-session
            state reset, config-derived scalar caching, secure-clear,
            buffer resizing, preroll prepend.
          - Calls ``_prewarm_device_cache()`` to spawn a best-effort
            daemon thread that populates
            ``DeviceManager._device_list_cache`` ahead of the first
            ``start()`` call.
        """
        # HOTKEY-CRASH: generation counter incremented in stop() so stale
        # device-disconnect handlers (launched from the audio callback) can
        # detect they're operating on an already-stopped stream and bail out
        # instead of racing with start()/stop().
        self._stop_generation: int = 0
        # STREAM-FIX: flag set by stop() BEFORE stream.stop() so
        # _stream_finished_callback can distinguish "user pressed stop"
        # (expected, no warning) from "device disconnected" (unexpected,
        # warn). Previously the callback checked _recording_event, but
        # stop() clears that flag BEFORE calling stream.stop() — so the
        # callback always saw is_set()==False and warned on every stop.
        self._user_stop_pending: bool = False

        # Medium: single-flight guard for the disconnect-handler
        # thread spawns. Three sites spawn ``_handle_device_disconnect``
        # on a fresh daemon thread (the audio callback's zero-fill
        # detector, ``_stream_finished_callback``, and the device
        # health-checker loop). Without a guard, a flapping device
        # (BT mic reconnecting repeatedly) can spawn multiple handler
        # threads concurrently — they race on ``_stream_lifecycle_lock``
        # and the stream-restart block. The guard ensures only ONE
        # handler thread is running at a time; additional spawns while
        # the first is running are no-ops (the existing handler will
        # complete the restart or hit the retry budget).
        self._disconnect_handler_lock = threading.Lock()
        self._disconnect_handler_running = False

        # Local import to avoid a circular import at module load time
        # (``recorder.py`` imports this mixin at the top of its class
        # body, and the collaborator modules import symbols from
        # ``recording`` package). The local import resolves the
        # collaborator classes lazily on first call (which happens
        # during ``Recorder.__init__``).
        from .audio_pipeline import AudioPipeline as _AudioPipeline
        from .capture import AudioCallbackDispatcher as _AudioCallbackDispatcher
        from .device_manager import DeviceManager as _DeviceManager
        from .disconnect_handler import DisconnectHandler as _DisconnectHandler
        from .session_state import SessionState as _SessionState
        from .stream_lifecycle import StreamLifecycle as _StreamLifecycle

        # AUDIO-HOT: hot-plug device disconnect handling
        # Phase 4.5: the 12 device-related state attrs +
        # MicrophoneDeviceWatcher lifecycle live on
        # ``DeviceManager`` (see ``device_manager.py``). The historical
        # read/write property shims on ``Recorder`` were removed — all
        # consumers (KEEP-methods, tests) access the state through
        # ``recorder._devices.<attr>``.
        #
        # The DeviceManager is constructed AFTER the basic Recorder
        # state is initialized (``_recording_event``, ``_stream``,
        # ``config``, etc.) so its ``__init__`` can register the
        # MicrophoneDeviceWatcher callback against its own
        # ``_invalidate_device_cache`` method.
        self._devices: _DeviceManager = _DeviceManager(self)

        # ``DisconnectHandler`` owns the ~175-LOC stream-restart
        # block previously inlined in ``_handle_device_disconnect``. The
        # bouncer checks + ``_stream_lifecycle_lock`` acquisition +
        # re-checks STAY on ``Recorder._handle_device_disconnect`` so
        # the source-inspection regression tests continue to pin
        # the lock-scope invariant (see
        # ``tests/test_recorder_worker_lifecycle.py``).
        # ``AudioPipeline`` owns the six named helpers split out of
        # ``_process_audio_chunk`` in a previous session. ``Recorder``
        # keeps 1-line delegator methods on each helper name so existing
        # call sites and ``inspect.getsource`` checks continue to work.
        # Both collaborators store a back-reference to ``self`` and do
        # NOT touch recorder state at construction time, so they can be
        # instantiated as soon as ``self._devices`` is ready.
        self._disconnect_handler: _DisconnectHandler = _DisconnectHandler(self)
        self._audio_pipeline: _AudioPipeline = _AudioPipeline(self)
        # Phase 4.5: three new collaborators constructed here
        # in the same back-reference pattern as ``_audio_pipeline`` /
        # ``_disconnect_handler`` above. Each is purely a collaborator —
        # stores ``self`` and reads/writes ``self.X`` for shared state —
        # so they can be instantiated as soon as ``self._audio_pipeline``
        # is ready. Construction order is harmless: each ``__init__`` only
        # stores the back-reference and does not touch other ``self.X``
        # state. The chosen order mirrors the dependency direction
        # (callback → lifecycle → session) for readability.
        self._capture: _AudioCallbackDispatcher = _AudioCallbackDispatcher(self)
        self._stream_lifecycle: _StreamLifecycle = _StreamLifecycle(self)
        self._session_state: _SessionState = _SessionState(self)

        # PERF: pre-warm the device-list cache on a background daemon
        # thread so the start() hotkey critical path doesn't pay the
        # 50-200ms PortAudio enumeration cost on the first recording.
        # ``DeviceManager._refresh_device_list`` is the cached path
        # (30s TTL, OS-event-invalidated); without pre-warming, the
        # first ``start()`` after app launch would block on
        # ``sd.query_devices()`` even though ``Recorder`` was
        # constructed seconds earlier. The thread is best-effort —
        # if PortAudio is unavailable (headless CI, no audio HW), the
        # cache stays empty and ``start()`` falls back to direct
        # ``sd.query_devices()`` calls (no regression).
        self._prewarm_device_cache()

    # ── Focused ``_init_*`` construction helpers ─────────────────────
    #
    # Moved verbatim from :mod:`.recorder` (further-split): each helper
    # owns ONE construction concern and lands every attribute on
    # ``self`` exactly as before, in the same construction order —
    # zero behavior change. ``Recorder.__init__`` sequences them.

    def _init_core_session_state(
        self,
        config: Config,
        audio_processor: Any | None,
        thread_registry: Any | None,
    ) -> None:
        """Config / audio-processor / thread-registry backrefs, the
        stream slot, and the contiguous recording buffer."""
        from .recorder import DEFAULT_MAX_BUFFER_CHUNKS

        self.config = config
        self._audio_processor = audio_processor  # AudioProcessor or None
        # THREAD-REGISTRY: optional central registry for shutdown
        # coordination. When provided, the audio-worker thread (and the
        # module-import-time scipy-preloader thread, if still alive) is
        # registered so ``shutdown_all()`` can signal and join it during
        # ``VoiceTyperApp.quit()``. When ``None`` (e.g. in unit tests),
        # behavior is unchanged — threads are still tracked locally via
        # ``self._worker_thread`` and stopped by ``_stop_audio_worker()``.
        self._thread_registry = thread_registry
        self._stream: sd.InputStream | None = None
        # Contiguous recording storage (replaces the deque-of-chunks).
        # ONE pre-allocated growable float32 ndarray: appends copy each
        # chunk in under ``_lock`` (~16 Hz small memcpys on the audio
        # worker), snapshots are O(1) views, and stop() hands off an
        # already-contiguous array. ``maxlen`` keeps the CHUNK-count
        # semantics of the old ``deque(maxlen=DEFAULT_MAX_BUFFER_CHUNKS)``
        # (duration cap + backpressure detection); see
        # :class:`GrowableRecordingBuffer` for the full capacity policy.
        # Typed ``Any`` because the mic hot-swap path transiently swaps in
        # a plain ``collections.deque`` mid-session; every reader
        # normalizes via ``_ensure_growable_buffer`` under the lock.
        self._buffer: Any = _recorder_split.GrowableRecordingBuffer(
            maxlen=DEFAULT_MAX_BUFFER_CHUNKS,
            nominal_sample_rate=config.sample_rate,
            on_extra_eviction=self._note_buffer_capacity_eviction,
        )
        # PERF: running total of buffered samples (sum of ``len(chunk)``
        # across ``_buffer``). Maintained as an O(1) counter incremented
        # under ``_lock`` in ``AudioPipeline.append_to_buffer_locked`` so
        # ``current_duration_seconds`` (polled at 4 Hz by the streaming
        # thread) doesn't have to iterate the whole deque — previously
        # each poll paid an O(chunks) ``sum(int(c.shape[0]) for c in
        # buffer)`` reduction, which on a 30-min dictation at ~16 Hz
        # chunk arrival summed over ~28k chunks per poll. Reset to 0 in
        # ``reset_session_state`` (start) and in ``stop()`` / ``discard()``
        # alongside the buffer swap.
        self._total_buffered_samples: int = 0

    def _init_locks_and_flags(self) -> None:
        """Create the four synchronization primitives + force-closed flag."""
        self._lock = threading.Lock()
        # serializes ``start()`` against ``discard()`` so a
        # concurrent toggle-thread + ESC-cancel-thread + auto-stop
        # Timer thread cannot race on the per-session state reset
        # (buffer.clear, _chunk_count=0, etc.) and stream-start. The
        # lock is acquired at the top of ``start()`` / ``discard()``
        # and released before the method returns; it is NEVER held
        # across ``thread.join()`` or ``_process_audio_chunk`` (same
        # invariant as ``_worker_lifecycle_lock``). Reentrant-safe
        # under ``start()→stop()`` because each method acquires +
        # releases in a ``with`` block. Tests in
        # ``tests/test_retry_regressions.py::TestStartLockRuntime``
        # assert the lock is unheld before/after each method.
        self._start_lock = threading.Lock()
        # serializes the read-check-create-start sequence in
        # ``_start_audio_worker`` / ``_start_event_worker`` and the
        # read-check-clear-join-unregister sequence in the
        # corresponding ``_stop_*`` methods so concurrent
        # start()/stop()/discard() (toggle thread, auto-stop Timer
        # thread, ESC-cancel thread, device-disconnect handler thread)
        # cannot race on ``self._worker_thread`` /
        # ``self._event_worker_thread``. This is a SEPARATE lock from
        # ``self._lock`` — never hold ``self._lock`` across
        # ``thread.join()`` or ``_process_audio_chunk`` would deadlock
        # when the worker tries to acquire ``self._lock`` for the
        # buffer append.
        self._worker_lifecycle_lock = threading.Lock()
        # serializes stream teardown (``_teardown_stream``)
        # against the stream-restart block of
        # ``_handle_device_disconnect`` so a concurrent ``stop()`` /
        # ``discard()`` cannot mutate ``self._stream`` mid-flight of
        # the disconnect handler's restart, and vice-versa.
        self._stream_lifecycle_lock = threading.Lock()

        # force-closed flag set by ``shutdown_controller`` when
        # ``recorder.stop()`` / ``recorder.discard()`` times out and the
        # worker thread is leaked (still touching the PortAudio stream).
        # ``shutdown_mic_watcher`` reads this flag at the top and
        # short-circuits, so a subsequent cleanup call cannot race the
        # leaked worker (PortAudio is not safe for concurrent stream
        # operations from multiple threads). Previously this was a
        # write-only dead state (``shutdown_controller`` set it under
        # ``contextlib.suppress`` but no one read it); the read side implements
        # the read side and the suppress wrapper is now unnecessary.
        self._force_closed: bool = False

    def _init_xrun_tracking(self) -> None:
        """XRUN / clipping counters, the threshold callback slot, the
        rolling xrun-timestamp window, and the recording gate event."""
        from .recorder import _XRUN_WINDOW_MAXLEN

        # XRUN and clipping tracking
        self._xruns: int = 0
        self._clip_count: int = 0
        self._peak: float = 0.0
        self._last_clip_log_time: float = 0.0
        # Item 1: xrun notification callback — set by VoiceTyperApp
        # to receive a notification when xrun count exceeds threshold.
        self.on_xrun_threshold: Callable[[int], None] | None = None
        self._xrun_threshold: int = 10  # notify after this many xruns
        # rolling window of xrun timestamps for rate-limited logging
        self._xrun_timestamps: collections.deque = collections.deque(maxlen=_XRUN_WINDOW_MAXLEN)
        self._recording_event = threading.Event()
        # removed dead ``_in_callback`` field — it
        # was declared here but never set, cleared, or read anywhere in
        # the codebase. The actual in-flight-callback guard is
        # ``_is_in_audio_callback`` (declared in ``_init_preroll_state``).

    def _init_sample_rate_and_chunk_state(self, config: Config) -> None:
        """Sample-rate scalars (device-rate + buffer-rate) and the
        per-chunk VAD property cache + RMS/chunk counters."""
        self._effective_sr: int = config.sample_rate
        # Critical: the actual sample rate of the audio currently
        # held in ``self._buffer``. Set by ``_process_audio_chunk`` after
        # ``AudioProcessor.process_chunk`` resamples to the chain's
        # construction rate (typically 16 kHz) — so when a processor is
        # attached, ``_buffer_sr == 16000`` regardless of the device's
        # native rate (``_effective_sr`` may be 48000). When no
        # processor is attached, ``_buffer_sr == _effective_sr``.
        # ``stop()`` and ``snapshot()`` (via ``_recorder_split``) read
        # this to avoid double-resampling already-resampled audio.
        # Reset to ``None`` by ``start()``, ``stop()``, and ``discard()``
        # (the latter two via ``_recorder_split``) so a fresh session
        # starts clean. The ``None`` sentinel makes the
        # ``_buffer_sr or _effective_sr`` fallback idiom work before the
        # first chunk arrives.
        self._buffer_sr: int | None = None
        # per-chunk VAD property cache. The audio worker hot path
        # (16 Hz) previously evaluated ``self._vad_enabled and
        # self._use_silero_vad and self._silero_available`` on every
        # chunk — 3 property-lookup dispatches × 16 Hz = 48 lookups/sec
        # for values that only change on ``on_config_changed()``. The
        # cache is refreshed at ``start()`` time, on every
        # ``on_config_changed()`` call, and lazily inside
        # ``_process_audio_chunk`` when ``_buffer_sr`` changes.
        self._cached_vad_enabled: bool = False
        self._cached_use_silero_vad: bool = False
        self._cached_silero_available: bool = False
        # Cached (up, down) integer ratio for the VAD
        # resample path (post-process_chunk rate → 16 kHz). ``None``
        # means no resample is needed (``_buffer_sr`` is already
        # 8000/16000). Avoids per-chunk ``math.gcd`` recomputation.
        self._cached_vad_resample_up_down: tuple[int, int] | None = None
        self._cached_vad_resample_sr: int | None = None
        self._last_rms: float = 0.0
        self._chunk_count: int = 0

    def _init_snapshot_caches(self) -> None:
        """Snapshot resample/no-resample caches + the last-audio-stats slot."""
        # H15/M8: Cached resampled prefix for snapshot() to avoid O(n²) resampling
        self._cached_resampled: np.ndarray = np.array([], dtype=np.float32)
        # Contiguous storage: ``_cached_resampled`` OWNS a geometrically
        # grown capacity buffer and ``_cached_resampled_len`` tracks the
        # filled prefix. (Previously the attribute was rebuilt via
        # segment-list concatenation on demand.)
        self._cached_resampled_len: int = 0
        self._cached_native_chunk_count: int = 0
        # cache key must include the audio dtype + sample rates
        # so a float32 vs int16 mismatch (theoretically possible if the
        # PortAudio stream is reconfigured mid-session) doesn't return
        # the wrong cached prefix. We track (dtype, src_sr, dst_sr) and
        # invalidate the cache on any change.
        self._cached_resample_key: tuple = ()
        # cache the no-resample concatenation result so
        # repeated snapshots with no new chunks don't repeat the
        # np.concatenate.  Invalidated whenever the buffer length
        # changes (i.e. a new chunk arrived).
        self._cached_no_resample_len: int = -1
        self._cached_no_resample_arr: np.ndarray | None = None
        # mirror the resample-path segment-list + lazy-concat
        # optimization for the no-resample branch. Previously the cache
        # was keyed on ``buf_len`` only and missed on every poll (the
        # streaming thread polls at 4 Hz while the audio worker appends
        # at 16 Hz → buf_len always differs), so the cache-miss path
        # ran ``np.concatenate(chunks, axis=0).reshape(-1)`` — rebuilding
        # the full array every poll (~460 MB/s memcpy churn on a 30-min
        # 16 kHz mono dictation). We now keep the cached prefix as a
        # *list* of segments (one per snapshot that saw new chunks) and
        # only re-concatenate when the list changes (``_ensure_no_resample_concat``
        # in :mod:`._recorder_split`). Snapshots that see no new chunks
        # reuse the cached concat (zero memcpy). ``_cached_no_resample_len``
        # is repurposed as the count of buffer chunks already in the
        # segment list (with ``-1`` retained as the "invalidated"
        # sentinel so existing ``_secure_clear_caches`` /
        # ``reset_session_state`` callers — which we cannot modify —
        # keep working unchanged).
        self._cached_no_resample_segments: list[np.ndarray] = []
        self._cached_no_resample_concat_dirty: bool = False
        # cache of (rms, peak, silence_pct) from the most
        # recent stop() call, so the transcription engine can reuse
        # them instead of recomputing on the same audio array.
        self._last_audio_stats: tuple[float, float, float] | None = None

        # snapshot() resample-path segment list + lazy concat.
        # Previously every snapshot that saw new chunks re-concatenated
        # the *entire* cached prefix (``np.concatenate([cached, new])``)
        # -- an O(N) memcpy where N grows linearly with session length.
        # At 16 Hz chunk arrival x 30 min x 1.9 MB/chunk-prefix, this
        # summed to hundreds of MB-GB of redundant memcpy per session.
        # We now keep the cached prefix as a *list* of resampled
        # segments and only materialize a contiguous ndarray when the
        # caller actually needs one (``_ensure_resampled_concat`` in
        # :mod:`._recorder_split`). The dirty flag tracks whether the
        # segments list has changed since the last concat; snapshots
        # that see no new chunks reuse the cached concat (zero memcpy).
        self._cached_resampled_segments: list[np.ndarray] = []
        self._cached_resampled_concat_dirty: bool = False

    def _init_error_counters(self) -> None:
        """Per-session error counters (reset by ``start()`` each session)."""
        # per-session error counters. Previously these were
        # lazily initialized via ``getattr(self, "_dropped_chunks", 0)``
        # in the audio callback / RMS callback and never reset in
        # ``start()`` -- so a long-running app accumulated error counts
        # across sessions, masking per-session regressions in tests
        # and dashboards. Declare them in ``__init__`` and reset them
        # in ``start()`` so each session starts from zero.
        self._dropped_chunks: int = 0
        self._rms_callback_error_count: int = 0

    def _init_vad(self, config: Config) -> None:
        """Construct the ``VadProcessor`` and log raw-recording mode."""
        from voice_typer.server.vad_processor import VadProcessor

        # VAD state machine with hysteresis.
        # the VAD state machine, Silero integration, and
        # auto-calibration logic were extracted to ``VadProcessor``
        # (see ``voice_typer/server/vad_processor.py``). ``Recorder``
        # owns a single ``self._vad`` instance and delegates VAD calls
        # to it. The historical ``self._vad_*`` attribute shims were
        # removed — consumers access the state through
        # ``self._vad.<attr>`` directly.
        self._vad: VadProcessor = VadProcessor(config)
        if not self._vad.vad_enabled:
            log.info("[RECORDING] VAD disabled — all audio enhancements off (raw recording mode).")

    def _init_preroll_state(self, config: Config) -> None:
        """Pre-roll circular buffer sizing + the in-flight-callback guard."""
        # ADR 0007 §3.5: AGC instance variables deleted (replaced by
        # Compressor filter in the audio filter chain).

        # AUDIO-PRE: pre-roll circular buffer (captures audio before
        # recording officially starts to reduce cold-start latency).
        # Configurable via config.pre_roll_buffer_seconds (0 = disabled).
        #
        # the deque maxlen MUST be sized against the device's
        # *effective* sample rate, not config.sample_rate (16kHz). At
        # 48kHz the same 512-sample blocksize fires 3× more often, so a
        # 1-second pre-roll needs 3× the chunk capacity. The placeholder
        # sizing below uses config.sample_rate as a safe default for the
        # common 16kHz case; start() re-sizes the deque once
        # _effective_sr is known (after the device loop succeeds) using
        # the values cached in _preroll_seconds / _preroll_blocksize.
        # pre_roll_buffer_seconds is a Config dataclass field
        # (config.py, default 0.0) — always present on a real Config
        # instance, so the getattr fallback could never fire and was
        # dead-defensive code. The ``or 0`` guard is preserved because
        # 0.0 is a valid "disabled" value and float(0.0 or 0) == 0.0
        # is a no-op equivalence.
        preroll_seconds = float(config.pre_roll_buffer_seconds or 0)
        sample_rate = int(config.sample_rate or WHISPER_SAMPLE_RATE)
        # cache these so start() can recompute the deque maxlen
        # using _effective_sr without re-reading config (which the audio
        # callback does not touch).
        self._preroll_seconds: float = preroll_seconds
        self._preroll_blocksize: int = _AUDIO_BLOCKSIZE  # matches sd.InputStream blocksize
        self._preroll_buffer: collections.deque = collections.deque(
            maxlen=int(preroll_seconds * sample_rate / _AUDIO_BLOCKSIZE) + 2 if preroll_seconds > 0 else 0
        )
        self._preroll_active: bool = preroll_seconds > 0  # only capture when enabled

        # guard flag for in-flight audio callback
        self._is_in_audio_callback: threading.Event = threading.Event()

    def _init_ring_and_worker_handles(self) -> None:
        """SPSC ring buffer + audio-worker thread/event handles + the
        ring-overflow warning bookkeeping."""
        from .recorder import _AUDIO_RING_BUFFER_CAPACITY

        # SPSC ring buffer for callback → worker handoff.
        # The audio callback (single producer) pushes (indata_copy,
        # frames, time_info, status, perf_timestamp) tuples; the audio
        # worker thread (single consumer) pops and processes them.
        # collections.deque is atomic for append/popleft under CPython's
        # GIL, so no lock is needed on the ring buffer itself.
        self._ring_buffer: collections.deque = collections.deque(maxlen=_AUDIO_RING_BUFFER_CAPACITY)
        # Worker thread that drains _ring_buffer and runs the heavy
        # audio processing pipeline (filter chain, VAD, resample, state
        # machine). Started by start(), stopped by stop()/discard().
        self._worker_thread: threading.Thread | None = None
        self._worker_stop_event: threading.Event = threading.Event()
        self._worker_wake_event: threading.Event = threading.Event()
        # Counter for chunks dropped because the ring buffer was full
        # (worker couldn't keep up). Surfaced by the worker thread via
        # ``_surface_ring_overflow_warning`` (rate-limited WARNING) and
        # post-recording by ``RecordingController._stop_impl``.
        self._dropped_ring_chunks: int = 0
        # Captures the most recent exception raised inside
        # ``AudioCallbackDispatcher.dispatch_callback_body`` (the
        # PortAudio RT-callback body). PortAudio silently aborts the
        # stream when the callback raises, which surfaces to the user
        # as a "device disconnect" — a misdiagnosis. The body now
        # catches the exception, stores it here, and re-raises (so
        # PortAudio still aborts). ``_stream_finished_callback`` reads
        # this attribute, logs the true cause at ERROR with full
        # traceback, and clears it. Read/written atomically under the
        # GIL (single assignment of an attribute reference).
        self._last_callback_error: Exception | None = None
        # Real-time ring-overflow WARNING bookkeeping (see
        # ``_surface_ring_overflow_warning``). ``_last_seen_dropped_ring_chunks``
        # is the value of ``_dropped_ring_chunks`` the last time the worker
        # thread checked; the delta between consecutive checks is the
        # number of chunks dropped since the last WARNING. ``_ring_overflow_warn_ts``
        # is the ``time.perf_counter()`` of the last WARNING, used to
        # rate-limit to one WARNING per ``_RING_OVERFLOW_WARN_INTERVAL_S``.
        self._last_seen_dropped_ring_chunks: int = 0
        self._ring_overflow_warn_ts: float = 0.0

    def _init_event_queue(self) -> None:
        """IPC event queue + event-worker thread handle + stop event."""
        # IPC event queue + dedicated worker thread. The audio
        # worker thread (``_audio_worker_loop``) enqueues IPC events
        # (e.g. ``audio_clip``) on this queue via a non-blocking
        # ``put``; the event worker thread (``_event_worker_loop``)
        # drains the queue and calls ``event_bus.publish``. This keeps
        # the audio worker off the IPC transport so a slow TCP
        # subscriber cannot stall the audio pipeline and cause
        # ring-buffer overflows / dropped audio.
        #
        # PERF-: bounded at ``maxsize=1000`` so a stalled worker
        # can't cause unbounded memory growth. The producer uses
        # ``put_nowait`` + ``queue.Full`` suppression (see the
        # ``audio_clip`` callsite below), which means events are
        # silently dropped when the worker falls behind — the
        # audio-thread producer never blocks.
        self._event_queue: queue.Queue[dict | _EventWorkerStopSentinel] = queue.Queue(maxsize=1000)
        self._event_worker_thread: threading.Thread | None = None
        self._event_stop_event: threading.Event = threading.Event()

    def _init_stream_format_state(self) -> None:
        """Input-stream channel count + the per-thread mono-downmix slot."""
        # AUDIO-CH: actual channel count of the input stream
        self._actual_channels: int = 1

        # Per-thread holder for the stereo downmix scratch. The downmix
        # itself (:func:`.format.ensure_mono`) now allocates its output
        # directly (one allocation, no scratch round-trip + copy), so
        # this holder is no longer written by production code — it is
        # kept because the mono/downmix regression tests pin its
        # presence (and type) in ``__init__``. Remove it together with
        # that test contract, not before.
        self._mono_scratch_local = threading.local()

    def _init_silence_detection(self) -> None:
        """H12 silent-mic-disconnection state + the app-wired callbacks."""
        # NOTE (): dead_air_timeout / _dead_air_speech_detected
        # _dead_air_silence_start were REMOVED — redundant with
        # stop_on_silence_seconds. Do NOT re-add.

        # H12: Silent mic disconnection detection
        self._silence_timer: float = 0.0
        # absolute timestamp for silence start, prevents
        # timer drift under CPU pressure
        self._silence_start_time: float | None = None
        self._silence_warning_count: int = 0
        self._silence_next_warning_wait: float = 10.0
        self._recording_start_time: float = 0.0
        self._recent_rms_values: collections.deque = collections.deque(maxlen=50)
        # (revised): the dead ``_max_duration_warning_sent``
        # and ``_silence_warning_sent`` boolean flags have been REMOVED.
        # They were declared and reset but NEVER read — the actual
        # silence-warning state machine uses ``_silence_warning_count``
        # (an int counter, see recording.py:1109). Removing the dead
        # flags prevents maintainers from thinking warning deduplication
        # exists when it doesn't.

        # H12 callbacks (wired by app.py)
        self.on_silence_warning = None  # type: Callable[..., Any] | None
        self.on_silence_auto_stop = None  # type: Callable[..., Any] | None
        self.on_max_duration_auto_stop = None  # type: Callable[..., Any] | None

        # Waveform bubble: fired from audio callback on every chunk (wired by app.py)
        self.on_rms_level = None  # type: Callable[..., Any] | None
        # callback signature is (rms: float, peak: float).
        # The previous 3-arg form (rms, peak, audio_chunk) forwarded the
        # filtered audio chunk so WaveformBubble could run Silero VAD on
        # it, but BUBBLE- removed the VAD gate entirely (the
        # device's native sample-rate audio was being fed to a model
        # that assumes 16 kHz).  No current consumer reads the chunk, so
        # it was removed from the contract (privacy surface + per-chunk
        # refcount cost on the audio hot path).

    def _register_scipy_preloader(self) -> None:
        """Start (once) + register the scipy-preloader thread with the
        thread registry so ``shutdown_all()`` can join it on exit."""
        # THREAD-REGISTRY: B-3/S-3 — the scipy-preloader thread is now
        # started lazily from Recorder.__init__ (not at module import).
        # The first Recorder instance triggers the preloader exactly
        # when needed; subsequent Recorders see the cached thread and
        # skip the spawn (see _start_scipy_preloader's idempotency
        # guard). The preloader is a one-shot daemon (no stop
        # mechanism), so it registers with stop_event=None. On a fast
        # system it has already exited by this point and registration
        # is skipped; on a slow system it may still be loading scipy,
        # in which case shutdown_all()'s join gives it up to
        # ``_SCIPY_PRELOADER_JOIN_TIMEOUT_S`` to finish before continuing.
        # The mutable preloader state lives on the .resampling submodule;
        # read it there at call time so tests patching
        # ``voice_typer.server.recording.resampling._scipy_preloader_thread``
        # are honored.
        from voice_typer.server.recording import resampling as _recording_resampling

        _recording_resampling._start_scipy_preloader()
        # Mirror the spawned (or already-cached) preloader thread onto the
        # instance so the start() critical path can tell whether the
        # background scipy import is still in flight. ``start_recording``
        # skips its synchronous ``warm_up_resampler()`` call while this
        # thread is alive — the preloader owns the import then, and the
        # resample helpers load scipy on demand if a resample lands
        # before it finishes (identical output; only the latency moves
        # off the hotkey thread). ``None`` when no thread was spawned
        # (scipy already cached) — the sync warm-up branch checks the
        # mutable resampling globals first anyway.
        self._scipy_preloader_thread = _recording_resampling._scipy_preloader_thread
        if (
            self._thread_registry is not None
            and _recording_resampling._scipy_preloader_thread is not None
            and _recording_resampling._scipy_preloader_thread.is_alive()
        ):
            self._thread_registry.register(
                name="scipy-preloader",
                thread=_recording_resampling._scipy_preloader_thread,
                stop_event=None,
                join_timeout=_recording_resampling._SCIPY_PRELOADER_JOIN_TIMEOUT_S,
            )
