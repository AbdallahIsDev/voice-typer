"""Microphone watcher — Linux polling implementation.

Provides :class:`_LinuxMixin` (mixed into ``MicrophoneDeviceWatcher``)
with the ``/dev/snd`` + secondary ``sd.query_devices()`` polling loop,
plus the shared ``_device_signature`` helper and default-input-device
change detection.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class _LinuxMixin:
    # Secondary PulseAudio/PipeWire-level poll cadence. The primary
    # ``/dev/snd`` poll is cheap (``os.listdir`` ≈ µs) but only sees
    # ALSA kernel devices. ``sd.query_devices()`` (a 10–50 ms
    # PortAudio round trip on Linux PulseAudio/PipeWire) sees BT
    # headsets and virtual sources that never touch ``/dev/snd``. We
    # throttle the secondary poll to the idle/active cadence
    # (selected via ``self._is_idle`` — see :meth:`set_idle`) so the
    # test-suite's small ``poll_interval`` values (e.g. 0.05 s) don't
    # multiply the PortAudio cost AND so the secondary poll widens to
    # 12 s at idle / tightens to 3 s during a recording (mirroring
    # ``_run_macos``'s cadence selection).

    def _run_linux(self) -> None:
        """Watch ``/dev/snd`` for changes by polling directory listing.

        Uses ``os.listdir`` + ``frozenset`` comparison at the
        configured poll interval. This deliberately avoids
        ``pyinotify``/``inotify_simple`` to keep the dependency
        surface minimal — PortAudio's 30s cache is the ultimate
        fallback if this loop misses an event.

        On modern desktop Linux the user-facing audio stack is
        PulseAudio or PipeWire, which exposes Bluetooth headsets, USB
        mics, and virtual sources as userspace sources — NOT as new
        ALSA kernel devices in ``/dev/snd``. A Bluetooth headset
        pairing produces a PulseAudio source
        ``bluez_source.XX_XX_XX_XX_XX_XX`` without touching
        ``/dev/snd``. To catch these, a secondary
        ``sd.query_devices()`` poll runs every
        ``_active_poll_interval_s`` (default 3 s) during a recording
        or every ``_idle_poll_interval_s`` (default 12 s) at idle —
        selected via :meth:`set_idle`. It diffs the device signature
        set (mirroring ``_run_macos``'s approach). The ``/dev/snd``
        poll catches ALSA-level events with low CPU; the
        ``sd.query_devices()`` poll catches PulseAudio/PipeWire-level
        events with ~10–50 ms CPU every 3–12 s.

        Also runs ``_check_default_device_changed()`` each cycle so
        the OS default input device change is detected when
        ``config.microphone is None``.
        """
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
            import sounddevice as _sd  # noqa: F401 — used inside loop

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
        """Build a hashable signature for a sounddevice device entry.

        Comparing only ``len(sd.query_devices())`` misses same-count device
        swaps — e.g. a USB mic unplugged at the same moment a Bluetooth
        headset is plugged in (count stays at 3 → 3). The signature
        includes ``name``, ``hostapi``, and ``default_samplerate`` so any
        such swap is detected even when the count is unchanged.

        Uses ``dict.get`` so partial device entries (e.g. those returned
        by the unit-test mocks that only populate ``name``) do not raise
        ``KeyError``. ``None`` values are valid set members and compare
        equal only to themselves, so stable-but-partial entries still
        produce a stable signature across polls.
        """
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
            try:
                self._on_default_device_changed()
            except Exception:
                log.exception("[MIC-WATCHER] _on_default_device_changed callback raised")

    def _query_default_input_device(self) -> Any:
        """Wrapper around ``sd.query_devices(kind='input')`` for test mocking."""
        sd = self._sd if hasattr(self, "_sd") else None
        if sd is None:
            import voice_typer.server.recording as recording_mod

            sd = recording_mod.sd
        return sd.query_devices(kind="input")
