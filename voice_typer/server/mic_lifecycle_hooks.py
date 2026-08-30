"""Mic-watcher hooks — extracted from ``RecordingController`` (Phase 4.5 split).

Owns the active-mic-lost callback wiring + the OS-event-driven
``on_active_mic_lost`` / ``on_device_lost`` callbacks that fire when the
active microphone disappears mid-recording (USB/BT unplug or OS-level
mic-permission revocation).

Collaborator pattern
--------------------
:class:`MicLifecycleHooks` is constructed by
``RecordingController.__init__`` with NO arguments (it is stateless).
Each method takes a back-reference to the owning ``RecordingController``
instance (``controller``) and accesses shared state that lives on the
controller — ``controller._app`` (the VoiceTyperApp), the recorder's
``_mic_watcher`` attribute, etc.

``RecordingController`` keeps 1-line delegator methods
(``_wire_mic_watcher_hooks``, ``_list_active_mic_ids``,
``on_active_mic_lost``, ``on_device_lost``,
``_publish_microphone_disconnected_event``) so existing call sites,
subclass overrides, and tests that monkeypatch the controller's methods
keep working unchanged.

Originally lines 198–410 of ``recording_controller.py``.
"""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


class MicLifecycleHooks:
    """Mic-watcher wiring + active-mic-lost callbacks.

    Extracted from the former ``RecordingController._wire_mic_watcher_hooks``
    / ``_list_active_mic_ids`` / ``on_active_mic_lost`` / ``on_device_lost``
    / ``_publish_microphone_disconnected_event`` methods. Each method's
    body is the moved implementation, with ``self.X`` references rewritten
    to ``controller.X`` for shared state. ``RecordingController`` keeps
    1-line delegators on each method name so existing call sites continue
    to work.
    """

    def __init__(self) -> None:
        # Stateless helper — all state lives on the controller.
        pass

    def wire(self, controller) -> None:
        """Register the active-mic-lost callback + device-id provider +
        ``on_device_lost`` callback on the recorder.

        Idempotent — safe to call multiple times. The hooks are stored on
        the recorder's ``_mic_watcher`` (a property delegating to
        ``DeviceManager._mic_watcher``) and on the recorder itself
        (``on_device_lost``). All assignments are best-effort and wrapped
        in ``contextlib.suppress`` so a partially-initialized recorder (or
        a test mock) doesn't crash RecordingController construction.
        """
        app = controller._app
        recorder = getattr(app, "recorder", None)
        if recorder is None:
            return
        # Wire ``on_device_lost`` so the terminal "max retries reached"
        # path (recorder.py:_handle_device_disconnect) fires the dedicated
        # ``notify.recording_controller.mic_disconnected`` notification
        # instead of falling through to ``on_silence_auto_stop``.
        with contextlib.suppress(Exception):
            recorder.on_device_lost = controller.on_device_lost
        # Wire the active-mic-lost hooks. The watcher may be None on
        # platforms where the watcher failed to start (macOS without the
        # CoreAudio bridge); the recorder's DeviceManager collaborator may
        # also be absent on partially-constructed test doubles.
        mic_watcher = getattr(getattr(recorder, "_devices", None), "_mic_watcher", None)
        if mic_watcher is None:
            return
        with contextlib.suppress(Exception):
            mic_watcher.set_on_active_mic_lost(controller.on_active_mic_lost)
        with contextlib.suppress(Exception):
            mic_watcher.set_device_id_provider(controller._list_active_mic_ids)

    def list_active_mic_ids(self, controller) -> list:
        """Return the current list of microphone IDs for the
        active-mic-lost watcher's membership check.

        The watcher calls this once per OS device-change event (after the
        cache-invalidation callback runs) and checks whether the active
        mic_id (set in ``_start_impl`` via ``set_active_mic_id``) is still
        present. If not, ``on_active_mic_lost`` fires.

        Returns the int ``index`` (not the str ``id``) so the membership
        check ``active_mic_id not in current_ids`` compares int-to-int.
        Pre-fix this returned ``m.get("id")`` (a str like ``"5"``), but
        ``_start_impl`` passes ``set_active_mic_id(resolved)`` where
        ``resolved`` is the int returned by ``recorder._resolve_device()``
        (or ``recorder._effective_device``). The int-vs-str mismatch meant
        the membership check ALWAYS failed on the first device-change
        event after recording started, so ``on_active_mic_lost`` fired
        spuriously and stopped the recording even though the mic was
        still present. Returning ``m.get("index")`` (an int) makes the
        comparison int-to-int and matches the format
        ``set_active_mic_id`` is called with.
        """
        try:
            return [m.get("index") for m in controller._app.list_microphones() if m.get("index") is not None]
        except Exception:
            log.debug("[DICTATION] _list_active_mic_ids failed", exc_info=True)
            return []

    def publish_microphone_disconnected_event(self, controller) -> None:
        """Emit the dedicated ``microphone_disconnected`` IPC event.

        Extracted from ``on_device_lost`` so the fast-path
        (``on_active_mic_lost``) and the slow-path (``on_device_lost``)
        both surface the same IPC banner to the renderer. Pre-fix, only
        the slow path (max-retries-reached) published the event; the fast
        path (OS-event-driven active-mic-lost, sub-second USB/BT unplug
        detection) skipped it, so the renderer showed no banner for the
        most common unplug scenario.

        Mirrors the ``on_microphone_permission_revoked`` pattern
        (best-effort publish with a logged suppress). The event_bus module
        is a leaf dependency, but if the import or publish raises (e.g.
        during shutdown teardown) the caller still proceeds to schedule
        the stop.
        """
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "microphone_disconnected"})
        except Exception:
            log.debug(
                "[DICTATION] failed to publish microphone_disconnected event",
                exc_info=True,
            )

    def on_device_lost(self, controller) -> None:
        """Handle the terminal 'max disconnect retries reached' case with
        a dedicated ``notify.recording_controller.mic_disconnected``
        notification.

        Distinct from ``on_silence_auto_stop`` so the user sees an accurate
        mic-disconnected message rather than the misleading
        'silence detected' message. The actual stop is scheduled off this
        thread (which is the recorder's disconnect-retry thread) to mirror
        the deadlock-avoidance pattern in ``on_silence_auto_stop``.
        """
        log.warning("[DICTATION] mic_disconnected mid-recording -- stopping after max retries")
        with contextlib.suppress(Exception):
            controller._app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.mic_disconnected"),
            )
        # Emit a dedicated IPC event so the renderer can show a banner
        # (distinct from the silence / max-duration auto-stop toast).
        self.publish_microphone_disconnected_event(controller)
        # Stop the recording off this thread (mirror the
        # ``on_silence_auto_stop`` pattern).
        controller._app._schedule_timer(0, controller._app._stop_dictation)

    def on_active_mic_lost(self, controller) -> None:
        """Handle the OS-event-driven active-mic-lost signal from
        ``MicrophoneDeviceWatcher``.

        The watcher fires this when it detects a device-list change AND
        the active mic_id (set in ``_start_impl``) is no longer in the
        freshly-queried device list. This is sub-second detection of
        USB/BT unplug mid-recording — faster than the 1-2s zero-fill-
        chunk retry path in ``_handle_device_disconnect``.

        Now publishes the same ``microphone_disconnected`` IPC event as
        ``on_device_lost`` (via the shared
        ``publish_microphone_disconnected_event`` helper) so the renderer
        surfaces a banner for the fast-path unplug case too. Pre-fix,
        only the slow-path published.

        Scheduled stop (mirrors ``on_silence_auto_stop``) so we don't
        deadlock on ``Recorder._lock`` if the watcher thread holds it.
        """
        log.warning("[DICTATION] Active microphone lost (OS event) -- stopping recording")
        with contextlib.suppress(Exception):
            controller._app.tray.notify_safety(
                APP_NAME,
                "Microphone was unplugged. Recording stopped.",
            )
        # Mirror the slow-path (``on_device_lost``): emit the dedicated
        # IPC event so the renderer can show a banner.
        self.publish_microphone_disconnected_event(controller)
        controller._app._schedule_timer(0, controller._app._stop_dictation)
