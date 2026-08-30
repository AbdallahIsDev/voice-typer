"""Microphone watcher — macOS polling implementation.

Provides :class:`_MacOSMixin` (mixed into ``MicrophoneDeviceWatcher``)
with the ``sounddevice.query_devices()`` polling loop and the
``CoreAudioMicrophoneWatcher`` construction attempt.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class _MacOSMixin:
    def _run_macos(self) -> None:
        """Watch for CoreAudio device changes by polling ``sounddevice``."""
        try:
            import sounddevice as sd
        except ImportError:
            log.debug("[MIC-WATCHER] sounddevice not importable on macOS, falling back to TTL polling")
            return

        try:
            initial_devices = sd.query_devices()
            last_count: int | None = len(initial_devices)
            last_sig: set | None = {self._device_signature(d) for d in initial_devices}
        except Exception:
            last_count = None
            last_sig = None
            log.debug(
                "[MIC-WATCHER] initial sd.query_devices() failed, deferring baseline capture",
                exc_info=True,
            )

        if self._poll_interval < 1.0:
            effective_poll = self._poll_interval
        else:
            effective_poll = self._idle_poll_interval_s if self._is_idle else self._active_poll_interval_s
        log.debug(
            "[MIC-WATCHER] watching macOS device count (initial=%s, poll=%.1fs, idle=%s)",
            last_count,
            effective_poll,
            self._is_idle,
        )
        while not self._stop_event.wait(effective_poll):
            try:
                current_devices = sd.query_devices()
                current_count = len(current_devices)
                current_sig = {self._device_signature(d) for d in current_devices}
            except Exception:
                log.debug(
                    "[MIC-WATCHER] macOS poll failed, skipping cycle",
                    exc_info=True,
                )
                continue
            if (
                last_count is not None
                and last_sig is not None
                and (current_count != last_count or current_sig != last_sig)
            ):
                log.debug(
                    "[MIC-WATCHER] macOS device set changed (count %d -> %d), invalidating cache",
                    last_count,
                    current_count,
                )
                self._invoke_callback()
            last_count = current_count
            last_sig = current_sig
