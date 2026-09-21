"""``RecorderInitMixin``: owns ``Recorder``'s construction helpers.

``Recorder.__init__`` was 370 LOC
declaring 60+ instance attributes. The device / disconnect-handler /
collaborator-construction block (~88 LOC) is extracted here as
:meth:`RecorderInitMixin._setup_device_state_and_collaborators`.
``Recorder.__init__`` calls the mixin method at the appropriate point
(after the basic Recorder state, ``_recording_event``, ``_stream``,
``config``, ``_lock``, ``_worker_thread`` etc., is initialized, since
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
``_last_seen_dropped_ring_chunks``, ``_ring_overflow_warn_ts``), none
of which are in the extracted section. The literals that ARE in the
extracted section (``_stop_generation``, ``_user_stop_pending``,
``DeviceManager(self)``, ``DisconnectHandler(self)``,
``AudioPipeline(self)``, ``AudioCallbackDispatcher(self)``,
``StreamLifecycle(self)``, ``SessionState(self)``,
``DevicePrewarm(self)``,
``DevicePrewarm(self)``,
``_prewarm_device_cache()``) are NOT pinned by any source-inspection
test, so moving them out of ``__init__``'s source is safe.
(The historical ``_disconnect_handler_lock`` /
``_disconnect_handler_running`` literals were in this list too until
they were moved OFF the host entirely: see
``DisconnectHandler.__init__``; their tests now pin the owning
collaborator's attributes.)
Patch-path compatibility
------------------------
Tests that access these as instance attributes
(``recorder._stop_generation = 0`` etc.) keep working unchanged, the
mixin methods run during ``__init__`` and set the same attributes on the
same ``self``. The disconnect single-flight guard is the exception: it
now lives on the owning collaborator
(``recorder._disconnect_handler._single_flight_lock`` /
``._single_flight_running``).

Cross-module name resolution
----------------------------
Recorder-owned module constants (``DEFAULT_MAX_BUFFER_CHUNKS``,
``_XRUN_WINDOW_MAXLEN``, ``_AUDIO_RING_BUFFER_CAPACITY``) stay defined
in :mod:`.recorder` (tests import them from there) and are read here at
CALL time via function-level imports, by the time any of these
helpers runs, ``recorder`` is fully loaded (they only run from
``Recorder.__init__``).
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE, scaled_audio_blocksize
from voice_typer.server._lazy_import import lazy_module

if TYPE_CHECKING:
    from collections.abc import Callable

    from voice_typer.server.config import Config

    from .recorder import _EventWorkerStopSentinel

# Lazy proxies, same cold-start pattern as recorder.py / format.py:
np = lazy_module("numpy")
sd = lazy_module("sounddevice")

# All submodules use the package-level logger so log records propagate
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
    etc.), the contract is documented on
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
          - ``_stop_generation`` / ``_user_stop_pending``, disconnect
            handler bouncer + stream-finished-callback disambiguation.
          - ``_devices`` (``DeviceManager``), owns device enumeration,
            hot-swap, mic-watcher, health-checker thread.
          - ``_disconnect_handler`` (``DisconnectHandler``), owns the
            ~175-LOC stream-restart block (called from
            ``_handle_device_disconnect`` under
            ``_stream_lifecycle_lock``) AND the disconnect single-flight
            guard (``_single_flight_lock`` / ``_single_flight_running`` —
            the historical ``Recorder._disconnect_handler_lock`` /
            ``_disconnect_handler_running`` pair, moved there).
          - ``_audio_pipeline`` (``AudioPipeline``), owns the six
            named helpers split out of ``_process_audio_chunk``.
          - ``_capture`` (``AudioCallbackDispatcher``), owns the
            audio worker main loop + the RT callback dispatch body.
          - ``_stream_lifecycle`` (``StreamLifecycle``), owns the
            PortAudio stream-open candidate loop + teardown body.
          - ``_session_state`` (``SessionState``), owns per-session
            state reset, config-derived scalar caching, secure-clear,
            buffer resizing, preroll prepend.
          - Calls ``_prewarm_device_cache()`` to spawn a best-effort
            daemon thread that populates
            ``DeviceManager._device_list_cache`` ahead of the first
            ``start()`` call.
        """
        # HOTKEY-CRASH: generation counter incremented in stop() so stale
        self._stop_generation: int = 0
        # STREAM-FIX: flag set by stop() BEFORE stream.stop() so
        self._user_stop_pending: bool = False

        # STATE-OWNERSHIP (E15): the disconnect single-flight guard

        # Local import to avoid a circular import at module load time
        from .audio_pipeline import AudioPipeline as _AudioPipeline
        from .capture import AudioCallbackDispatcher as _AudioCallbackDispatcher
        from .device_manager import DeviceManager as _DeviceManager
        from .device_prewarm import DevicePrewarm as _DevicePrewarm
        from .disconnect_handler import DisconnectHandler as _DisconnectHandler
        from .session_state import SessionState as _SessionState
        from .stream_lifecycle import StreamLifecycle as _StreamLifecycle

        # AUDIO-HOT: hot-plug device disconnect handling
        self._devices: _DeviceManager = _DeviceManager(self)

        # ``DisconnectHandler`` owns the ~175-LOC stream-restart
        self._disconnect_handler: _DisconnectHandler = _DisconnectHandler(self)
        self._audio_pipeline: _AudioPipeline = _AudioPipeline(self)
        # ``DevicePrewarm`` owns the device-cache
        self._device_prewarm: _DevicePrewarm = _DevicePrewarm(self)
        # Collaborators constructed here
        self._capture: _AudioCallbackDispatcher = _AudioCallbackDispatcher(self)
        self._stream_lifecycle: _StreamLifecycle = _StreamLifecycle(self)
        self._session_state: _SessionState = _SessionState(self)

        # PERF: pre-warm the device-list cache on a background daemon
        self._prewarm_device_cache()

    # Moved verbatim from :mod:`.recorder` (further-split): each helper

    def _init_core_session_state(
        self,
        config: Config,
        audio_processor: Any | None,
        thread_registry: Any | None,
    ) -> None:
        """Config / audio-processor / thread-registry backrefs."""
        self.config = config
        self._audio_processor = audio_processor  # AudioProcessor or None
        # THREAD-REGISTRY: optional central registry for shutdown
        self._thread_registry = thread_registry
        # STATE-OWNERSHIP: the PortAudio ``InputStream`` slot

    def _init_locks_and_flags(self) -> None:
        """Create the three synchronization primitives + force-closed flag."""
        # serializes ``start()`` against ``discard()`` so a
        self._start_lock = threading.Lock()
        # serializes the read-check-create-start sequence in
        self._worker_lifecycle_lock = threading.Lock()
        # serializes stream teardown (``_teardown_stream``)
        self._stream_lifecycle_lock = threading.Lock()

        # force-closed flag set by ``shutdown_controller`` when
        self._force_closed: bool = False

    def _init_xrun_tracking(self) -> None:
        """The threshold callback slot (app wiring) + the recording gate event."""
        # Item 1: xrun notification callback. Set by VoiceTyperApp
        self.on_xrun_threshold: Callable[[int], None] | None = None
        self._recording_event = threading.Event()
        # removed dead ``_in_callback`` field, it

    def _init_sample_rate_and_chunk_state(self, config: Config) -> None:
        """Sample-rate scalars (device-rate) and the per-chunk VAD"""
        self._effective_sr: int = config.sample_rate
        # per-chunk VAD property cache. The audio worker hot path
        self._cached_vad_enabled: bool = False
        self._cached_use_silero_vad: bool = False
        self._cached_silero_available: bool = False
        # Cached (up, down) integer ratio for the VAD
        self._cached_vad_resample_up_down: tuple[int, int] | None = None
        self._cached_vad_resample_sr: int | None = None
        self._last_rms: float = 0.0

    def _init_snapshot_caches(self) -> None:
        """Snapshot resample/no-resample caches + the last-audio-stats slot."""
        # H15/M8: Cached resampled prefix for snapshot() to avoid O(n²) resampling
        self._cached_resampled: np.ndarray = np.array([], dtype=np.float32)
        # Contiguous storage: ``_cached_resampled`` OWNS a geometrically
        self._cached_resampled_len: int = 0
        self._cached_native_chunk_count: int = 0
        # cache key must include the audio dtype + sample rates
        self._cached_resample_key: tuple = ()
        # cache the no-resample concatenation result so
        self._cached_no_resample_len: int = -1
        self._cached_no_resample_arr: np.ndarray | None = None
        # mirror the resample-path segment-list + lazy-concat
        self._cached_no_resample_segments: list[np.ndarray] = []
        self._cached_no_resample_concat_dirty: bool = False
        # cache of (rms, peak, silence_pct) from the most
        self._last_audio_stats: tuple[float, float, float] | None = None

        # snapshot() resample-path segment list + lazy concat.
        self._cached_resampled_segments: list[np.ndarray] = []
        self._cached_resampled_concat_dirty: bool = False

    def _init_error_counters(self) -> None:
        """Per-session error counters (reset by ``start()`` each session)."""
        # per-session error counters. Previously these were
        self._dropped_chunks: int = 0
        self._rms_callback_error_count: int = 0

    def _init_vad(self, config: Config) -> None:
        """Construct the ``VadProcessor`` and log raw-recording mode."""
        from voice_typer.server.vad_processor import VadProcessor

        # VAD state machine with hysteresis.
        self._vad: VadProcessor = VadProcessor(config)
        if not self._vad.vad_enabled:
            log.info("[RECORDING] VAD disabled, all audio enhancements off (raw recording mode).")

    def _init_preroll_state(self, config: Config) -> None:
        """Pre-roll circular buffer sizing + the in-flight-callback guard."""
        # ADR 0007 §3.5: AGC instance variables deleted (replaced by

        # dead-defensive code. The ``or 0`` guard is preserved because
        preroll_seconds = float(config.pre_roll_buffer_seconds or 0)
        sample_rate = int(config.sample_rate or WHISPER_SAMPLE_RATE)
        # cache these so start() can recompute the deque maxlen
        self._preroll_seconds: float = preroll_seconds
        # Chunk-size mirror for the placeholder deque below: the ~32 ms
        self._preroll_blocksize: int = scaled_audio_blocksize(sample_rate)
        self._preroll_buffer: collections.deque = collections.deque(
            maxlen=int(preroll_seconds * sample_rate / self._preroll_blocksize) + 2 if preroll_seconds > 0 else 0
        )
        self._preroll_active: bool = preroll_seconds > 0  # only capture when enabled

        # guard flag for in-flight audio callback
        self._is_in_audio_callback: threading.Event = threading.Event()

    def _init_ring_and_worker_handles(self) -> None:
        """SPSC ring buffer + audio-worker thread/event handles + the
        ring-overflow warning bookkeeping."""
        from .recorder import _AUDIO_RING_BUFFER_CAPACITY

        # SPSC ring buffer for callback → worker handoff.
        self._ring_buffer: collections.deque = collections.deque(maxlen=_AUDIO_RING_BUFFER_CAPACITY)
        # Worker thread that drains _ring_buffer and runs the heavy
        self._worker_thread: threading.Thread | None = None
        self._worker_stop_event: threading.Event = threading.Event()
        self._worker_wake_event: threading.Event = threading.Event()
        # Counter for chunks dropped because the ring buffer was full
        self._dropped_ring_chunks: int = 0
        # ``AudioCallbackDispatcher.dispatch_callback_body`` (the
        # (``AudioCallbackDispatcher._last_callback_error``, see
        self._last_seen_dropped_ring_chunks: int = 0
        self._ring_overflow_warn_ts: float = 0.0

    def _init_event_queue(self) -> None:
        """IPC event queue + event-worker thread handle + stop event."""
        # IPC event queue + dedicated worker thread. The audio
        self._event_queue: queue.Queue[dict | _EventWorkerStopSentinel] = queue.Queue(maxsize=1000)
        self._event_worker_thread: threading.Thread | None = None
        self._event_stop_event: threading.Event = threading.Event()

    def _init_stream_format_state(self) -> None:
        """Input-stream channel count + the per-thread mono-downmix slot."""
        # AUDIO-CH: actual channel count of the input stream
        self._actual_channels: int = 1

        # Per-thread holder for the stereo downmix scratch. The downmix
        self._mono_scratch_local = threading.local()

    def _init_silence_detection(self) -> None:
        """H12 silent-mic-disconnection state + the app-wired callbacks."""
        # NOTE (): dead_air_timeout / _dead_air_speech_detected

        # H12: Silent mic disconnection detection
        self._silence_timer: float = 0.0
        # absolute timestamp for silence start, prevents
        self._silence_start_time: float | None = None
        self._silence_warning_count: int = 0
        self._silence_next_warning_wait: float = 10.0
        self._recording_start_time: float = 0.0
        self._recent_rms_values: collections.deque = collections.deque(maxlen=50)
        # (revised): the dead ``_max_duration_warning_sent``

        # H12 callbacks (wired by app.py)
        self.on_silence_warning = None  # type: Callable[..., Any] | None
        self.on_silence_auto_stop = None  # type: Callable[..., Any] | None
        self.on_max_duration_auto_stop = None  # type: Callable[..., Any] | None

        # Waveform bubble: fired from audio callback on every chunk (wired by app.py)
        self.on_rms_level = None  # type: Callable[..., Any] | None
        # callback signature is (rms: float, peak: float).

    def _register_scipy_preloader(self) -> None:
        """Start (once) + register the scipy-preloader thread with the
        thread registry so ``shutdown_all()`` can join it on exit."""
        # THREAD-REGISTRY: the scipy-preloader thread is now
        from voice_typer.server.recording import resampling as _recording_resampling

        _recording_resampling._start_scipy_preloader()
        # Mirror the spawned (or already-cached) preloader thread onto the
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
