"""#2 RecordingController — thin facade composing four focused helpers
(Phase 4.5 split).

Owns the recording lifecycle: toggle/start/stop/cancel, silence/xrun
callbacks, and the streaming session management that runs alongside
recording.

Previously this was a 2055-line monolith mixing five distinct concerns:
(1) lifecycle state machine, (2) persistent transcription watchdog,
(3) streaming-session coordination, (4) mic-watcher hooks, (5)
level_monitor coordination. Per C-ARCH-1 / Rule 20, the monolith has
been split into four focused collaborator modules that mirror the
``AudioPipeline`` / ``AudioCallbackDispatcher`` / ``StreamLifecycle``
pattern already used in :mod:`voice_typer.server.recording`:

- :class:`voice_typer.server.recording_lifecycle.RecordingLifecycle` —
  ``toggle`` / ``_toggle_impl`` / ``start`` / ``_start_impl`` /
  ``stop`` / ``_stop_impl`` / ``_stop_and_transcribe_worker_entry`` /
  ``_run_stop_and_transcribe`` / ``cancel`` / ``_cancel_impl``.
- :class:`voice_typer.server.transcription_watchdog.TranscriptionWatchdog`
  — ``_start_watchdog_thread`` / ``_watchdog_loop`` / ``_reset_watchdog``
  / ``_stop_watchdog_thread`` / ``_force_recover_from_stuck_transcription``
  / ``_mark_cycle_cancelled`` / ``_discard_cancelled_cycle_id``.
- :class:`voice_typer.server.streaming_session_coordinator.StreamingSessionCoordinator`
  — ``_streaming_enabled`` / ``_streaming_config`` /
  ``_start_streaming_session_if_enabled``.
- :class:`voice_typer.server.mic_lifecycle_hooks.MicLifecycleHooks` —
  ``_wire_mic_watcher_hooks`` / ``_list_active_mic_ids`` /
  ``on_active_mic_lost`` / ``on_device_lost`` /
  ``_publish_microphone_disconnected_event``.

Each helper is constructed by ``RecordingController.__init__`` with NO
arguments (stateless); each helper method takes a back-reference to the
owning ``RecordingController`` (``controller``) and reads/writes shared
state that lives on the controller. ``RecordingController`` keeps 1-line
delegator methods on every name that previously lived here so existing
call sites, subclass overrides, and tests that monkeypatch the
controller's methods continue to work unchanged.

What stays on ``RecordingController`` (NOT moved)
-------------------------------------------------
- The streaming-session accessors (``get_streaming_session`` /
  ``set_streaming_session`` / ``pop_streaming_session``) and the cancel
  helper (``_cancel_streaming_session``) remain here. They are tightly
  coupled to the controller's ``_streaming_session`` /
  ``_streaming_session_lock`` / ``_pending_finalize_session`` state, and
  several static-source checks in the test-suite pin their source to
  this module (``inspect.getsource(RecordingController.pop_streaming_session)``
  etc.). Moving them would break those tripwires.
- The audio callbacks (``on_recorder_rms`` / ``on_silence_warning`` /
  ``on_silence_auto_stop`` / ``on_max_duration_auto_stop`` /
  ``on_microphone_permission_revoked`` / ``on_xrun_threshold``) remain
  here — they are tiny one-liners wired to the recorder.
- The level_monitor coordination helpers
  (``_stop_level_monitor_for_recorder_start`` /
  ``_maybe_restart_level_monitor_for_always_visible_bubble``) remain
  here — they touch only ``controller._level_monitor_was_active`` +
  ``level_monitor`` module globals.

Patch-path compatibility
------------------------
Tests do ``patch("voice_typer.server.recording_controller.gc.collect")``
to spy on the GC call inside ``_force_recover_from_stuck_transcription``
(now in :mod:`.transcription_watchdog`). Because the ``gc`` module is a
singleton, patching ``recording_controller.gc.collect`` patches
``gc.collect`` GLOBALLY — so the call from the watchdog helper is still
intercepted. This module keeps ``import gc`` at top so the patch PATH
resolves cleanly.

Lazy helper construction
------------------------
Several tests construct a controller via ``RecordingController.__new__(...)``
(skipping ``__init__``) and then invoke delegator methods directly. To
support that pattern, the helper attributes (``_lifecycle``,
``_watchdog_helper``, ``_streaming_coordinator``, ``_mic_hooks``) are
lazily created by ``__getattr__`` on first access — so a
``__new__``-constructed controller without ``__init__`` still works.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections import OrderedDict
from typing import Any

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.streaming import StreamingConfig, StreamingTranscriptionSession

log = logging.getLogger(__name__)


class RecordingController:
    """Owns recording lifecycle + streaming session + silence/xrun callbacks.

    Thin facade composing four focused helpers (Phase 4.5 split):
    :class:`RecordingLifecycle`, :class:`TranscriptionWatchdog`,
    :class:`StreamingSessionCoordinator`, :class:`MicLifecycleHooks`.
    Each helper is constructed in ``__init__`` (and lazily re-created by
    ``__getattr__`` if ``__init__`` was skipped, e.g. by tests using
    ``RecordingController.__new__(...)``).

    #2 extracted from VoiceTyperApp. The app passes itself (``app``) so
    RecordingController can:
    - Read ``app.config`` (recording_mode, streaming_*, silence_*)
    - Read/write ``app.recorder`` (Recorder instance)
    - Read/write ``app._busy_event`` (busy flag). NOTE: ``app._busy_event``
      uses INVERTED semantics — ``is_set() == True`` means NOT busy,
      ``is_set() == False`` means busy. This is because the event
      doubles as a "ready" signal (``wait()`` blocks while busy). All
      call sites in this module annotate the inverted meaning with
      ``# busy`` / ``# not busy`` / ``# busy = True`` / ``# busy = False``
      comments. A full rename to ``_not_busy_event`` was considered but
      deferred as a large semantic change touching every caller.
    - Own ``self._transcription_thread`` / ``self._streaming_session``
      (: callers must read these via ``app.recording.X``)
    - Update ``app.tray`` state during recording
    - Call ``app._schedule_timer`` / ``app._cancel_pending_timers``
    - Call ``app.models.ensure_active_engine_loaded()`` /
      ``app._fallback_to_whisper()``
    - Call ``app.models.active_transcriber()``
    - Call ``app._duck_volume()`` / ``app._restore_volume()``
    - Call ``app._waveform_bubble`` show/hide/reset_level
    - Call ``app._audio_quality.reset()`` /
      ``app._finalize_audio_quality_report()``
    - Read ``app._cycle_id`` / increment ``app._cycle_counter``
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._streaming_session: StreamingTranscriptionSession | None = None
        # Stash for the streaming session that ``_stop_impl``'s
        # main transcription path pops + signals cancel on. The pipeline's
        # ``_transcribe`` step retrieves it via ``pop_streaming_session()``
        # (which checks this stash as a fallback) so it can call
        # ``session.finalize(audio)`` — the streaming fast path that
        # replaces batch transcription when a streaming session was active.
        # The stash is written + read under ``_streaming_session_lock``.
        self._pending_finalize_session: StreamingTranscriptionSession | None = None
        self._transcription_thread: threading.Thread | None = None
        # RACE-025: lifecycle serialization lock. Prevents concurrent
        # toggle/start/stop/cancel calls from different threads (hotkey
        # thread + tray thread + auto-stop Timer thread + ESC cancel
        # hotkey) from both passing the _busy_event / recorder.recording
        # check before either modifies it. Promoted from a plain Lock to
        # an RLock so the re-entrant call path toggle() ->
        # app._stop_dictation() -> self.stop() (and the matching start()
        # path) does NOT self-deadlock when start/stop acquire the same
        # lock at entry.
        self._toggle_lock = threading.RLock()
        # Watchdog firing counter for the current transcription cycle.
        # Reset to 0 whenever a new transcription thread starts. After
        # ``_watchdog_max_firings`` consecutive watchdog expirations with
        # the worker still alive, we force-recover instead of re-arming —
        # otherwise a genuinely deadlocked ctranslate2 call leaves the
        # app stuck busy forever.
        self._watchdog_firings = 0
        self._watchdog_max_firings = 3
        self._watchdog_lock = threading.Lock()
        # Dedicated lock for ``_streaming_session``. Previously the
        # accessors claimed "thread-safe" but weren't — concurrent
        # start()/cancel() calls could see torn reads or trigger
        # duplicate add_final callbacks.
        self._streaming_session_lock = threading.Lock()
        # RACE-013: persistent watchdog thread + Event instead of chained
        # threading.Timer. Under CPU pressure, chained Timers can stack
        # up (each Timer fires and schedules the next, but the next
        # hasn't started yet so there's no cancellation path). A single
        # persistent thread using Event.wait(timeout=60) is immune to
        # stacking and cheaper than creating a new Timer object every 60s.
        self._watchdog_event = threading.Event()
        self._watchdog_stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        # Bounded LRU registry of cycle_ids that were force-cancelled by
        # the watchdog (or by ESC during the transcription phase).
        # ``DictationPipeline.run()`` checks this registry BEFORE calling
        # ``_copy_and_paste`` — if the cycle was cancelled (because the
        # transcription thread took >4.5min and the watchdog fired), the
        # late transcription is NOT pasted into whatever window currently
        # has focus. Prevents data corruption when the user alt-tabs away
        # during a stuck transcription and the ctranslate2 call eventually
        # completes 5-30 min later. Mutations go through
        # ``_mark_cycle_cancelled`` (add + LRU-evict) and
        # ``_discard_cancelled_cycle_id`` (best-effort remove). See
        # :mod:`.transcription_watchdog` for the eviction cap.
        self._cancelled_cycle_ids: OrderedDict[str, None] = OrderedDict()
        self._cancelled_cycle_ids_lock = threading.Lock()
        # Privacy: shared, clearable slot holding the audio bytes
        # captured by ``stop()`` for the transcription thread. Reading
        # from this slot in the thread (and clearing it in
        # ``_force_recover_from_stuck_transcription``) drops our
        # Python-side reference at force-recovery time; the unavoidable
        # C-level retention by a stuck ctranslate2 call is a documented
        # limitation that requires engine-level changes.
        self._current_audio: Any = None
        # Track whether the level_monitor was actively running when we
        # started recording, so ``_stop_impl`` can restart it if
        # ``bubble_behavior == "always_visible"``. The level_monitor and
        # the Recorder cannot share the mic device concurrently (Windows
        # MME device-conflict; Linux/macOS doubles audio-path CPU). The
        # frontend tries to stop the level_monitor before
        # ``recording_start``, but if the IPC races or the frontend
        # forgets, the backend guard in
        # ``_stop_level_monitor_for_recorder_start`` is the safety net.
        self._level_monitor_was_active: bool = False

        # Phase 4.5 split: construct the four focused collaborator
        # helpers. Each is stateless — all shared state stays on the
        # controller (this object), and the helpers access it via the
        # back-reference passed to each method call.
        from voice_typer.server.mic_lifecycle_hooks import MicLifecycleHooks
        from voice_typer.server.recording_lifecycle import RecordingLifecycle
        from voice_typer.server.streaming_session_coordinator import (
            StreamingSessionCoordinator,
        )
        from voice_typer.server.transcription_watchdog import TranscriptionWatchdog

        self._lifecycle = RecordingLifecycle()
        self._watchdog_helper = TranscriptionWatchdog()
        self._streaming_coordinator = StreamingSessionCoordinator()
        self._mic_hooks = MicLifecycleHooks()

        # Wire the active-mic-lost hooks + the ``on_device_lost``
        # callback so the OS-event-driven watcher can cancel in-flight
        # recordings sub-second when the active mic disappears (USB/BT
        # unplug), instead of falling through to the misleading "silence
        # detected" message after 1-2s of retries. Best-effort: guarded
        # so a recorder without a mic watcher (tests, mock recorders)
        # doesn't fail init.
        self._mic_hooks.wire(self)

    # ── Lazy helper construction for ``__new__``-skipped tests ─────────
    #
    # Several tests construct a controller via
    # ``RecordingController.__new__(RecordingController)`` (skipping
    # ``__init__``) and then invoke delegator methods. Without these
    # ``__getattr__`` overrides, the delegators would fail with
    # ``AttributeError: _lifecycle``. ``__getattr__`` is only called
    # when normal attribute lookup FAILS, so it's a no-op for
    # controllers constructed via the normal ``__init__`` path.

    def __getattr__(self, name: str) -> Any:
        if name == "_lifecycle":
            from voice_typer.server.recording_lifecycle import RecordingLifecycle

            helper = RecordingLifecycle()
            object.__setattr__(self, name, helper)
            return helper
        if name == "_watchdog_helper":
            from voice_typer.server.transcription_watchdog import TranscriptionWatchdog

            helper = TranscriptionWatchdog()
            object.__setattr__(self, name, helper)
            return helper
        if name == "_streaming_coordinator":
            from voice_typer.server.streaming_session_coordinator import (
                StreamingSessionCoordinator,
            )

            helper = StreamingSessionCoordinator()
            object.__setattr__(self, name, helper)
            return helper
        if name == "_mic_hooks":
            from voice_typer.server.mic_lifecycle_hooks import MicLifecycleHooks

            helper = MicLifecycleHooks()
            object.__setattr__(self, name, helper)
            return helper
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    # ── Mic-watcher hooks (delegators → MicLifecycleHooks) ─────────────

    def _wire_mic_watcher_hooks(self) -> None:
        """1-line delegator → :meth:`MicLifecycleHooks.wire`."""
        return self._mic_hooks.wire(self)

    def _list_active_mic_ids(self) -> list:
        """1-line delegator → :meth:`MicLifecycleHooks.list_active_mic_ids`."""
        return self._mic_hooks.list_active_mic_ids(self)

    def on_active_mic_lost(self) -> None:
        """1-line delegator → :meth:`MicLifecycleHooks.on_active_mic_lost`."""
        return self._mic_hooks.on_active_mic_lost(self)

    def on_device_lost(self) -> None:
        """1-line delegator → :meth:`MicLifecycleHooks.on_device_lost`."""
        return self._mic_hooks.on_device_lost(self)

    def _publish_microphone_disconnected_event(self) -> None:
        """1-line delegator → :meth:`MicLifecycleHooks.publish_microphone_disconnected_event`."""
        return self._mic_hooks.publish_microphone_disconnected_event(self)

    # ── Streaming session coordination (delegators → StreamingSessionCoordinator) ─

    def _streaming_enabled(self) -> bool:
        """1-line delegator → :meth:`StreamingSessionCoordinator.streaming_enabled`."""
        return self._streaming_coordinator.streaming_enabled(self)

    def _streaming_config(self) -> StreamingConfig:
        """1-line delegator → :meth:`StreamingSessionCoordinator.streaming_config`."""
        return self._streaming_coordinator.streaming_config(self)

    def _start_streaming_session_if_enabled(self) -> None:
        """1-line delegator → :meth:`StreamingSessionCoordinator.start_streaming_session_if_enabled`."""
        return self._streaming_coordinator.start_streaming_session_if_enabled(self)

    # ── Streaming session accessors (kept on controller — pinned by ──
    # static-source checks; tightly coupled to controller state) ─────────

    def get_streaming_session(self) -> StreamingTranscriptionSession | None:
        """Thread-safe accessor for the active streaming session.

        Guarded by ``_streaming_session_lock``.
        """
        with self._streaming_session_lock:
            return self._streaming_session

    def set_streaming_session(self, session_or_none: StreamingTranscriptionSession | None) -> None:
        """Thread-safe setter for the active streaming session.

        Guarded by ``_streaming_session_lock``.
        """
        with self._streaming_session_lock:
            self._streaming_session = session_or_none

    def pop_streaming_session(self) -> StreamingTranscriptionSession | None:
        """Atomically get AND clear the streaming session.

        Pre-fix, ``_cancel_streaming_session`` did:
            session = self.get_streaming_session()   # lock acquire/release #1
            self.set_streaming_session(None)          # lock acquire/release #2
        This left a TOCTOU window between the two lock acquisitions: a
        concurrent ``_start_streaming_session_if_enabled`` could install
        a NEW session that the subsequent ``set_streaming_session(None)``
        would clobber — cancelling a session that was just freshly started.

        This method does the get-and-clear under a SINGLE lock
        acquisition, eliminating the race.

        If the main slot is empty, also drains the
        ``_pending_finalize_session`` stash. ``_stop_impl``'s main
        transcription path pops the active session, signals cancel, and
        stashes it here so the pipeline's ``_transcribe`` step can
        retrieve it (via this method) and call ``session.finalize(audio)``
        — the streaming fast path. Without this fallback, the pipeline
        would always see ``None`` (the session was already popped by
        ``_stop_impl``) and fall back to batch transcription even when
        streaming was enabled.
        """
        with self._streaming_session_lock:
            session = self._streaming_session
            self._streaming_session = None
            if session is None:
                # Drain the pending-finalize stash so the pipeline can
                # retrieve the session that ``_stop_impl`` popped +
                # signalled cancel on. ``getattr`` default handles test
                # fixtures that build a controller via
                # ``RecordingController.__new__(...)`` without calling
                # ``__init__`` (which is where the stash is declared).
                session = getattr(self, "_pending_finalize_session", None)
                self._pending_finalize_session = None
            return session

    def _cancel_streaming_session(self) -> None:
        """Cancel any active hidden streaming session.

        Uses ``pop_streaming_session()`` (atomic get-and-clear) instead
        of the pre-fix get-then-set sequence that had a TOCTOU window
        between the two lock acquisitions.

        Called from the helper modules via ``controller._cancel_streaming_session()``
        (e.g. from ``RecordingLifecycle._stop_and_transcribe_worker_entry`` /
        ``RecordingLifecycle._cancel_impl`` /
        ``TranscriptionWatchdog.force_recover``) so the TOCTOU-safe pop is
        the only path to clear an active session.
        """
        session = self.pop_streaming_session()
        if session is not None:
            try:
                session.cancel()
            except Exception:
                log.exception("[STREAMING] Failed to cancel streaming session")

    # ── Lifecycle (delegators → RecordingLifecycle) ────────────────────

    def toggle(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle.toggle`."""
        return self._lifecycle.toggle(self)

    def _toggle_impl(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle._toggle_impl`."""
        return self._lifecycle._toggle_impl(self)

    def start(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle.start`."""
        return self._lifecycle.start(self)

    def _start_impl(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle._start_impl`."""
        return self._lifecycle._start_impl(self)

    def stop(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle.stop`."""
        return self._lifecycle.stop(self)

    def _stop_impl(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle._stop_impl`."""
        return self._lifecycle._stop_impl(self)

    def _stop_and_transcribe_worker_entry(self, cycle_id: str) -> None:
        """1-line delegator → :meth:`RecordingLifecycle._stop_and_transcribe_worker_entry`.

        On ``recorder.stop()`` failure, the helper calls
        ``app.tray.notify_safety(APP_NAME, i18n.t("notify.recording_controller.stop_failed"))``
        so the critical-notification bypass behavior is
        preserved verbatim.
        """
        return self._lifecycle._stop_and_transcribe_worker_entry(self, cycle_id)

    def _run_stop_and_transcribe(self, audio, cycle_id: str) -> None:
        """1-line delegator → :meth:`RecordingLifecycle._run_stop_and_transcribe`."""
        return self._lifecycle._run_stop_and_transcribe(self, audio, cycle_id)

    def cancel(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle.cancel`."""
        return self._lifecycle.cancel(self)

    def _cancel_impl(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle._cancel_impl`."""
        return self._lifecycle._cancel_impl(self)

    # ── Watchdog + cancelled-cycle registry (delegators → ──────────────
    # TranscriptionWatchdog) ────────────────────────────────────────────

    def _mark_cycle_cancelled(self, cycle_id: str) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.mark_cycle_cancelled`."""
        return self._watchdog_helper.mark_cycle_cancelled(self, cycle_id)

    def _discard_cancelled_cycle_id(self, cycle_id: str) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.discard_cancelled_cycle_id`."""
        return self._watchdog_helper.discard_cancelled_cycle_id(self, cycle_id)

    def _force_recover_from_stuck_transcription(self, force: bool = False) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.force_recover`."""
        return self._watchdog_helper.force_recover(self, force=force)

    def _start_watchdog_thread(self) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.start_thread`."""
        return self._watchdog_helper.start_thread(self)

    def _watchdog_loop(self) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.loop`."""
        return self._watchdog_helper.loop(self)

    def _reset_watchdog(self) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.reset_watchdog`."""
        return self._watchdog_helper.reset_watchdog(self)

    def _stop_watchdog_thread(self) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.stop_thread`."""
        return self._watchdog_helper.stop_thread(self)

    # ── Audio callbacks (wired to Recorder; kept on controller — tiny ──
    # one-liners) ───────────────────────────────────────────────────────

    def on_recorder_rms(self, rms: float, peak: float, audio_chunk=None) -> None:
        """Forward per-chunk RMS + peak to the waveform bubble."""
        self._app._waveform_bubble.update_level(rms, peak, audio_chunk=audio_chunk)

    def on_silence_warning(self) -> None:
        """Handle silence warning from recorder."""
        log.warning("[DICTATION] Silence warning: no audio detected for a while")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.silence_warning"),
            )

    def on_silence_auto_stop(self) -> None:
        """Handle silence auto-stop from recorder."""
        log.warning("[DICTATION] Silence auto-stop: stopping recording due to prolonged silence")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.silence_auto_stop"),
            )
        # Must NOT call stop() directly here -- this callback runs inside
        # the audio callback while Recorder._lock is held. Calling
        # recorder.stop() would deadlock on the same lock. Schedule it on
        # a separate thread instead. Call ``app._stop_dictation``
        # (delegate) instead of ``self.stop()`` directly so tests that
        # monkeypatch ``_stop_dictation`` still intercept the call.
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_max_duration_auto_stop(self) -> None:
        """Handle max duration auto-stop from recorder."""
        log.warning("[DICTATION] Max duration auto-stop: stopping recording")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.max_duration_auto_stop"),
            )
        # Same reason as on_silence_auto_stop: avoid deadlock on Recorder._lock.
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_microphone_permission_revoked(self) -> None:
        """Handle mid-recording OS-level microphone-permission revocation.

        Distinct from ``on_silence_auto_stop`` so the user sees a
        ``notify.recording_controller.mic_permission_revoked``
        notification (and the renderer receives a dedicated
        ``microphone_permission_revoked`` IPC event) instead of the
        misleading "silence detected" message that
        ``on_silence_auto_stop`` produces.

        Spawned by ``DeviceManager._check_microphone_permission_revoked``
        on a fresh daemon thread (via ``recorder._spawn_device_thread``),
        so we DON'T hold ``Recorder._lock`` here — but we still schedule
        the actual stop off this thread for parity with the silence /
        max-duration auto-stop callbacks (their comment explains the
        deadlock-avoidance rationale; we mirror it for consistency).
        """
        log.warning("[DICTATION] mic_permission_revoked mid-recording -- stopping stream and surfacing IPC event")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.mic_permission_revoked"),
            )
        # Emit the dedicated ``microphone_permission_revoked`` IPC event
        # so the renderer can show a banner (distinct from the generic
        # ``recording_stopped`` / silence-auto-stop toast). Best-effort:
        # the event_bus module is a leaf dependency, but if the import or
        # publish raises (e.g. during shutdown teardown) we still need the
        # stop to fire.
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "microphone_permission_revoked"})
        except Exception:
            log.debug(
                "[DICTATION] failed to publish microphone_permission_revoked event",
                exc_info=True,
            )
        # Stop the recording off this thread (mirror the
        # on_silence_auto_stop pattern). ``_stop_dictation`` re-acquires
        # ``Recorder._lock``; the original caller (the device-health
        # checker's spawned handler) does NOT hold that lock, so the
        # schedule is for consistency with the silence/max-duration
        # callbacks rather than strict deadlock avoidance.
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_xrun_threshold(self, count: int) -> None:
        """Notify the user when xrun count exceeds threshold."""
        log.warning("[XRUN] Threshold reached: %d xruns", count)
        if self._app.config.show_notifications:
            with contextlib.suppress(Exception):
                self._app.tray.notify(
                    f"{APP_NAME} — Audio Issues",
                    f"Detected {count} audio buffer underruns. Try closing other audio apps or reducing CPU load.",
                )

    # ── level_monitor / Recorder InputStream coordination ──────────────

    def _stop_level_monitor_for_recorder_start(self) -> None:
        """Stop the level_monitor's PortAudio InputStream BEFORE opening
        the Recorder's stream.

        ``level_monitor.start_monitoring()`` opens its own ``sd.InputStream``
        on the mic device. ``RecordingController.start()`` opens a SEPARATE
        ``sd.InputStream`` via ``app.recorder.start()``. Without
        coordination, both streams run concurrently -- on Linux/macOS this
        doubles audio-path CPU (2x ``indata.copy()``, 2x RMS, 2x RNNoise);
        on Windows MME the second ``sd.InputStream`` open FAILS (device-
        conflict error), so recording fails to start.

        The frontend tries to stop the level_monitor before
        ``recording_start``, but if the IPC races or the frontend forgets,
        the backend guard here is the safety net. We record whether we
        stopped it (``_level_monitor_was_active``) so ``_stop_impl`` can
        restart it if ``bubble_behavior == "always_visible"``.
        """
        self._level_monitor_was_active = False
        try:
            from voice_typer.server import level_monitor

            if level_monitor.is_monitoring():
                level_monitor.stop_monitoring()
                self._level_monitor_was_active = True
                log.debug(
                    "[DICTATION] level_monitor was active -- stopped before recorder.start() (AB-4, cycle=%s)",
                    getattr(self._app, "_cycle_id", "?"),
                )
        except Exception:
            log.debug(
                "[DICTATION] failed to stop level_monitor before recorder.start() (AB-4)",
                exc_info=True,
            )

    def _maybe_restart_level_monitor_for_always_visible_bubble(self, app: Any) -> None:
        """Restart the level monitor after recording stops if the bubble
        is in ``always_visible`` mode.

        When ``bubble_behavior == "always_visible"``, the level bar is
        shown at all times (even when not recording), so the level_monitor
        must be running whenever the Recorder's stream is closed. We
        stopped it in ``_stop_level_monitor_for_recorder_start`` before
        opening the Recorder's stream; now that ``recorder.stop()`` has
        closed the stream, we restart the level_monitor so the always-
        visible bubble continues showing ambient levels.

        Best-effort: if ``start_monitoring`` raises (e.g. device unplugged
        mid-recording), we log and continue -- the bubble will just show a
        flat line until the next ``level_monitor_start`` IPC.
        """
        try:
            if getattr(app.config, "bubble_behavior", "") != "always_visible":
                return
            from voice_typer.server import level_monitor

            if level_monitor.is_monitoring():
                # Already running (e.g. the frontend restarted it via
                # ``level_monitor_start`` IPC after the recording stopped).
                return
            # Use the configured microphone if set; otherwise default.
            mic_id = getattr(app.config, "microphone", None)
            if not isinstance(mic_id, str):
                mic_id = None
            level_monitor.start_monitoring(mic_id=mic_id)
            log.debug(
                "[DICTATION] level_monitor restarted (bubble_behavior=always_visible, AB-4, cycle=%s)",
                getattr(app, "_cycle_id", "?"),
            )
        except Exception:
            log.debug(
                "[DICTATION] failed to restart level_monitor after recorder.stop() (AB-4)",
                exc_info=True,
            )
