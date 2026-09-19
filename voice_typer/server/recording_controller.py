"""C-ARCH-1: wiring-only. Streaming-session accessors + audio callbacks +
NOTE: see docs/code-notes/recording.md#package-layout
Keep ``import gc`` at top: tests patch ``recording_controller.gc.collect``
__getattr__ so ``__new__``-constructed controllers (tests) still work.
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
    """Wire contract: ``app._busy_event`` is INVERTED — is_set() means NOT busy"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._streaming_session: StreamingTranscriptionSession | None = None
        # Stash for the streaming session that ``_stop_impl``'s
        self._pending_finalize_session: StreamingTranscriptionSession | None = None
        self._transcription_thread: threading.Thread | None = None
        # RACE-025: lifecycle serialization lock. Prevents concurrent
        self._toggle_lock = threading.RLock()
        # Watchdog firing counter for the current transcription cycle.
        self._watchdog_firings = 0
        self._watchdog_max_firings = 3
        self._watchdog_lock = threading.Lock()
        self._streaming_session_lock = threading.Lock()
        # RACE-013: persistent watchdog thread + Event instead of chained
        self._watchdog_event = threading.Event()
        self._watchdog_stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        # Bounded LRU registry of cycle_ids that were force-cancelled by
        self._cancelled_cycle_ids: OrderedDict[str, None] = OrderedDict()
        self._cancelled_cycle_ids_lock = threading.Lock()
        # Privacy: shared, clearable slot holding the audio bytes
        self._current_audio: Any = None
        # Track whether the level_monitor was actively running when we
        self._level_monitor_was_active: bool = False

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
        self._mic_hooks.wire(self)

    # Several tests construct a controller via

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
        """Atomically get AND clear the streaming session."""
        with self._streaming_session_lock:
            session = self._streaming_session
            self._streaming_session = None
            if session is None:
                # Drain the pending-finalize stash so the pipeline can
                session = getattr(self, "_pending_finalize_session", None)
                self._pending_finalize_session = None
            return session

    def _cancel_streaming_session(self) -> None:
        """Cancel any active hidden streaming session."""
        session = self.pop_streaming_session()
        if session is not None:
            try:
                session.cancel()
            except Exception:
                log.exception("[STREAMING] Failed to cancel streaming session")

    # The public entry points (``toggle`` / ``start`` / ``stop`` /

    def toggle(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle.toggle`."""
        return self._lifecycle.toggle(self)

    def start(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle.start`."""
        return self._lifecycle.start(self)

    def stop(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle.stop`."""
        return self._lifecycle.stop(self)

    def cancel(self) -> None:
        """1-line delegator → :meth:`RecordingLifecycle.cancel`."""
        return self._lifecycle.cancel(self)

    def _mark_cycle_cancelled(self, cycle_id: str) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.mark_cycle_cancelled`."""
        return self._watchdog_helper.mark_cycle_cancelled(self, cycle_id)

    def _discard_cancelled_cycle_id(self, cycle_id: str) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.discard_cancelled_cycle_id`."""
        return self._watchdog_helper.discard_cancelled_cycle_id(self, cycle_id)

    def _force_recover_from_stuck_transcription(self, force: bool = False) -> None:
        """1-line delegator → :meth:`TranscriptionWatchdog.force_recover`."""
        return self._watchdog_helper.force_recover(self, force=force)

    def force_recover(self, *, force: bool = False) -> None:
        """Public force-cancel of a stuck transcription."""
        return self._force_recover_from_stuck_transcription(force=force)

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

    def on_recorder_rms(self, rms: float, peak: float) -> None:
        """Forward per-chunk RMS + peak to the waveform bubble."""
        self._app._waveform_bubble.update_level(rms, peak)

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
        """Handle mid-recording OS-level microphone-permission revocation."""
        log.warning("[DICTATION] mic_permission_revoked mid-recording -- stopping stream and surfacing IPC event")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.mic_permission_revoked"),
            )
        # Emit the dedicated ``microphone_permission_revoked`` IPC event
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "microphone_permission_revoked"})
        except Exception:
            log.debug(
                "[DICTATION] failed to publish microphone_permission_revoked event",
                exc_info=True,
            )
        # Stop the recording off this thread (mirror the
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_xrun_threshold(self, count: int) -> None:
        """Notify the user when xrun count exceeds threshold."""
        log.warning("[XRUN] Threshold reached: %d xruns", count)
        if self._app.config.show_notifications:
            with contextlib.suppress(Exception):
                self._app.tray.notify(
                    i18n.t("notify.recording_controller.xrun_title", app=APP_NAME),
                    i18n.t("notify.recording_controller.xrun_body", count=count),
                )

    def _stop_level_monitor_for_recorder_start(self) -> None:
        """Stop the level_monitor's PortAudio InputStream BEFORE opening"""
        self._level_monitor_was_active = False
        try:
            from voice_typer.server import level_monitor

            if level_monitor.is_monitoring():
                level_monitor.stop_monitoring()
                self._level_monitor_was_active = True
                log.debug(
                    "[DICTATION] level_monitor was active -- stopped before recorder.start() (cycle=%s)",
                    getattr(self._app, "_cycle_id", "?"),
                )
        except Exception:
            log.debug(
                "[DICTATION] failed to stop level_monitor before recorder.start()",
                exc_info=True,
            )

    def _maybe_restart_level_monitor_for_always_visible_bubble(self, app: Any) -> None:
        """Restart the level monitor after recording stops if the bubble"""
        try:
            if getattr(app.config, "bubble_behavior", "") != "always_visible":
                return
            from voice_typer.server import level_monitor

            if level_monitor.is_monitoring():
                # Already running (e.g. the frontend restarted it via
                return
            # Use the configured microphone if set; otherwise default.
            mic_id = getattr(app.config, "microphone", None)
            if not isinstance(mic_id, str):
                mic_id = None
            level_monitor.start_monitoring(mic_id=mic_id)
            log.debug(
                "[DICTATION] level_monitor restarted (bubble_behavior=always_visible, cycle=%s)",
                getattr(app, "_cycle_id", "?"),
            )
        except Exception:
            log.debug(
                "[DICTATION] failed to restart level_monitor after recorder.stop()",
                exc_info=True,
            )
