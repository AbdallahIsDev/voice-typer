"""Microphone device-change watcher, shared state, lifecycle, and callback dispatch."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from voice_typer.server.microphone_watcher._linux import _LinuxMixin
from voice_typer.server.microphone_watcher._macos import _MacOSMixin
from voice_typer.server.microphone_watcher._windows import _WindowsMixin

log = logging.getLogger(__name__)


class MicrophoneDeviceWatcher(_LinuxMixin, _MacOSMixin, _WindowsMixin):
    """Watches for microphone device changes and invalidates the cache."""

    def __init__(
        self,
        on_change: Callable[[], None],
        poll_interval: float = 5.0,
    ) -> None:
        self._on_change = on_change
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._platform = self._detect_platform()
        self._lock = threading.Lock()
        self._coreaudio_watcher: Any | None = None
        self._hooks_lock = threading.Lock()
        self._active_mic_id: Any | None = None
        self._on_active_mic_lost: Callable[[], None] | None = None
        self._device_id_provider: Callable[[], list[Any]] | None = None
        self._is_idle: bool = True
        self._idle_poll_interval_s: float = 12.0
        self._active_poll_interval_s: float = 3.0
        self._on_default_device_changed: Callable[[], None] | None = None
        self._last_default_input_index: Any | None = None

    def set_on_default_device_changed(self, callback: Callable[[], None] | None) -> None:
        self._on_default_device_changed = callback
        self._last_default_input_index = None

    def set_idle(self, is_idle: bool) -> None:
        self._is_idle = bool(is_idle)
        log.debug(
            "[MIC-WATCHER] set_idle(%s), poll cadence now %ss",
            is_idle,
            self._idle_poll_interval_s if is_idle else self._active_poll_interval_s,
        )

    def _detect_platform(self) -> str:
        from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

        if is_windows():
            return "windows"
        if is_macos():
            return "macos"
        if is_linux():
            return "linux"
        return "unknown"

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._coreaudio_watcher is not None:
                return
            if self._platform not in ("windows", "linux", "macos"):
                log.debug(
                    "[MIC-WATCHER] Platform %s not supported, falling back to TTL polling",
                    self._platform,
                )
                return
            if self._platform == "macos":
                ca_watcher = self._try_create_coreaudio_watcher()
                if ca_watcher is not None:
                    try:
                        ca_watcher.start()
                    except Exception:
                        log.warning(
                            "[MIC-WATCHER] CoreAudio watcher start failed, falling back to sounddevice polling",
                            exc_info=True,
                        )
                        self._coreaudio_watcher = None
                    else:
                        self._coreaudio_watcher = ca_watcher
                        log.info("[MIC-WATCHER] Using CoreAudio property-listener watcher (event-driven)")
                        return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="mic-device-watcher",
            )
            self._thread.start()
            if self._platform == "windows":
                log.debug(
                    "[MIC-WATCHER] Starting device-change watcher for %s",
                    self._platform,
                )
            else:
                log.info(
                    "[MIC-WATCHER] Started device-change watcher for %s",
                    self._platform,
                )

    def _try_create_coreaudio_watcher(self) -> Any | None:
        try:
            from voice_typer.server.microphone_watcher_coreaudio import (  # type: ignore[import-untyped]
                CoreAudioMicrophoneWatcher,
            )
        except ImportError:
            log.debug(
                "[MIC-WATCHER] microphone_watcher_coreaudio module unavailable, falling back to sounddevice polling"
            )
            return None
        try:
            return CoreAudioMicrophoneWatcher(self._invoke_callback, poll_interval=self._poll_interval)
        except ImportError:
            log.debug(
                "[MIC-WATCHER] CoreAudioMicrophoneWatcher unavailable "
                "(pyobjc not installed or not on macOS), falling back "
                "to sounddevice polling"
            )
            return None
        except Exception:
            log.warning(
                "[MIC-WATCHER] CoreAudioMicrophoneWatcher construction "
                "raised unexpectedly, falling back to sounddevice polling",
                exc_info=True,
            )
            return None

    def stop(self) -> None:
        with self._lock:
            ca_watcher = self._coreaudio_watcher
            if ca_watcher is not None:
                try:
                    ca_watcher.stop()
                except Exception:
                    log.warning(
                        "[MIC-WATCHER] CoreAudio watcher stop failed",
                        exc_info=True,
                    )
                self._coreaudio_watcher = None
                log.info("[MIC-WATCHER] Stopped CoreAudio watcher")
                return
            if self._thread is None:
                return
            self._stop_event.set()
            if self._platform == "windows":
                try:
                    self._post_quit_to_windows()
                except Exception:
                    log.debug("[MIC-WATCHER] WM_QUIT post failed", exc_info=True)
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                log.warning(
                    "[MIC-WATCHER] Watcher thread did not exit within 2s "
                    "(it is a daemon and will not block process exit)"
                )
            self._thread = None
            log.info("[MIC-WATCHER] Stopped device-change watcher")

    def _run(self) -> None:
        try:
            if self._platform == "windows":
                self._run_windows()
            elif self._platform == "linux":
                self._run_linux()
            elif self._platform == "macos":
                self._run_macos()
        except Exception:
            log.warning(
                "[MIC-WATCHER] Watcher thread crashed, falling back to TTL polling",
                exc_info=True,
            )

    _DEBOUNCE_SECONDS = 0.5

    def _invoke_callback(self) -> None:
        now = time.monotonic()
        last = getattr(self, "_last_callback_time", 0.0)
        if now - last < self._DEBOUNCE_SECONDS:
            log.debug(
                "[MIC-WATCHER] Skipping duplicate invalidation (%.0fms since last)",
                (now - last) * 1000,
            )
            return
        self._last_callback_time = now

        try:
            self._on_change()
        except Exception:
            log.warning(
                "[MIC-WATCHER] Invalidation callback raised",
                exc_info=True,
            )

        try:
            from voice_typer.server.server_platform import invalidate_microphone_list_cache

            invalidate_microphone_list_cache()
        except Exception:
            log.debug(
                "[MIC-WATCHER] platform microphone-list cache invalidation skipped",
                exc_info=True,
            )
        self._check_active_mic_lost()

    def set_active_mic_id(self, mic_id: Any) -> None:
        with self._hooks_lock:
            self._active_mic_id = mic_id

    def set_on_active_mic_lost(self, callback: Callable[[], None]) -> None:
        with self._hooks_lock:
            self._on_active_mic_lost = callback

    def set_device_id_provider(self, provider: Callable[[], list[Any]]) -> None:
        with self._hooks_lock:
            self._device_id_provider = provider

    def _check_active_mic_lost(self) -> None:
        with self._hooks_lock:
            active_mic_id = self._active_mic_id
            on_active_mic_lost = self._on_active_mic_lost
            device_id_provider = self._device_id_provider
        if active_mic_id is None or on_active_mic_lost is None or device_id_provider is None:
            return
        try:
            current_ids = list(device_id_provider())
        except Exception:
            log.warning(
                "[MIC-WATCHER] device_id_provider raised; skipping active-mic-lost check this cycle",
                exc_info=True,
            )
            return
        if active_mic_id not in current_ids:
            log.info(
                "[MIC-WATCHER] Active mic %r no longer in device list "
                "(%d devices available), firing on_active_mic_lost",
                active_mic_id,
                len(current_ids),
            )
            try:
                on_active_mic_lost()
            except Exception:
                log.warning(
                    "[MIC-WATCHER] on_active_mic_lost callback raised",
                    exc_info=True,
                )
