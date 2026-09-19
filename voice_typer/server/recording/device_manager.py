"""Device enumeration, hot-swap, and health-checker for :class:`Recorder`."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

from voice_typer.server._lazy_import import lazy_module

# PERF-COLDSTART-001: lazy import, sounddevice loads the PortAudio C
sd = lazy_module("sounddevice")

# All submodules use the package-level logger so log records propagate
log = logging.getLogger("voice_typer.server.recording")

# when the mic device watcher fails to start (e.g. on macOS
_DEVICE_LIST_FAST_TTL: float = 5.0


class DeviceManager:
    """Device enumeration, hot-swap, and health-checker for ``Recorder``."""

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        self.recorder = recorder

        # AUDIO-HOT: hot-plug device disconnect handling
        self._device_disconnected: bool = False
        self._device_disconnect_retries: int = 0
        self._max_disconnect_retries: int = 3
        # AUDIO-HOT: periodic device availability check, every N chunks,
        self._device_check_interval: int = 500  # check every ~500 chunks (~32s at 16Hz)
        self._device_check_counter: int = 0
        # CPU-03: dedicated device-health-checker thread state. The checker
        self._device_health_checker_thread: threading.Thread | None = None
        self._device_health_stop_event: threading.Event = threading.Event()
        self._device_check_interval_s: float = 30.0  # seconds between probes
        # BT-narrowband devices get a shorter health-check interval (5 s)
        self._device_check_interval_s_bt: float = 5.0
        # counter for periodic OS-level microphone-permission
        self._permission_check_counter: int = 0
        # With the default 30 s interval, every 2nd iteration = ~60 s.
        self._permission_check_interval: int = 2

        # AUDIO-MIC: device list cache with timestamp
        self._device_list_cache: list[dict] | None = None
        self._device_list_cache_time: float = 0.0
        self._device_list_cache_ttl: float = 30.0  # seconds

        # PERF-MIC-001: OS-event-driven cache invalidation. The watcher
        self._mic_watcher: Any | None = None
        # optional service-layer cache invalidator callback.
        self._service_cache_invalidator: Any | None = None
        # configurable sleep between BT-device disconnect retries.
        self._bt_retry_sleep_seconds: float = 0.75
        # one-shot flag so the name-mismatch warning fires at
        self._device_name_mismatch_warned: bool = False
        # Last successful ``sd.query_devices(kind="input")`` result from
        self._last_default_input_info: dict | None = None
        # OS default-input index captured at stream-open (or lazily on
        self._stream_open_default_input_index: Any | None = None
        # Lazy host-API index → name cache. Populated on first
        self._host_api_cache: dict[int, str] = {}
        try:
            from voice_typer.server.microphone_watcher import (
                MicrophoneDeviceWatcher,
            )

            # (pyrefly): bind to a local so pyrefly can see the
            watcher: Any = MicrophoneDeviceWatcher(on_change=self._invalidate_device_cache)
            watcher.start()
            self._mic_watcher = watcher
        except Exception:
            # Watcher is best-effort, the 30s TTL cache covers the
            log.warning(
                "[RECORDING] mic device watcher failed to start, falling back to 5s TTL polling for the session",
                exc_info=True,
            )
            self._mic_watcher = None

    def _refresh_device_list(self) -> list[dict]:
        """Return the device list, refreshing the cache if stale."""
        now = time.monotonic()
        # compute the effective TTL based on whether an OS-event
        if self._mic_watcher is None:
            effective_ttl: float = _DEVICE_LIST_FAST_TTL
        else:
            effective_ttl = self._device_list_cache_ttl
        if self._device_list_cache is not None and now - self._device_list_cache_time < effective_ttl:
            return self._device_list_cache

        try:
            # Shared PortAudio walk + filters with the canonical UI list
            _shared_walk = None
            try:
                from voice_typer.server.server_platform.microphone_list import (
                    iter_filtered_input_devices as _shared_walk,
                )
            except Exception:
                log.debug("[RECORDING] shared device walk unavailable, skipping name filters", exc_info=True)

            if _shared_walk is not None:
                filtered_inputs = _shared_walk(sd.query_devices())
            else:
                # Import-failure fallback: channel filter only (the
                filtered_inputs = []
                for i, dev in enumerate(sd.query_devices()):
                    if isinstance(dev, dict) and dev.get("max_input_channels", 0) > 0:
                        raw_name = dev.get("name", "")
                        name = raw_name.strip() if isinstance(raw_name, str) else ""
                        filtered_inputs.append((i, dev, name))

            devices = []
            for i, dev, _name in filtered_inputs:
                devices.append(
                    {
                        "index": i,
                        "name": dev.get("name", ""),
                        "max_input_channels": dev.get("max_input_channels", 0),
                        # Cache the native sample rate and host-API index
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
        """Register a service-layer cache invalidator ()."""
        self._service_cache_invalidator = callback

    def _invalidate_device_cache(self) -> None:
        """Reset the device-list cache so the next ``_refresh_device_list``"""
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
        if not self._device_disconnected:
            return
        try:
            if not self.recorder._recording_event.is_set():
                return
        except AttributeError:
            # ``_recording_event`` may not be set yet during early init —
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
            "[RECORDING] hot-plug event triggered disconnect recovery (device was disconnected, re-attempting restart)"
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

    def _start_device_health_checker(self) -> None:
        """Start the device health checker daemon thread."""
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
        """Signal the device health checker thread to stop and join it."""
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
            if health_exited:
                self._device_health_checker_thread = None
                self._device_health_stop_event.clear()

    def stop_device_health_checker(self, timeout: float | None = None) -> None:
        """Signal the device health checker thread to stop and join it."""
        if timeout == 0.0:
            # Fire-and-forget: signal the stop event, do NOT join. The
            self._device_health_stop_event.set()
            return
        self._stop_device_health_checker()

    def _device_health_checker_loop(self) -> None:
        """Device health checker daemon thread main loop."""
        while True:
            # Compute the effective poll interval per iteration. BT
            effective_interval = self._effective_device_check_interval_s()
            if self._device_health_stop_event.wait(timeout=effective_interval):
                return
            # skip the check if we've already detected a disconnect
            if self._device_disconnected:
                continue
            # periodic OS-level microphone-permission re-probe.
            self._permission_check_counter += 1
            if self._permission_check_counter >= self._permission_check_interval:
                self._permission_check_counter = 0
                if self._check_microphone_permission_revoked():
                    # Permission was revoked mid-recording. The
                    continue
            try:
                current_device = self._resolve_device()
                if current_device is not None:
                    try:
                        dev_info = sd.query_devices(current_device)
                    except Exception:
                        # HOTKEY-CRASH: double-check recording is still active.
                        if not self.recorder._recording_event.is_set():
                            return
                        log.warning(
                            "[RECORDING] Current device no longer available in query_devices -- disconnect detected"
                        )
                        self._device_disconnected = True
                        _captured_gen = self.recorder._stop_generation
                        # Route through ``_spawn_device_thread`` so
                        with contextlib.suppress(Exception):
                            self.recorder._spawn_device_thread(
                                name="device-disconnect-check",
                                target=self.recorder._handle_device_disconnect,
                                kwargs={"_captured_generation": _captured_gen},
                                single_flight=True,
                            )
                    else:
                        # Sample-rate drift detection. After
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
                    self._check_default_input_device_changed()
            except Exception:
                log.debug("[RECORDING] Device health checker error", exc_info=True)

    def _effective_device_check_interval_s(self) -> float:
        """Return the effective health-checker interval for the current device."""
        try:
            # ``allow_live_default_fallback=False``: on the System
            dev_info = self._build_device_info_for_retry_policy(
                allow_live_default_fallback=False,
            )
            if dev_info is not None and self._get_max_retries_for_device(dev_info) >= 6:
                return self._device_check_interval_s_bt
        except Exception:
            log.debug(
                "[RECORDING] BT-classification query failed; using default health-checker interval",
                exc_info=True,
            )
        return self._device_check_interval_s

    def record_stream_open_default_input_index(self, index: Any | None = None) -> None:
        """Record the OS default input device index at stream-open time."""
        self._stream_open_default_input_index = index

    def _check_default_input_device_changed(self, current_info: dict | None = None) -> None:
        """Detect OS default input device change when ``config.microphone is None``."""
        if current_info is None:
            current_info = self._cached_device_info(None)
            if current_info is None:
                log.debug(
                    "[RECORDING] canonical + raw default query failed; "
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
        # Stash for the next cycle's BT classification so the health
        self._last_default_input_info = current_info
        stream_open_index = self._stream_open_default_input_index
        # Lazily capture the baseline on the first successful query
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
        try:
            if not self.recorder._recording_event.is_set():
                return
        except AttributeError:
            pass
        self._device_disconnected = True
        # Update the baseline so a subsequent iteration doesn't
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
        """Re-query the device's ``default_samplerate`` after a restart and"""
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
            "(candidate_sr=%r, device default_samplerate=%r), scheduling immediate drift re-check",
            restart_device,
            candidate_sr,
            post_sr,
        )
        # Immediate drift re-check: if the recorder's ``_effective_sr``
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
        """Return True if the device's ``default_samplerate`` differs from"""
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
        return abs(current_native - effective_sr) > 1.0

    def _check_microphone_permission_revoked(self) -> bool:
        """probe the OS-level microphone permission state.

        Returns True if the permission was detected as DENIED, in which
        """
        # Lazy import to avoid paying the import cost on every loop wake
        try:
            from voice_typer.server import permissions as _permissions_mod

            state = _permissions_mod.check_microphone_permission()
        except Exception:
            log.debug(
                "[RECORDING] Microphone permission probe raised, ignoring",
                exc_info=True,
            )
            return False

        # ``MicrophonePermissionState.DENIED`` is the only state we act
        try:
            denied = state == _permissions_mod.MicrophonePermissionState.DENIED
        except Exception:
            denied = str(state).lower() == "denied"

        if not denied:
            return False

        # HOTKEY-CRASH: double-check recording is still active before
        try:
            if not self.recorder._recording_event.is_set():
                return False
        except AttributeError:
            # ``_recording_event`` may not be set yet during early init —
            pass

        log.warning(
            "[RECORDING] Microphone permission revoked mid-recording -- "
            "stopping stream and surfacing on_microphone_permission_revoked"
        )
        self._device_disconnected = True
        _captured_gen = getattr(self.recorder, "_stop_generation", 0)

        def _permission_revoked_handler(_captured_generation: int = _captured_gen) -> None:
            """Spawned on a fresh daemon thread so we don't block the"""
            try:
                cb = getattr(self.recorder, "on_microphone_permission_revoked", None)
                if callable(cb):
                    cb()
                else:
                    # Fallback: if the callback isn't wired (older
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

    def _resolve_device(self):
        """Resolve config.microphone to a sounddevice device specifier."""
        mic = self.recorder.config.microphone
        if mic is None:
            return None
        # Stable-id form (and any non-legacy string): try an exact match
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

        # Name lookup failed, fall back to the saved index.
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
                        "'%s' (was '%s'), device may have been renumbered by hot-swap; "
                        "re-select the microphone in Settings to update the saved reference",
                        saved_index,
                        current_info.get("name", ""),
                        saved_name,
                    )
                    self._device_name_mismatch_warned = True
            except Exception:
                # The mismatch warning is purely diagnostic, if we can't
                log.debug(
                    "[RECORDING] could not query saved device index %r "
                    "(name mismatch warning skipped), device likely gone",
                    saved_index,
                    exc_info=True,
                )
        return saved_index

    def _build_device_info_for_retry_policy(
        self,
        *,
        allow_live_default_fallback: bool = True,
    ) -> dict | None:
        """Return the current device info for BT retry classification."""
        try:
            current = self._resolve_device()
            if current is None:
                stashed = self._last_default_input_info
                if stashed is not None:
                    return stashed
                if not allow_live_default_fallback:
                    # Health-checker path: skip the live query. The
                    return None
                return self._cached_device_info(None)
            return self._cached_device_info(current)
        except Exception:
            return None

    def _get_max_retries_for_device(self, device_info: dict | None) -> int:
        """return the max retry count for the given device."""
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
        """return the per-retry sleep for the given device."""
        if device_info is None:
            return 0.0
        if self._get_max_retries_for_device(device_info) >= 6:
            return self._bt_retry_sleep_seconds
        return 0.0

    def _host_api_name(self, host_api_index: int) -> str:
        """Return the host-API name for the given index, with a one-shot cache."""
        cached = self._host_api_cache.get(host_api_index)
        if cached is not None:
            return cached
        try:
            name = str(sd.query_hostapis(host_api_index)["name"])
        except Exception:
            return ""
        self._host_api_cache[host_api_index] = name
        return name

    def _canonical_default_index(self) -> int | None:
        """Return the canonical default input index, or ``None``."""
        try:
            from voice_typer.server.server_platform.microphone_list import (
                get_canonical_default_index as _canonical_default,
            )

            result = _canonical_default()
            return int(result) if result is not None else None
        except (TypeError, ValueError):
            return None
        except Exception:
            log.debug("[RECORDING] canonical default index lookup failed", exc_info=True)
            return None

    def _cached_device_info(self, device: int | None) -> dict | None:
        """Look up the cached device info dict for ``device``.

        Returns the cached entry from ``_device_list_cache`` when the
        index is present (fast path, no PortAudio RPC). Falls back to
        a live ``sd.query_devices(device)`` query on cache miss (e.g.
        the cache is stale, the device was just hot-plugged, or the
        TTL expired between the ``_refresh_device_list`` call and this
        lookup). Returns ``None`` if both the cache lookup and the
        live query fail, callers must handle ``None`` gracefully
        (same as the pre-fix ``sd.query_devices`` exception path).

        For ``device=None`` (system default input), resolve through the
        canonical host-API view first so the sample-rate probe and the
        stream open agree with the UI list (WASAPI on Windows). Only
        when the canonical view has no usable default does this fall
        through to the live ``sd.query_devices(kind="input")`` query
        (preserves the pre-fix behavior for the OS-default path).

        Returns ``None`` when the resolved value is not a ``dict``
        (defensive: some test stubs / broken host-API layers return a
        list or other shape; callers contract is dict-or-None).
        """
        if device is None:
            canonical = self._canonical_default_index()
            if canonical is not None:
                resolved = self._cached_device_info(canonical)
                if resolved is not None:
                    return resolved
            try:
                info = sd.query_devices(kind="input")
            except Exception:
                return None
            return info if isinstance(info, dict) else None
        cache = self._device_list_cache
        if cache is not None:
            for entry in cache:
                try:
                    if int(entry.get("index", -1)) == device:
                        return entry
                except (TypeError, ValueError):
                    continue
        # Cache miss, fall back to a live query. This preserves
        try:
            info = sd.query_devices(device)
        except Exception:
            return None
        return info if isinstance(info, dict) else None

    def _device_index(self, fallback_index: int, device_info: dict) -> int:
        try:
            return int(device_info.get("index", fallback_index))
        except Exception:
            return fallback_index

    def _same_physical_microphone_candidates(self, device: Any) -> list[Any]:
        """Return equivalent input device IDs to try if the selected one fails."""
        candidates = [device]
        if device is None:
            # Null means System Default (fresh-install default): resolve it
            try:
                canonical = self._canonical_default_index()
                if canonical is not None:
                    return [canonical]
            except Exception:
                log.debug(
                    "[RECORDING] canonical default lookup failed, using OS default",
                    exc_info=True,
                )
            return candidates
        if not isinstance(device, int):
            return candidates

        # Use the cached device info + cached device list instead of
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
        """REC-6: rank host APIs for fallback device selection."""
        lower = host_name.lower()
        # Windows hosts
        if "wasapi" in lower:
            return 0
        if lower == "mme":
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
        # Unknown host, lowest priority but not last (leaves room for
        return 5

    def _resolve_effective_sample_rate(self, device: int | None) -> tuple[int, dict | None]:
        """Determine the effective sample rate and device info for the given device.

        Returns (effective_sr, dev_info_dict) where dev_info_dict has
        """
        target_sr = self.recorder.config.sample_rate  # 16000 for Whisper
        dev_info_extra = None
        try:
            # Use the cached device info (fast path, no PortAudio RPC)
            dev_info = self._cached_device_info(device)
            if dev_info is None:
                # Both cache and live query failed. Raise to trigger
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
                "[RECORDING] Device query: name=%s, host_api=%s | native_rate=%d, target_rate=%d",
                dev_info.get("name", ""),
                host_api_name,
                native_rate,
                target_sr,
            )

            # If the device's native rate matches the target, use it directly.
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
            log.warning(
                "[RECORDING] Device %s info unavailable, falling back to %d Hz (PortAudio resamples)",
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
