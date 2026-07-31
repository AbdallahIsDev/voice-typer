"""Device enumeration, hot-swap, and health-checker for :class:`Recorder`.

Phase 4.5 — extracted from :mod:`.recorder` to shrink the
3019-LOC ``recorder.py`` partial monolith (see  in
``review.md``). Owns all device-list caching,
microphone-watcher lifecycle, sample-rate negotiation, and the periodic
device-health-checker daemon thread.

Collaborator pattern
--------------------
:class:`DeviceManager` is constructed by ``Recorder.__init__`` with a
back-reference to the owning ``Recorder`` instance
(``DeviceManager(recorder)``). The collaborator reference is used to
access *shared* state that lives on ``Recorder`` and is NOT moved here:

- ``self.recorder.config`` — for ``microphone`` / ``sample_rate``
- ``self.recorder._recording_event`` — read by the health-checker loop
- ``self.recorder._stop_generation`` — captured when spawning the
  disconnect handler so the handler can bail out on a deliberate
  stop/start cycle (HOTKEY-CRASH)
- ``self.recorder._handle_device_disconnect`` — KEEP on ``Recorder``
  because it manipulates stream state directly (opens a new
  ``sd.InputStream``, reassigns ``self._stream`` / ``self._effective_sr``
  / ``self._actual_channels``). ``DeviceManager`` only schedules the
  handler on a fresh daemon thread.

Device-owned state (the 12 attributes moved from ``Recorder.__init__``)
lives on ``DeviceManager`` directly:

- ``_device_list_cache`` / ``_device_list_cache_time`` /
  ``_device_list_cache_ttl`` — AUDIO-MIC TTL cache
- ``_mic_watcher`` — PERF-MIC-001 OS-event-driven cache invalidation
- ``_device_disconnected`` / ``_device_disconnect_retries`` /
  ``_max_disconnect_retries`` — AUDIO-HOT hot-plug disconnect state
- ``_device_check_interval`` / ``_device_check_counter`` — legacy
  per-chunk check counter (the per-chunk probe was removed; the counter
  is reset in ``start()`` for diagnostic cleanliness)
- ``_device_health_checker_thread`` / ``_device_health_stop_event`` /
  ``_device_check_interval_s`` — CPU-03 dedicated health-checker thread

``Recorder`` exposes the device-owned state via read/write property
shims (``r._device_disconnected``, ``r._mic_watcher``, etc.) so existing
tests that do ``r._device_disconnected = False`` /
``r._mic_watcher is None`` keep working unchanged.

Patch-path compatibility
------------------------
Tests use ``monkeypatch.setattr(recording.sd, "InputStream", fake)``
and similar to inject fake sounddevice behavior. The ``sd`` lazy-module
proxy in this module re-resolves ``sys.modules`` on every attribute
access (see ``voice_typer/server/_lazy_import.py``), so the patch on the
package-level ``recording.sd`` propagates here automatically — no
``_recording_pkg.sd`` indirection needed.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

from voice_typer.server._lazy_import import lazy_module

# PERF-COLDSTART-001: lazy import — sounddevice loads the PortAudio C
# library at import time. The lazy proxy re-resolves ``sys.modules`` on
# every attribute access, so test patches of the form
# ``monkeypatch.setattr(recording.sd, "InputStream", fake)`` (which
# mutate the real ``sounddevice`` module) propagate here automatically.
sd = lazy_module("sounddevice")

# All submodules use the package-level logger so log records propagate
# to ``caplog.at_level(..., logger="voice_typer.server.recording")`` in
# tests.
log = logging.getLogger("voice_typer.server.recording")

# when the mic device watcher fails to start (e.g. on macOS
# where the OS-event hook isn't implemented, or on platforms where
# /dev/snd inotify fails), fall back to a SHORTER device-list TTL for
# a window after the failure so device hot-plug events are detected
# sooner than the default 30s. Without this, a user who plugs in a
# USB mic immediately after launch (a common pattern — start app,
# realize mic is missing, plug it in) waits up to 30s for the device
# list to refresh. The fast TTL is only active for
# ``_DEVICE_LIST_FAST_TTL_WINDOW`` seconds after the watcher-start
# failure; outside that window, the default 30s TTL applies (the
# watcher is started exactly once in ``__init__`` and never retried,
# so once we're past the failure window we revert to the conservative
# fallback that matches the rest of the codebase's behavior on
# watcher-less platforms).
_DEVICE_LIST_FAST_TTL: float = 5.0
_DEVICE_LIST_FAST_TTL_WINDOW: float = 60.0


class DeviceManager:
    """Device enumeration, hot-swap, and health-checker for ``Recorder``.

    Phase 4.5 — extracted from :mod:`.recorder`. See the module
        docstring for the collaborator-pattern rationale and the list of
        device-owned vs. recorder-owned state.
    """

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        # import (``recorder`` imports ``device_manager`` at module top
        # to construct this class in ``Recorder.__init__``).
        self.recorder = recorder

        # AUDIO-HOT: hot-plug device disconnect handling
        self._device_disconnected: bool = False
        self._device_disconnect_retries: int = 0
        self._max_disconnect_retries: int = 3
        # AUDIO-HOT: periodic device availability check — every N chunks,
        # verify the current device is still present in sd.query_devices().
        # NOTE: the per-chunk probe was removed (CPU-03 — moved to a
        # dedicated daemon thread, see ``_device_health_checker_loop``).
        # The counter is kept for diagnostic cleanliness and is reset by
        # ``Recorder.start()`` via the property shim.
        self._device_check_interval: int = 500  # check every ~500 chunks (~32s at 16Hz)
        self._device_check_counter: int = 0
        # CPU-03: dedicated device-health-checker thread state. The checker
        # runs OFF the audio worker thread (replacing the old per-chunk
        # sd.query_devices() probe that could block the worker 50-200ms on
        # Windows MME). It wakes every ``_device_check_interval_s`` and is
        # started by start() / stopped by stop()+discard().
        self._device_health_checker_thread: threading.Thread | None = None
        self._device_health_stop_event: threading.Event = threading.Event()
        self._device_check_interval_s: float = 30.0  # seconds between probes
        # counter for periodic OS-level microphone-permission
        # re-probe. The health-checker loop wakes every
        # ``_device_check_interval_s`` (30 s default); every
        # ``_permission_check_interval``-th iteration (~60 s) we call
        # ``permissions.check_microphone_permission()`` to detect a
        # mid-recording OS-level revocation (macOS System Settings,
        # Windows Privacy toggle, Flatpak portal). Pre-fix, the
        # permission was only probed once at ``Recorder.start()`` so a
        # mid-session revocation silently delivered zero-filled buffers
        # for 30-60 s until silence-auto-stop fired with a misleading
        # "silence detected" notification.
        self._permission_check_counter: int = 0
        # With the default 30 s interval, every 2nd iteration = ~60 s.
        # Tests can override to 1 to probe on every loop wake.
        self._permission_check_interval: int = 2

        # AUDIO-MIC: device list cache with timestamp
        self._device_list_cache: list[dict] | None = None
        self._device_list_cache_time: float = 0.0
        self._device_list_cache_ttl: float = 30.0  # seconds
        # monotonic timestamp of the most recent mic-watcher-start
        # failure. Set in the except branch of the MicrophoneDeviceWatcher
        # startup try/except below; consulted by ``_refresh_device_list``
        # to decide whether to use the fast TTL (5s) or the default
        # (30s). Stays at 0.0 if the watcher started successfully.
        self._mic_watcher_failed_at: float = 0.0

        # PERF-MIC-001: OS-event-driven cache invalidation. The watcher
        # runs in a daemon thread and calls ``_invalidate_device_cache``
        # when the OS reports a device plug/unplug event (WM_DEVICECHANGE
        # on Windows, /dev/snd dir change on Linux). The 30s TTL above
        # remains as a fallback for platforms where the watcher can't
        # start (macOS) or for the case where the watcher thread crashes.
        self._mic_watcher: Any | None = None
        # optional service-layer cache invalidator callback.
        self._service_cache_invalidator: Any | None = None
        # configurable sleep between BT-device disconnect retries.
        self._bt_retry_sleep_seconds: float = 0.75
        # one-shot flag so the name-mismatch warning fires at
        # most once per DeviceManager instance.
        self._device_name_mismatch_warned: bool = False
        try:
            from voice_typer.server.microphone_watcher import (
                MicrophoneDeviceWatcher,
            )

            # (pyrefly): bind to a local so pyrefly can see the
            # value is non-None when we call .start() on it. Assigning
            # straight to ``self._mic_watcher`` (typed ``Any | None``)
            # made pyrefly think ``self._mic_watcher.start()`` could be
            # called on None.
            watcher: Any = MicrophoneDeviceWatcher(on_change=self._invalidate_device_cache)
            watcher.start()
            self._mic_watcher = watcher
        except Exception:
            # Watcher is best-effort — the 30s TTL cache covers the
            # case where the watcher fails to start.
            log.warning(
                "[RECORDING] mic device watcher failed to start, falling back to 30s TTL polling",
                exc_info=True,
            )
            self._mic_watcher = None
            # record the failure timestamp so ``_refresh_device_list``
            # can use a shorter TTL (5s) for the next 60s to catch hot-plug
            # events more aggressively than the default 30s fallback.
            self._mic_watcher_failed_at = time.monotonic()

    # ── AUDIO-MIC: device list caching ──────────────────────────────────

    def _refresh_device_list(self) -> list[dict]:
        """Return the device list, refreshing the cache if stale.

                AUDIO-MIC: The mic list was previously loaded once at startup.
                If a USB/BT device was disconnected or connected mid-session,
                the stale list would reference non-existent devices. We now
                cache the device list with a TTL of 30 seconds and re-query
                PortAudio when the cache expires or when the current device
                disappears.

        if the mic device watcher failed to start recently
                (within ``_DEVICE_LIST_FAST_TTL_WINDOW``), use a shorter TTL
                of ``_DEVICE_LIST_FAST_TTL`` (5s) instead of the default 30s.
                This catches hot-plug events more aggressively in the window
                right after a watcher-start failure (a common user pattern:
                launch app → realize mic is missing → plug it in). Outside
                the failure window, the default 30s TTL applies.
        """
        now = time.monotonic()
        # compute the effective TTL based on whether the mic
        # watcher failed recently. ``_mic_watcher_failed_at`` is 0.0
        # if the watcher started successfully (or hasn't tried yet),
        # so the fast TTL only applies after an actual failure.
        if (
            self._mic_watcher is None
            and self._mic_watcher_failed_at > 0.0
            and now - self._mic_watcher_failed_at < _DEVICE_LIST_FAST_TTL_WINDOW
        ):
            effective_ttl: float = _DEVICE_LIST_FAST_TTL
        else:
            effective_ttl = self._device_list_cache_ttl
        if self._device_list_cache is not None and now - self._device_list_cache_time < effective_ttl:
            return self._device_list_cache

        try:
            devices = []
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) <= 0:
                    continue
                devices.append(
                    {
                        "id": str(i),
                        "index": i,
                        "name": dev.get("name", ""),
                        "max_input_channels": dev.get("max_input_channels", 0),
                    }
                )
            self._device_list_cache = devices
            self._device_list_cache_time = now
            return devices
        except Exception as e:
            log.debug("[RECORDING] Could not enumerate devices: %s", e)
            return self._device_list_cache or []

    def set_service_cache_invalidator(self, callback: Any | None) -> None:
        """Register a service-layer cache invalidator ().

        The registered callback is invoked from ``_invalidate_device_cache``
        whenever the OS microphone watcher fires a hot-plug event. The
        service layer registers its cache-invalidation hook here so a
        hot-plug immediately propagates to the UI's mic dropdown.

        Pass ``None`` to unregister. Safe to call multiple times — the
        most recent callback wins.
        """
        self._service_cache_invalidator = callback

    def _invalidate_device_cache(self) -> None:
        """Reset the device-list cache so the next ``_refresh_device_list``
                call re-queries PortAudio.

                PERF-MIC-001: called by ``MicrophoneDeviceWatcher`` from its
                daemon thread when the OS reports a device plug/unplug event.

        also fires the registered service-layer cache invalidator
                so the UI's mic dropdown refreshes immediately after a hot-plug.

        (High): when a hot-plug event arrives AND a device disconnect
                is currently in-progress (``_device_disconnected=True``),
                proactively trigger a re-attempt of the disconnect handler on a
                fresh daemon thread. Pre-fix, the recorder stayed stuck (the
                health-checker loop skipped) and the only path forward was the
                user pressing the hotkey to stop+start. With this hook, plugging
                in a new mic mid-session auto-recovers within one OS-event cycle.

                Thread-safety: writes to ``_device_list_cache`` and
                ``_device_list_cache_time`` are simple attribute assignments
                guarded by the GIL.
        """
        self._device_list_cache = None
        self._device_list_cache_time = 0.0
        log.debug("[RECORDING] Device cache invalidated by OS-event watcher")

        # fire the service-layer cache invalidator (best-effort).
        service_cb = self._service_cache_invalidator
        if service_cb is not None:
            try:
                service_cb()
            except Exception:
                log.debug(
                    "[RECORDING] service cache invalidator callback raised",
                    exc_info=True,
                )

        # (High): proactive recovery on hot-plug while a disconnect
        # is in-progress.
        if not self._device_disconnected:
            return
        try:
            if not self.recorder._recording_event.is_set():
                return
        except Exception:
            pass
        _captured_gen = getattr(self.recorder, "_stop_generation", 0)
        with contextlib.suppress(Exception):
            self.recorder._spawn_device_thread(
                name="device-hotplug-recovery",
                target=self.recorder._handle_device_disconnect,
                kwargs={"_captured_generation": _captured_gen},
                single_flight=True,
            )
        log.info(
            "[RECORDING] TY-5: hot-plug event triggered disconnect recovery "
            "(device was disconnected, re-attempting restart)"
        )

    def shutdown_mic_watcher(self) -> None:
        """Stop the microphone device-change watcher.

        Called explicitly from ``VoiceTyperApp.quit_app()`` during
        shutdown and defensively from ``Recorder.__del__``. Safe to call
        even if the watcher never started (``_mic_watcher`` is None).
        """
        watcher = getattr(self, "_mic_watcher", None)
        if watcher is None:
            return
        try:
            watcher.stop()
        except Exception:
            log.debug("[RECORDING] mic watcher stop failed", exc_info=True)
        self._mic_watcher = None

    # ── CPU-03: Device health checker thread ─────────────────────────

    def _start_device_health_checker(self) -> None:
        """Start the device health checker daemon thread.

        CPU-03: replaces the old per-chunk ``sd.query_devices()`` check that
        was running on the audio worker thread. The old approach could block
        the worker for 50-200ms on Windows MME with many audio devices,
        causing the ring buffer to overflow and audio chunks to be dropped.

        The health checker wakes every ``_device_check_interval_s`` (default
        30s) and calls ``sd.query_devices(current_device)``. If the device
        is no longer available, it sets ``_device_disconnected`` and spawns
        the disconnect handler -- same logic as before, but off the audio
        worker thread.

        Idempotent: if the checker is already running, this is a no-op.
        Started by ``start()``, stopped by ``stop()`` / ``discard()``.
        """
        if self._device_health_checker_thread is not None and self._device_health_checker_thread.is_alive():
            return
        self._device_health_stop_event.clear()
        self._device_health_checker_thread = threading.Thread(
            target=self._device_health_checker_loop,
            name="device-health-checker",
            daemon=True,
        )
        self._device_health_checker_thread.start()

    def _stop_device_health_checker(self) -> None:
        """Signal the device health checker thread to stop and join it.

        CPU-03: sets the stop event and waits up to 1s for the thread
        to wake from its sleep and exit. Since the thread sleeps for 30s
        between checks, worst-case the wait times out and the daemon
        thread exits on its next sleep cycle.

        Safe to call when the checker is not running (no-op).
        """
        self._device_health_stop_event.set()
        thread = self._device_health_checker_thread
        if thread is not None:
            thread.join(timeout=1.0)
            health_exited = not thread.is_alive()
            if not health_exited:
                log.debug(
                    "[RECORDING] Device health checker thread did not exit within 1s "
                    "(stop event left SET; thread NOT nulled to prevent duplicate spawn)"
                )
            # only null the thread reference + clear stop event if the
            # thread actually exited. If still alive (stuck in sd.query_devices),
            # leave the stop event SET so the loop exits on its next wait()
            # return, and keep the thread reference so _start_device_health_checker's
            # is_alive() guard prevents spawning a duplicate.
            if health_exited:
                self._device_health_checker_thread = None
                self._device_health_stop_event.clear()

    def _device_health_checker_loop(self) -> None:
        """Device health checker daemon thread main loop.

                CPU-03: wakes every ``_device_check_interval_s`` (default 30s) and
                calls ``sd.query_devices(current_device)`` to verify the current
                recording device is still present. If PortAudio raises an exception
                (device disconnected), sets ``_device_disconnected`` and spawns
                ``_handle_device_disconnect`` on a fresh daemon thread.

        every ``_permission_check_interval``-th iteration (~60s)
                also calls ``permissions.check_microphone_permission()`` to detect
                a mid-recording OS-level microphone-permission revocation. On
                DENIED, sets ``_device_disconnected=True`` and spawns a handler
                that calls ``recorder.on_microphone_permission_revoked`` (a
                distinct callback from ``on_silence_auto_stop``) so the
                recording_controller can stop the stream and surface a clear
                "Microphone permission revoked" notification instead of the
                misleading "silence detected" message.

                Exits immediately when ``_device_health_stop_event`` is set.
        """
        while not self._device_health_stop_event.wait(timeout=self._device_check_interval_s):
            # skip the check if we've already detected a disconnect
            # and scheduled a handler.
            if self._device_disconnected:
                continue
            # periodic OS-level microphone-permission re-probe.
            # ``check_microphone_permission`` is best-effort — on
            # Windows/Linux it historically returns GRANTED (the
            # fix tightens this), but on macOS it does a real
            # AVCaptureDevice probe. Gate the call behind the counter so
            # we don't pay the cost on every 30 s wake.
            self._permission_check_counter += 1
            if self._permission_check_counter >= self._permission_check_interval:
                self._permission_check_counter = 0
                if self._check_microphone_permission_revoked():
                    # Permission was revoked mid-recording. The
                    # ``_check_microphone_permission_revoked`` helper
                    # already set ``_device_disconnected=True`` and
                    # spawned the handler — continue the loop so we
                    # don't also try the device query below.
                    continue
            try:
                current_device = self._resolve_device()
                if current_device is not None:
                    try:
                        sd.query_devices(current_device)
                    except Exception:
                        # HOTKEY-CRASH: double-check recording is still active.
                        # The collaborator's ``_recording_event`` is the
                        # source of truth — accessing it via the back-reference
                        # avoids duplicating the Event on DeviceManager.
                        if not self.recorder._recording_event.is_set():
                            return
                        log.warning(
                            "[RECORDING] Current device no longer available in query_devices -- disconnect detected"
                        )
                        self._device_disconnected = True
                        _captured_gen = self.recorder._stop_generation
                        # Route through ``_spawn_device_thread`` so
                        # the handler is registered with ``thread_registry``
                        # (when available) and single-flight guarded so a
                        # flapping device can't spawn multiple concurrent
                        # handlers. The helper lives on ``Recorder`` (not
                        # ``DeviceManager``) because the registry + guard
                        # state are owned by ``Recorder``.
                        with contextlib.suppress(Exception):
                            self.recorder._spawn_device_thread(
                                name="device-disconnect-check",
                                target=self.recorder._handle_device_disconnect,
                                kwargs={"_captured_generation": _captured_gen},
                                single_flight=True,
                            )
            except Exception:
                log.debug("[RECORDING] Device health checker error", exc_info=True)

    def _check_microphone_permission_revoked(self) -> bool:
        """probe the OS-level microphone permission state.

        Returns True if the permission was detected as DENIED, in which
        case the caller has already set ``self._device_disconnected=True``
        and spawned a handler that invokes
        ``recorder.on_microphone_permission_revoked`` (a callback the
        ``RecordingController`` wires up to stop the stream and emit a
        dedicated ``microphone_permission_revoked`` IPC event).

        Returns False on GRANTED / PROMPT / UNKNOWN, or if the probe
        itself raised (we never want a permission-check failure to take
        down the health-checker thread).
        """
        # Lazy import to avoid paying the import cost on every loop wake
        # (the module is imported once and then cached in ``sys.modules``).
        try:
            from voice_typer.server import permissions as _permissions_mod

            state = _permissions_mod.check_microphone_permission()
        except Exception:
            log.debug(
                "[RECORDING] Microphone permission probe raised — ignoring",
                exc_info=True,
            )
            return False

        # ``MicrophonePermissionState.DENIED`` is the only state we act
        # on. ``PROMPT`` means the OS will re-prompt on next access
        # (not a revocation). ``UNKNOWN`` means we can't tell (probe
        # failure, unsupported platform) — defer to the runtime
        # PortAudio-open re-classification path in the recorder.
        try:
            denied = state == _permissions_mod.MicrophonePermissionState.DENIED
        except Exception:
            denied = str(state).lower() == "denied"

        if not denied:
            return False

        # HOTKEY-CRASH: double-check recording is still active before
        # scheduling the handler — if the user already stopped the
        # recording, there's nothing to revoke.
        try:
            if not self.recorder._recording_event.is_set():
                return False
        except Exception:
            # If we can't read the recording event, proceed — the
            # handler will no-op if recording has stopped.
            pass

        log.warning(
            "[RECORDING] Microphone permission revoked mid-recording -- "
            "stopping stream and surfacing on_microphone_permission_revoked"
        )
        self._device_disconnected = True
        _captured_gen = getattr(self.recorder, "_stop_generation", 0)

        def _permission_revoked_handler(_captured_generation: int = _captured_gen) -> None:
            """Spawned on a fresh daemon thread so we don't block the
            health-checker loop. Calls the recorder's
            ``on_microphone_permission_revoked`` callback if wired (the
            ``RecordingController`` installs it in ``_start_impl``)."""
            try:
                cb = getattr(self.recorder, "on_microphone_permission_revoked", None)
                if callable(cb):
                    cb()
                else:
                    # Fallback: if the callback isn't wired (older
                    # recording_controller, or tests that bypass
                    # _start_impl), fall back to ``on_device_lost`` then
                    # ``on_silence_auto_stop`` so the recording at least
                    # stops — mirrors the recorder's
                    # ``_handle_device_disconnect`` fallback chain.
                    device_lost_cb = getattr(self.recorder, "on_device_lost", None)
                    if callable(device_lost_cb):
                        with contextlib.suppress(Exception):
                            device_lost_cb()
                    elif self.recorder.on_silence_auto_stop is not None:
                        with contextlib.suppress(Exception):
                            self.recorder.on_silence_auto_stop()
            except Exception:
                log.debug(
                    "[RECORDING] on_microphone_permission_revoked handler raised",
                    exc_info=True,
                )

        with contextlib.suppress(Exception):
            self.recorder._spawn_device_thread(
                name="mic-permission-revoked",
                target=_permission_revoked_handler,
                kwargs={"_captured_generation": _captured_gen},
                single_flight=True,
            )
        return True

    # ── AUDIO-MIC: device resolution + sample-rate negotiation ──────────

    def _resolve_device(self):
        """Resolve config.microphone to a sounddevice device specifier.

                ``config.microphone`` is one of:

                - ``None`` — system default → return ``None``.
                - ``"<index>"`` (legacy bare index string) → return ``int(index)``.
        ``"<index>|<name>|<host_api>"`` ( compound form) → prefer
                  name-based resolution via ``find_microphone_by_name`` so a
                  saved index that now points at a different physical device
                  (after Windows MME hot-swap renumbering) is NOT silently
                  substituted.

        (Medium): PortAudio device indices are NOT stable across
                hot-swap on Windows MME. The compound form stores the device
                NAME alongside the index so the resolver can re-find the
                original physical device by name after renumbering.

        when the saved index now points at a differently-named
                device AND name lookup failed, emit a one-time WARNING.
        """
        mic = self.recorder.config.microphone
        if mic is None:
            return None
        # Legacy / simple case: bare numeric index or non-compound string.
        if not isinstance(mic, str) or "|" not in mic:
            try:
                return int(mic)
            except (ValueError, TypeError):
                return mic

        # compound form "<index>|<name>|<host_api>".
        parts = mic.split("|", 2)
        if len(parts) < 2:
            try:
                return int(mic.split("|", 1)[0])
            except (ValueError, TypeError):
                return mic
        saved_index_str, saved_name = parts[0], parts[1]
        try:
            saved_index = int(saved_index_str)
        except (ValueError, TypeError):
            saved_index = None

        # Prefer name-based resolution: this is the stable identifier
        # that survives PortAudio hot-swap renumbering on Windows MME.
        try:
            from voice_typer.server.server_platform import find_microphone_by_name

            match = find_microphone_by_name(saved_name)
        except Exception:
            match = None
        if match is not None:
            try:
                return int(match.get("index", saved_index) if saved_index is not None else match["index"])
            except (ValueError, TypeError, KeyError):
                pass

        # Name lookup failed — fall back to the saved index.
        if saved_index is None:
            return saved_name

        # one-time name-mismatch warning.
        if not self._device_name_mismatch_warned:
            try:
                current_info = sd.query_devices(saved_index)
                current_name = str(current_info.get("name", "")).strip().lower()
                if current_name and current_name != saved_name.strip().lower():
                    log.warning(
                        "[RECORDING] DJ-69: saved microphone index %d now points to "
                        "'%s' (was '%s') — device may have been renumbered by hot-swap; "
                        "re-select the microphone in Settings to update the saved reference",
                        saved_index,
                        current_info.get("name", ""),
                        saved_name,
                    )
                    self._device_name_mismatch_warned = True
            except Exception:
                pass
        return saved_index

    def _build_device_info_for_retry_policy(self) -> dict | None:
        """query the current device info for BT retry classification.

        Returns the ``sd.query_devices(current_device)`` dict, or ``None``
        if the query raised. Used by ``_get_max_retries_for_device`` and
        ``_get_retry_sleep_for_device`` so the disconnect handler can
        pick a BT-aware retry policy without each callsite duplicating
        the query-and-classify logic.
        """
        try:
            current = self._resolve_device()
            if current is None:
                return sd.query_devices(kind="input")
            return sd.query_devices(current)
        except Exception:
            return None

    def _get_max_retries_for_device(self, device_info: dict | None) -> int:
        """return the max retry count for the given device.

        Bluetooth HFP/HSP devices (identified by name keyword OR by an
        8/16 kHz native sample rate — the HFP/HSP narrowband signature)
        get 6 retries. Non-BT devices get 3 retries.
        """
        if device_info is None:
            return 3
        name = str(device_info.get("name", "")).lower()
        if any(kw in name for kw in ("bluetooth", "hfp", "hands-free", "hands free")):
            return 6
        try:
            sr = int(device_info.get("default_samplerate", 0))
        except (ValueError, TypeError):
            sr = 0
        if sr in (8000, 16000):
            return 6
        return 3

    def _get_retry_sleep_for_device(self, device_info: dict | None) -> float:
        """return the per-retry sleep for the given device.

        BT devices sleep ``_bt_retry_sleep_seconds`` (default 0.75s)
        between retries. Non-BT devices sleep 0.0 (immediate retry).
        """
        if device_info is None:
            return 0.0
        if self._get_max_retries_for_device(device_info) >= 6:
            return self._bt_retry_sleep_seconds
        return 0.0

    def _host_api_name(self, host_api_index: int) -> str:
        try:
            return sd.query_hostapis(host_api_index)["name"]
        except Exception:
            return ""

    def _device_index(self, fallback_index: int, device_info: dict) -> int:
        try:
            return int(device_info.get("index", fallback_index))
        except Exception:
            return fallback_index

    def _same_physical_microphone_candidates(self, device: Any) -> list[Any]:
        """Return equivalent input device IDs to try if the selected one fails."""
        candidates = [device]
        if not isinstance(device, int):
            return candidates

        try:
            selected = sd.query_devices(device)
            selected_name = selected.get("name", "").strip().lower()
            all_devices = list(sd.query_devices())
        except Exception as e:
            log.debug("[RECORDING] Could not build microphone fallback list: %s", e)
            return candidates

        if not selected_name:
            return candidates

        alternates = []
        for fallback_index, info in enumerate(all_devices):
            index = self._device_index(fallback_index, info)
            if index == device:
                continue
            if info.get("max_input_channels", 0) <= 0:
                continue
            if info.get("name", "").strip().lower() != selected_name:
                continue
            host_name = self._host_api_name(info.get("hostapi", 0))
            alternates.append((self._fallback_host_rank(host_name), index))

        alternates.sort()
        seen = set()
        ordered = []
        for candidate in candidates + [index for _, index in alternates]:
            marker = str(candidate)
            if marker in seen:
                continue
            ordered.append(candidate)
            seen.add(marker)
        return ordered

    def _fallback_host_rank(self, host_name: str) -> int:
        """REC-6: rank host APIs for fallback device selection.

        Lower rank = preferred. The ranking covers Windows, macOS, and
        Linux host APIs so the fallback loop picks the most reliable
        host when multiple devices share the same name.

        Windows:
          - MME = 0 (most compatible, lowest latency on legacy hardware)
          - WASAPI = 1 (modern, lower latency on Win 10+)
          - WDM-KS = 2 (kernel streaming, rare)
          - DirectSound = 3 (legacy, higher latency)

        macOS:
          - CoreAudio = 0 (the only native host API — always rank 0)

        Linux:
          - ALSA = 0 (native, lowest latency)
          - PulseAudio = 1 (userspace daemon, ubiquitous on desktop)
          - JACK = 2 (pro audio, low latency but rare on consumer systems)

        Unknown hosts return 5 (lowest priority but not last — leaves
        room for future additions without renumbering).
        """
        lower = host_name.lower()
        # Windows hosts
        if lower == "mme":
            return 0
        if "wasapi" in lower:
            return 1
        if "wdm-ks" in lower:
            return 2
        if "directsound" in lower:
            return 3
        # macOS hosts
        if "coreaudio" in lower or "core audio" in lower:
            return 0
        # Linux hosts
        if lower == "alsa":
            return 0
        if "pulseaudio" in lower:
            return 1
        if lower == "jack":
            return 2
        # Unknown host — lowest priority but not last (leaves room for
        # future additions).
        return 5

    def _resolve_effective_sample_rate(self, device: int | None) -> tuple[int, dict | None]:
        """Determine the effective sample rate and device info for the given device.

        Returns (effective_sr, dev_info_dict) where dev_info_dict has
        'name', 'host_api_name', 'native_rate' keys, or None if query failed.

        Strategy: always record at the device's native sample rate when it
        differs from the Whisper target rate (16kHz), and resample afterwards
        with scipy.  This avoids relying on PortAudio's internal resampling
        (which can introduce artifacts, especially via MME on Windows) and
        ensures WASAPI devices that reject non-native rates work correctly.

        Only uses the requested 16kHz rate directly when the device's native
        rate IS 16000 Hz.
        """
        target_sr = self.recorder.config.sample_rate  # 16000 for Whisper
        dev_info_extra = None
        try:
            # device=None means system default; query_devices(None) returns
            # a list of ALL devices, so we must use kind='input' instead.
            dev_info = sd.query_devices(kind="input") if device is None else sd.query_devices(device)
            native_rate = int(dev_info["default_samplerate"])
            host_api_name = ""
            try:
                host_api_idx = dev_info.get("hostapi", 0)
                host_api_name = sd.query_hostapis(host_api_idx)["name"]
            except Exception:
                pass
            dev_info_extra = {
                "name": dev_info["name"],
                "host_api_name": host_api_name,
                "native_rate": native_rate,
            }
            log.debug(
                "[RECORDING] Device query: name=%s, host_api=%s, native_rate=%d, target_rate=%d",
                dev_info["name"],
                host_api_name,
                native_rate,
                target_sr,
            )

            # If the device's native rate matches the target, use it directly.
            # Otherwise, always record at native rate and resample afterwards.
            # This avoids PortAudio's internal resampling (which can produce
            # lower-quality audio via MME) and ensures WASAPI devices that
            # reject non-native rates (e.g. 16kHz on a 48kHz WASAPI device)
            # work correctly.
            if native_rate == target_sr:
                log.debug(
                    "[RECORDING] Native rate matches target, using %d Hz directly",
                    target_sr,
                )
                return target_sr, dev_info_extra
            else:
                log.debug(
                    "[RECORDING] Native rate %d differs from target %d, will record at native rate and resample",
                    native_rate,
                    target_sr,
                )
                return native_rate, dev_info_extra
        except Exception as e:
            # log at WARNING (not DEBUG) so the user knows
            # the native-rate detection failed and PortAudio will do
            # internal resampling (which may introduce artifacts).
            log.warning(
                "[RECORDING] Could not query device info for device %s: %s. "
                "Falling back to target rate %d Hz (PortAudio will resample "
                "internally — audio quality may be lower).",
                device,
                e,
                target_sr,
            )
            return target_sr, dev_info_extra

    def _all_input_device_candidates(self) -> list[int]:
        """Return all available input device IDs as a last-resort fallback."""
        candidates = []
        try:
            all_devices = list(sd.query_devices())
            for fallback_index, info in enumerate(all_devices):
                index = self._device_index(fallback_index, info)
                if info.get("max_input_channels", 0) <= 0:
                    continue
                if index not in candidates:
                    candidates.append(index)
        except Exception as e:
            log.debug("[RECORDING] Could not build all-device fallback list: %s", e)
        return candidates
