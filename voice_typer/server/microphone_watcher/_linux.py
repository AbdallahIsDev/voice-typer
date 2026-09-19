"""Microphone watcher. Linux polling implementation."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

log = logging.getLogger(__name__)


class _LinuxMixin:
    # Members provided by the composed ``MicrophoneDeviceWatcher``
    _stop_event: threading.Event
    _poll_interval: float
    _idle_poll_interval_s: float
    _active_poll_interval_s: float
    _is_idle: bool
    _on_default_device_changed: Callable[[], None] | None

    if TYPE_CHECKING:
        # Method provided by ``_core.py`` in the composed MRO; a
        def _invoke_callback(self) -> None: ...
    # Secondary PulseAudio/PipeWire-level poll cadence. The primary

    def _run_linux(self) -> None:
        """Watch ``/dev/snd`` for changes by polling directory listing."""
        import os

        snd_dir = "/dev/snd"
        if not os.path.isdir(snd_dir):
            log.debug(
                "[MIC-WATCHER] %s not found, falling back to TTL polling",
                snd_dir,
            )
            return

        try:
            last_entries = frozenset(os.listdir(snd_dir))
        except OSError as e:
            log.debug(
                "[MIC-WATCHER] cannot list %s (%s), falling back to TTL polling",
                snd_dir,
                e,
            )
            return

        last_sd_sig: set | None = None
        last_sd_query_monotonic: float = time.monotonic()
        try:
            import sounddevice as _sd  # noqa: F401, used inside loop

            sd_available = True
        except ImportError:
            sd_available = False

        log.debug(
            "[MIC-WATCHER] watching %s (%d entries, sd.query_devices poll %s)",
            snd_dir,
            len(last_entries),
            "enabled" if sd_available else "disabled (sounddevice unavailable)",
        )
        while not self._stop_event.wait(self._poll_interval):
            try:
                current = frozenset(os.listdir(snd_dir))
            except OSError:
                current = last_entries
            if current != last_entries:
                log.debug(
                    "[MIC-WATCHER] %s entries changed (%d -> %d), invalidating cache",
                    snd_dir,
                    len(last_entries),
                    len(current),
                )
                last_entries = current
                self._invoke_callback()

            now = time.monotonic()
            sd_query_interval = self._idle_poll_interval_s if self._is_idle else self._active_poll_interval_s
            if sd_available and (now - last_sd_query_monotonic) >= sd_query_interval:
                last_sd_query_monotonic = now
                try:
                    import sounddevice as _sd

                    current_devices = _sd.query_devices()
                    current_sd_sig = {self._device_signature(d) for d in current_devices}
                except Exception:
                    current_sd_sig = None
                if current_sd_sig is not None:
                    if last_sd_sig is not None and current_sd_sig != last_sd_sig:
                        log.debug(
                            "[MIC-WATCHER] sd.query_devices signature set changed (%d -> %d), invalidating cache",
                            len(last_sd_sig),
                            len(current_sd_sig),
                        )
                        self._invoke_callback()
                    last_sd_sig = current_sd_sig

            self._check_default_device_changed()

    @staticmethod
    def _device_signature(dev: Any) -> tuple:
        """Build a hashable signature for a sounddevice device entry."""
        if not isinstance(dev, dict):
            return (id(dev),)
        return (
            dev.get("name"),
            dev.get("hostapi"),
            dev.get("default_samplerate"),
        )

    def _check_default_device_changed(self) -> None:
        """Detect OS default-input-device changes and fire the callback."""
        if self._on_default_device_changed is None:
            return
        try:
            current = self._query_default_input_device()
            current_index = current.get("index") if isinstance(current, dict) else getattr(current, "index", None)
        except Exception:
            log.debug("[MIC-WATCHER] default-device check failed", exc_info=True)
            return
        if self._last_default_input_index is None:
            self._last_default_input_index = current_index
            return
        if current_index != self._last_default_input_index:
            self._last_default_input_index = current_index
            callback = self._on_default_device_changed
            if callback is None:
                return
            try:
                callback()
            except Exception:
                log.exception("[MIC-WATCHER] _on_default_device_changed callback raised")

    def _query_default_input_device(self) -> Any:
        """Wrapper around ``sd.query_devices(kind='input')`` for test mocking."""
        sd = self._sd if hasattr(self, "_sd") else None
        if sd is None:
            import voice_typer.server.recording as recording_mod

            sd = recording_mod.sd
        return sd.query_devices(kind="input")
