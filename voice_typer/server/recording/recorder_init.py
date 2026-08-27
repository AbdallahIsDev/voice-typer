"""``RecorderInitMixin`` — extract ``Recorder.__init__``'s device /
health-checker state setup.

Phase 4.5 further-split: ``Recorder.__init__`` was 370 LOC
declaring 60+ instance attributes. The device / disconnect-handler /
collaborator-construction block (~88 LOC) is extracted here as
:meth:`RecorderInitMixin._setup_device_state_and_collaborators`.
``Recorder.__init__`` calls the mixin method at the appropriate point
(after the basic Recorder state — ``_recording_event``, ``_stream``,
``config``, ``_lock``, ``_worker_thread`` etc. — is initialized, since
the collaborators' ``__init__`` methods reference those attributes
through the back-reference).

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
etc.) keep working unchanged — the mixin method runs during
``__init__`` and sets the same attributes on the same ``self``.
"""

from __future__ import annotations

import threading


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
        # MicrophoneDeviceWatcher lifecycle were moved to
        # ``DeviceManager`` (see ``device_manager.py``). The attrs are
        # re-exposed on ``Recorder`` via read/write property shims (see
        # the property block below ``__init__``) so existing tests that
        # do ``r._device_disconnected = False`` / ``r._mic_watcher is
        # None`` keep working unchanged. KEEP-methods on ``Recorder``
        # that read/write these attrs (``_handle_device_disconnect``,
        # ``_stream_finished_callback``, ``_process_audio_chunk``,
        # ``start``) also go through the shims.
        #
        # The DeviceManager is constructed AFTER the basic Recorder
        # state is initialized (``_recording_event``, ``_stream``,
        # ``config``, etc.) so its ``__init__`` can register the
        # MicrophoneDeviceWatcher callback against
        # ``self._invalidate_device_cache`` (a delegator method that
        # routes through ``self._devices`` — which is set by this
        # assignment).
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
