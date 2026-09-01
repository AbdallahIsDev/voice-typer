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

The historical ``Recorder``-level read/write property shims for this
state (``r._device_disconnected = False`` / ``r._mic_watcher is None``)
were REMOVED — all consumers (``Recorder`` KEEP-methods, tests) access
the state through ``recorder._devices.<attr>``.

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
        # ``Recorder.start()`` (via ``SessionState.reset_session_state``).
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
        # BT-narrowband devices get a shorter health-check interval (5 s)
        # so a flapping BT mic is re-probed promptly —
        # ``_effective_device_check_interval_s`` returns this for
        # BT-classified devices (see the retry-policy path).
        self._device_check_interval_s_bt: float = 5.0
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
        # Lazy host-API index → name cache. Populated on first
        # ``_host_api_name`` call (or via ``_refresh_device_list`` when
        # the cached device dicts already include ``hostapi``). Each
        # ``sd.query_hostapis(idx)`` RPC costs 50-200ms on Windows MME;
        # the index → name mapping is stable for the process lifetime
        # (host APIs don't appear/disappear at runtime), so a one-shot
        # cache eliminates the RPC on every ``_resolve_effective_sample_rate``
        # call (1-3 candidates on the ``start()`` critical path) and
        # every ``_same_physical_microphone_candidates`` fallback lookup.
        self._host_api_cache: dict[int, str] = {}
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
                        "index": i,
                        "name": dev.get("name", ""),
                        "max_input_channels": dev.get("max_input_channels", 0),
                        # Cache the native sample rate and host-API index
                        # so ``_resolve_effective_sample_rate`` and
                        # ``_same_physical_microphone_candidates`` can read
                        # from the cache instead of issuing fresh
                        # ``sd.query_devices(device)`` /
                        # ``sd.query_hostapis(idx)`` RPCs on every
                        # ``start()`` candidate (50-200ms/RPC on Windows
                        # MME; 1-3 candidates × 2 RPCs each = 100-1200ms
                        # of avoidable latency on the hotkey-press →
                        # recording-begins critical path). The TTL is 30s
                        # (or 5s after a watcher-start failure), so a
                        # BT HFP mode-switch (which changes
                        # ``default_samplerate`` from 48k → 8k/16k) is
                        # reflected on the next cache refresh — the
                        # live-query fallback in ``_cached_device_info``
                        # covers the rare case where a fresher read is
                        # needed mid-TTL.
                        "default_samplerate": dev.get("default_samplerate", 0),
                        "hostapi": dev.get("hostapi", 0),
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
        except AttributeError:
            # ``_recording_event`` may not be set yet during early init —
            # proceed (the handler will no-op if recording hasn't started).
            # Previously a broad ``except Exception: pass``.
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

    def stop_device_health_checker(self, timeout: float | None = None) -> None:
        """Signal the device health checker thread to stop and join it.

        Promoted from ``Recorder._stop_device_health_checker`` (Phase 4.5
        completion) — the body is unchanged. ``recorder._stop_device_health_checker``
        (the documented 1-line delegator) routes here.

        When ``timeout`` is explicitly ``0.0``, the call is
        fire-and-forget — the stop event is signalled but the method
        returns immediately without joining the daemon thread. This is
        used by ``stop()`` to avoid blocking up to 1.0s on a thread that
        almost always times out anyway (the checker sleeps 30s between
        probes, so a 1.0s join rarely succeeds). The daemon thread exits
        on its next ``_device_health_stop_event.wait()`` return.

        Any other ``timeout`` value (including ``None`` for backward
        compatibility with callers that don't pass one) performs the full
        stop + join (1.0s join budget).
        """
        if timeout == 0.0:
            # Fire-and-forget: signal the stop event, do NOT join. The
            # daemon thread will exit on its next 30s wait() return.
            # Accessing the private attribute is safe because this class
            # and the health-checker loop are tightly coupled collaborators
            # in the same package.
            self._device_health_stop_event.set()
            return
        self._stop_device_health_checker()

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

        BT devices (Bluetooth HFP/HSP, hands-free, or 8/16 kHz
                native-rate signatures) get a tighter 5 s interval
                (``_device_check_interval_s_bt``) so a profile-switch
                drift is detected within ~5 s instead of up to 30 s.
                The interval is re-evaluated every iteration so a
                profile switch mid-session is picked up promptly.

        when ``current_device is None`` (``config.microphone is
                None`` — PortAudio opened the OS default), the loop
                also queries ``sd.query_devices(kind="input")["index"]``
                and compares to ``_stream_open_default_input_index``
                (captured at stream-open time or lazily on the first
                iteration). On mismatch (user changed the OS default
                input mid-session), it routes through
                ``_handle_device_disconnect`` so the stream is torn
                down + re-opened against the new OS default.

                Exits immediately when ``_device_health_stop_event`` is set.
        """
        while True:
            # Compute the effective poll interval per iteration. BT
            # devices (HFP/HSP, hands-free, or 8/16 kHz native rate)
            # get a 5 s interval so a profile-switch drift is detected
            # within ~5 s; everything else gets the default 30 s. The
            # classification is re-evaluated every iteration so a BT
            # profile switch mid-session is picked up promptly.
            effective_interval = self._effective_device_check_interval_s()
            if self._device_health_stop_event.wait(timeout=effective_interval):
                return
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
                        dev_info = sd.query_devices(current_device)
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
                    else:
                        # Sample-rate drift detection. After
                        # ``sd.query_devices`` succeeds, compare the
                        # device's current ``default_samplerate`` against
                        # the recorder's ``_effective_sr`` (the rate the
                        # open InputStream is actually running at). A
                        # drift happens when the OS reconfigures the
                        # device's native rate behind our back — most
                        # commonly a Bluetooth HFP headset that drops
                        # from 48 kHz wideband to 16 kHz narrowband when
                        # the phone-call profile takes over, or a USB
                        # device that gets re-enumerated at a different
                        # default after a driver update. The open stream
                        # keeps running at the old ``_effective_sr``, but
                        # PortAudio silently resamples — which on macOS
                        # CoreAudio produces audible artifacts and on
                        # Windows MME produces zero-filled chunks.
                        # Detecting the drift here and routing through
                        # ``_handle_device_disconnect`` tears down +
                        # re-opens the stream so the new ``_effective_sr``
                        # matches the device's current native rate.
                        if self._detect_sample_rate_drift(dev_info):
                            if not self.recorder._recording_event.is_set():
                                return
                            log.warning(
                                "[RECORDING] Sample-rate drift detected on current "
                                "device -- disconnect recovery will pick up the new rate"
                            )
                            self._device_disconnected = True
                            _captured_gen = self.recorder._stop_generation
                            with contextlib.suppress(Exception):
                                self.recorder._spawn_device_thread(
                                    name="device-samplerate-drift",
                                    target=self.recorder._handle_device_disconnect,
                                    kwargs={"_captured_generation": _captured_gen},
                                    single_flight=True,
                                )
                else:
                    # ``current_device is None`` → ``config.microphone
                    # is None`` → PortAudio opened the OS default at
                    # stream-open time. The OS-event watchers only
                    # listen for device-LIST changes — none subscribe
                    # to default-input-device-change notifications on
                    # every platform. The health-checker fills the
                    # gap by periodically re-querying the OS default
                    # input and comparing to the index captured at
                    # stream-open (or lazily on the first iteration).
                    # On mismatch, route through
                    # ``_handle_device_disconnect`` so the stream is
                    # torn down + re-opened against the new OS
                    # default. Best-effort — any query failure is
                    # logged and skipped (the next iteration retries).
                    self._check_default_input_device_changed()
            except Exception:
                log.debug("[RECORDING] Device health checker error", exc_info=True)

    def _effective_device_check_interval_s(self) -> float:
        """Return the effective health-checker interval for the current device.

        BT devices (Bluetooth HFP/HSP headsets, hands-free devices, or
        any device whose native sample rate is 8/16 kHz — the HFP/HSP
        narrowband signature) get ``_device_check_interval_s_bt``
        (default 5 s). Everything else gets ``_device_check_interval_s``
        (default 30 s). The BT classification is done via
        ``_get_max_retries_for_device`` (which checks the BT keywords +
        8/16 kHz signature) on ``_build_device_info_for_retry_policy``
        — a fresh ``sd.query_devices`` query per call.

        The cost is one ``sd.query_devices`` per loop iteration. With
        the default 30 s interval this is negligible; with the 5 s BT
        interval it's 1 call per 5 s — acceptable.

        Best-effort: if the query fails or the device info is None,
        returns the default 30 s interval (can't tell if the device
        is BT).
        """
        try:
            dev_info = self._build_device_info_for_retry_policy()
            if dev_info is not None and self._get_max_retries_for_device(dev_info) >= 6:
                return self._device_check_interval_s_bt
        except Exception:
            log.debug(
                "[RECORDING] BT-classification query failed; using default health-checker interval",
                exc_info=True,
            )
        return self._device_check_interval_s

    def record_stream_open_default_input_index(self, index: Any | None = None) -> None:
        """Record the OS default input device index at stream-open time.

        Called by ``Recorder.start()`` (via stream_lifecycle) when the
        stream is opened with ``config.microphone is None``. The
        health-checker compares this index to subsequent
        ``sd.query_devices(kind="input")["index"]`` results; on
        mismatch it routes through ``_handle_device_disconnect`` so
        the stream is torn down + re-opened against the new OS
        default.

        If ``index`` is None (the caller didn't capture it), the
        health-checker lazily captures it on the first iteration
        (similar to baseline capture in
        ``MicrophoneDeviceWatcher._check_default_device_changed``).

        Public so stream_lifecycle.py can call it; safe to call
        multiple times (the most recent value wins).
        """
        self._stream_open_default_input_index = index

    def _check_default_input_device_changed(self) -> None:
        """Detect OS default input device change when ``config.microphone is None``.

        Queries ``sd.query_devices(kind="input")`` (which PortAudio
        resolves to the OS default input) and compares its ``index``
        to ``_stream_open_default_input_index``. On the first
        successful query, captures the baseline (lazily —
        ``record_stream_open_default_input_index`` may not have been
        called by older callers). On subsequent queries, on mismatch
        routes through ``_handle_device_disconnect`` so the stream is
        torn down + re-opened against the new OS default.

        Best-effort: any query failure is logged and skipped (the
        next iteration retries). HOTKEY-CRASH: double-checks the
        recording is still active before scheduling the handler.
        """
        try:
            current_info = sd.query_devices(kind="input")
        except Exception:
            log.debug(
                "[RECORDING] sd.query_devices(kind='input') failed; "
                "skipping default-input-device change check this cycle",
                exc_info=True,
            )
            return
        if not isinstance(current_info, dict):
            return
        try:
            current_index = current_info.get("index")
        except Exception:
            return
        if current_index is None:
            return
        stream_open_index = self._stream_open_default_input_index
        # Lazily capture the baseline on the first successful query
        # (older callers may not have called
        # ``record_stream_open_default_input_index`` — the first
        # iteration establishes the baseline so a mid-session OS
        # default change is detected on subsequent iterations).
        if stream_open_index is None:
            self._stream_open_default_input_index = current_index
            return
        if current_index == stream_open_index:
            return
        log.warning(
            "[RECORDING] OS default input device changed (index %r -> %r) "
            "— routing through disconnect handler to re-open against the new OS default",
            stream_open_index,
            current_index,
        )
        # HOTKEY-CRASH: double-check recording is still active before
        # scheduling the handler — if the user already stopped the
        # recording, there's nothing to recover.
        try:
            if not self.recorder._recording_event.is_set():
                return
        except AttributeError:
            pass
        self._device_disconnected = True
        # Update the baseline so a subsequent iteration doesn't
        # re-fire (the disconnect handler will re-open against the
        # new OS default; the next stream-open will record the new
        # baseline via ``record_stream_open_default_input_index``).
        self._stream_open_default_input_index = current_index
        _captured_gen = getattr(self.recorder, "_stop_generation", 0)
        with contextlib.suppress(Exception):
            self.recorder._spawn_device_thread(
                name="device-default-input-changed",
                target=self.recorder._handle_device_disconnect,
                kwargs={"_captured_generation": _captured_gen},
                single_flight=True,
            )

    def _verify_post_restart_sample_rate(
        self,
        restart_device: Any,
        candidate_sr: Any,
    ) -> None:
        """Re-query the device's ``default_samplerate`` after a restart and
        schedule an immediate drift re-check on mismatch.

        After a successful ``restart_stream``, the new stream is open
        at ``candidate_sr`` (the rate the restart path negotiated).
        A BT HFP/HSP profile switch (e.g. A2DP → HFP) can race the
        restart: the restart re-opens at the rate the device
        advertised at restart time, but the device may switch to a
        different rate immediately after (e.g. when a phone call
        grabs the mic). The next health-checker iteration would catch
        the drift on the regular cadence (30 s, or 5 s for BT), but
        this method catches it immediately.

        On mismatch, logs a WARNING and triggers an immediate drift
        re-check by querying ``sd.query_devices(restart_device)`` and
        routing through ``_handle_device_disconnect`` if drift is
        detected (mirroring the in-loop drift-detection path).

        Best-effort: any query failure is logged and skipped (the
        next health-checker iteration will catch the drift on the
        regular cadence).

        Intended to be called by ``DisconnectHandler.restart_stream``
        (in disconnect_handler.py) after the new stream is opened.
        """
        if restart_device is None or candidate_sr is None:
            return
        try:
            post_info = sd.query_devices(restart_device)
        except Exception:
            log.debug(
                "[RECORDING] post-restart sd.query_devices(%r) failed; skipping immediate drift re-check",
                restart_device,
                exc_info=True,
            )
            return
        if not isinstance(post_info, dict):
            return
        try:
            post_sr = post_info.get("default_samplerate")
            if post_sr is None:
                return
            post_sr = float(post_sr)
            cand_sr = float(candidate_sr)
        except (TypeError, ValueError):
            return
        if abs(post_sr - cand_sr) <= 1.0:
            return
        log.warning(
            "[RECORDING] Post-restart sample-rate drift detected on device %r "
            "(candidate_sr=%r, device default_samplerate=%r) — scheduling immediate drift re-check",
            restart_device,
            candidate_sr,
            post_sr,
        )
        # Immediate drift re-check: if the recorder's ``_effective_sr``
        # still mismatches the device's current native rate, route
        # through ``_handle_device_disconnect`` so the stream is torn
        # down + re-opened at the new rate. This mirrors the in-loop
        # drift-detection path (``_detect_sample_rate_drift`` +
        # ``_spawn_device_thread``) but runs immediately after the
        # restart instead of waiting for the next 5-30 s health-checker
        # wake.
        if self._detect_sample_rate_drift(post_info):
            try:
                if not self.recorder._recording_event.is_set():
                    return
            except AttributeError:
                pass
            self._device_disconnected = True
            _captured_gen = getattr(self.recorder, "_stop_generation", 0)
            with contextlib.suppress(Exception):
                self.recorder._spawn_device_thread(
                    name="device-post-restart-drift",
                    target=self.recorder._handle_device_disconnect,
                    kwargs={"_captured_generation": _captured_gen},
                    single_flight=True,
                )

    def _detect_sample_rate_drift(self, dev_info: Any) -> bool:
        """Return True if the device's ``default_samplerate`` differs from
        the recorder's current ``_effective_sr``.

        Best-effort: any unexpected shape (non-dict ``dev_info``,
        missing ``default_samplerate`` key, non-numeric value, missing
        ``_effective_sr`` on the recorder) returns False so the health
        checker never false-positives into a disconnect-recovery loop.
        The actual tear-down + re-open is the caller's responsibility
        (it routes through ``_handle_device_disconnect`` so the restart
        path picks up the new native rate).
        """
        if not isinstance(dev_info, dict):
            return False
        try:
            current_native = dev_info.get("default_samplerate")
            if current_native is None:
                return False
            current_native = float(current_native)
        except (TypeError, ValueError):
            return False
        effective_sr = getattr(self.recorder, "_effective_sr", None)
        if effective_sr is None:
            return False
        try:
            effective_sr = float(effective_sr)
        except (TypeError, ValueError):
            return False
        # Tolerance: 1 Hz. ``default_samplerate`` is a float from
        # PortAudio (e.g. 44100.0); ``_effective_sr`` is an int from
        # our config. A direct ``==`` between int 48000 and float
        # 48000.0 is True in Python, but the small epsilon protects
        # against a 44099-vs-44100 rounding artifact some host APIs
        # report without flagging a real drift.
        return abs(current_native - effective_sr) > 1.0

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
        except AttributeError:
            # ``_recording_event`` may not be set yet during early init —
            # proceed (the handler will no-op if recording hasn't started).
            # Previously a broad ``except Exception: pass``.
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
        - ``"<host api>|<name>[#N]"`` (stable id, as emitted by
                  ``list_microphones``) → resolved via
                  ``find_microphone_by_id``, which survives reboots and
                  hot-swap renumbering because it matches on host API +
                  name instead of the unstable index.

        Resolution order for string values: exact stable-id lookup first
        (an exact match against a live device's generated id is correct
        regardless of which format produced the stored string), then the
        legacy bare-index / compound parsing below. Purely-numeric strings
        skip the enumeration entirely — they can never equal a generated
        id, and the legacy fast path must stay RPC-free.

        (Medium): PortAudio device indices are NOT stable across
                hot-swap on Windows MME. The compound form stores the device
                NAME alongside the index and the stable id stores name +
                host API so the resolver can re-find the original physical
                device by name after renumbering.

        when the saved index now points at a differently-named
                device AND name lookup failed, emit a one-time WARNING.
        """
        mic = self.recorder.config.microphone
        if mic is None:
            return None
        # Stable-id form (and any non-legacy string): try an exact match
        # against the live enumeration before falling back to the legacy
        # parsers. Skipped for bare digits — the legacy index path below
        # handles those without a PortAudio round-trip.
        if isinstance(mic, str) and not mic.isdigit():
            try:
                from voice_typer.server.server_platform.microphone_list import find_microphone_by_id

                stable_match = find_microphone_by_id(mic)
            except Exception:
                stable_match = None
            if stable_match is not None:
                try:
                    return int(stable_match["index"])
                except (KeyError, TypeError, ValueError):
                    pass
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
        # An empty/whitespace name fragment (corrupt value like "5|")
        # must skip the lookup — substring-matching "" would return the
        # first enumerated device.
        match = None
        if saved_name.strip():
            try:
                from voice_typer.server.server_platform.microphone_list import find_microphone_by_name

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
                        "[RECORDING] saved microphone index %d now points to "
                        "'%s' (was '%s') — device may have been renumbered by hot-swap; "
                        "re-select the microphone in Settings to update the saved reference",
                        saved_index,
                        current_info.get("name", ""),
                        saved_name,
                    )
                    self._device_name_mismatch_warned = True
            except Exception:
                # The mismatch warning is purely diagnostic — if we can't
                # query the saved index's current info (PortAudio raises
                # ``PortAudioError`` on a hot-swapped-out index; tests
                # raise ``RuntimeError``; either way the device is simply
                # gone), skip the warning and fall through to the
                # ``return saved_index`` fallback below. A previous
                # narrowing to ``(KeyError, TypeError, AttributeError)``
                # let ``PortAudioError`` / ``RuntimeError`` propagate and
                # crash the resolution path. Broadened to ``Exception``
                # because no caller of ``_resolve_device`` is prepared to
                # handle a raised exception from this diagnostic probe.
                # No bare ``pass`` — log with exc_info so the
                # diagnostic failure is visible in the log file.)
                log.debug(
                    "[RECORDING] could not query saved device index %r "
                    "(name mismatch warning skipped) — device likely gone",
                    saved_index,
                    exc_info=True,
                )
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
        """Return the host-API name for the given index, with a one-shot cache.

        Each ``sd.query_hostapis(idx)`` RPC costs 50-200ms on Windows
        MME. The index → name mapping is stable for the process
        lifetime (host APIs don't appear/disappear at runtime), so the
        first call for each index pays the RPC and subsequent calls
        hit the cache. Returns ``""`` on query failure (preserves the
        pre-fix best-effort semantics).
        """
        cached = self._host_api_cache.get(host_api_index)
        if cached is not None:
            return cached
        try:
            name = str(sd.query_hostapis(host_api_index)["name"])
        except Exception:
            return ""
        self._host_api_cache[host_api_index] = name
        return name

    def _cached_device_info(self, device: int | None) -> dict | None:
        """Look up the cached device info dict for ``device``.

        Returns the cached entry from ``_device_list_cache`` when the
        index is present (fast path — no PortAudio RPC). Falls back to
        a live ``sd.query_devices(device)`` query on cache miss (e.g.
        the cache is stale, the device was just hot-plugged, or the
        TTL expired between the ``_refresh_device_list`` call and this
        lookup). Returns ``None`` if both the cache lookup and the
        live query fail — callers must handle ``None`` gracefully
        (same as the pre-fix ``sd.query_devices`` exception path).

        For ``device=None`` (system default input), the cache cannot
        resolve which physical device is the OS default, so we fall
        through to the live ``sd.query_devices(kind="input")`` query
        (preserves the pre-fix behavior for the OS-default path).
        """
        if device is None:
            try:
                return sd.query_devices(kind="input")
            except Exception:
                return None
        cache = self._device_list_cache
        if cache is not None:
            for entry in cache:
                try:
                    if int(entry.get("index", -1)) == device:
                        return entry
                except (TypeError, ValueError):
                    continue
        # Cache miss — fall back to a live query. This preserves
        # correctness when the cache is stale (the device was just
        # hot-plugged and the TTL hasn't expired yet) or when the
        # cache was never populated.
        try:
            return sd.query_devices(device)
        except Exception:
            return None

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

        # Use the cached device info + cached device list instead of
        # two fresh ``sd.query_devices()`` RPCs (50-200ms each on
        # Windows MME). ``_cached_device_info`` falls back to a live
        # query on cache miss; ``_refresh_device_list`` returns the
        # cached list (refreshing if stale). The pre-fix exception
        # path is preserved: if both lookups fail, we return the
        # original ``candidates`` list (just the selected device).
        selected = self._cached_device_info(device)
        if selected is None:
            return candidates
        selected_name = selected.get("name", "").strip().lower()
        all_devices = self._refresh_device_list()
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
            # Use the cached device info (fast path — no PortAudio RPC)
            # with a live-query fallback on cache miss. The cache is
            # populated by ``_refresh_device_list`` (TTL 30s, or 5s
            # after a watcher-start failure) and now includes
            # ``default_samplerate`` + ``hostapi``, so the common case
            # (start() within 30s of the last refresh) hits the cache
            # and skips the 50-200ms ``sd.query_devices(device)`` RPC.
            # ``_host_api_name`` has its own one-shot cache (host-API
            # names are stable for the process lifetime), so the
            # ``sd.query_hostapis(idx)`` RPC is also skipped on cache
            # hit. Pre-fix, each ``_resolve_effective_sample_rate``
            # call issued 2 RPCs (query_devices + query_hostapis);
            # with 1-3 candidates on the ``start()`` critical path,
            # that was 100-1200ms of avoidable latency on Windows MME.
            dev_info = self._cached_device_info(device)
            if dev_info is None:
                # Both cache and live query failed — raise to trigger
                # the outer except branch (logs a warning, returns the
                # target rate so PortAudio does internal resampling).
                raise RuntimeError(f"Could not query device info for device {device} (cache miss + live query failed)")
            native_rate = int(dev_info.get("default_samplerate", 0))
            host_api_idx = dev_info.get("hostapi", 0)
            host_api_name = self._host_api_name(host_api_idx)
            dev_info_extra = {
                "name": dev_info.get("name", ""),
                "host_api_name": host_api_name,
                "native_rate": native_rate,
            }
            log.debug(
                "[RECORDING] Device query: name=%s, host_api=%s, native_rate=%d, target_rate=%d",
                dev_info.get("name", ""),
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
        except Exception:
            # log at WARNING (not DEBUG) so the user knows
            # the native-rate detection failed and PortAudio will do
            # internal resampling (which may introduce artifacts).
            # The exception text is deliberately NOT spliced in — it
            # repeats "Could not query device info for device ..." and
            # doubled the line length without adding information.
            log.warning(
                "[RECORDING] Device %s info unavailable — falling back to %d Hz (PortAudio resamples)",
                device,
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
