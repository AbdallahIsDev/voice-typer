"""controller, ``controller._app`` (the VoiceTyperApp), the recorder's
Mic-watcher hooks, extracted from ``RecordingController`` (Phase 4.5 split).
``on_active_mic_lost`` / ``on_device_lost`` callbacks that fire when the
``RecordingController.__init__`` with NO arguments (it is stateless).
Each method takes a back-reference to the owning ``RecordingController``
instance (``controller``) and accesses shared state that lives on the
``_mic_watcher`` attribute, etc.
``RecordingController`` keeps 1-line delegator methods
(``_wire_mic_watcher_hooks``, ``_list_active_mic_ids``,
``on_active_mic_lost``, ``on_device_lost``,
``_publish_microphone_disconnected_event``) so existing call sites,
Originally lines 198–410 of ``recording_controller.py``.
"""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


class MicLifecycleHooks:
    """Mic-watcher wiring + active-mic-lost callbacks."""

    def __init__(self) -> None:
        # Stateless helper, all state lives on the controller.
        pass

    def wire(self, controller) -> None:
        """Register the active-mic-lost callback + device-id provider +"""
        app = controller._app
        recorder = getattr(app, "recorder", None)
        if recorder is None:
            return
        # Wire ``on_device_lost`` so the terminal "max retries reached"
        with contextlib.suppress(Exception):
            recorder.on_device_lost = controller.on_device_lost
        # Wire the active-mic-lost hooks. The watcher may be None on
        mic_watcher = getattr(getattr(recorder, "_devices", None), "_mic_watcher", None)
        if mic_watcher is None:
            return
        with contextlib.suppress(Exception):
            mic_watcher.set_on_active_mic_lost(controller.on_active_mic_lost)
        with contextlib.suppress(Exception):
            mic_watcher.set_device_id_provider(controller._list_active_mic_ids)

    def list_active_mic_ids(self, controller) -> list:
        """Return the current list of microphone IDs for the

        Returns the int ``index`` (not the str ``id``) so the membership
        """
        try:
            return [m.get("index") for m in controller._app.list_microphones() if m.get("index") is not None]
        except Exception:
            log.debug("[DICTATION] _list_active_mic_ids failed", exc_info=True)
            return []

    def publish_microphone_disconnected_event(self, controller) -> None:
        """Emit the dedicated ``microphone_disconnected`` IPC event."""
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "microphone_disconnected"})
        except Exception:
            log.debug(
                "[DICTATION] failed to publish microphone_disconnected event",
                exc_info=True,
            )

    def on_device_lost(self, controller) -> None:
        """Handle the terminal 'max disconnect retries reached' case with"""
        log.warning("[DICTATION] mic_disconnected mid-recording -- stopping after max retries")
        with contextlib.suppress(Exception):
            controller._app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.mic_disconnected"),
            )
        # Emit a dedicated IPC event so the renderer can show a banner
        self.publish_microphone_disconnected_event(controller)
        # Stop the recording off this thread (mirror the
        controller._app._schedule_timer(0, controller._app._stop_dictation)

    def on_active_mic_lost(self, controller) -> None:
        """Handle the OS-event-driven active-mic-lost signal from"""
        log.warning("[DICTATION] Active microphone lost (OS event) -- stopping recording")
        with contextlib.suppress(Exception):
            controller._app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.mic_unplugged"),
            )
        # Mirror the slow-path (``on_device_lost``): emit the dedicated
        self.publish_microphone_disconnected_event(controller)
        controller._app._schedule_timer(0, controller._app._stop_dictation)
