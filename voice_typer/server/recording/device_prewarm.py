"""Device-cache prewarm + stream-open validation for :class:`Recorder`.

Phase 4.5 completion — the device-prewarm bodies that previously lived
on :class:`Recorder` (``_prewarm_device_cache`` /
``_prewarm_input_stream`` / ``_cached_max_input_channels`` /
``_classify_portaudio_open_error``) are owned by
:class:`DevicePrewarm` (the owning collaborator, constructed by
``RecorderInitMixin`` and stored at ``recorder._device_prewarm``).
``Recorder`` keeps documented 1-line delegators so existing call sites
and source-inspection consumers keep working.

Collaborator pattern
--------------------
:class:`DevicePrewarm` is constructed with a back-reference to the
owning ``Recorder`` instance (``DevicePrewarm(recorder)``). The
collaborator accesses shared state that lives on ``Recorder`` /
``DeviceManager`` and is NOT moved here:

- ``self._recorder._devices`` — :class:`.device_manager.DeviceManager`
  (``_refresh_device_list`` / ``_resolve_device`` /
  ``_resolve_effective_sample_rate``)
- ``self._recorder._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS`` — the
  PortAudio permission-denial substring table (owned by ``Recorder``)

Patch-path compatibility
------------------------
Tests patch ``Recorder._prewarm_input_stream`` at the class level
(``monkeypatch.setattr(Recorder, "_prewarm_input_stream", ...)``) and
call the ``Recorder`` delegators (``r._prewarm_device_cache()`` etc.);
the delegators on ``Recorder`` route here, so patches on ``Recorder``
methods keep taking effect and patches of ``recording.sd`` propagate
through this module's ``sd`` lazy proxy.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any

from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE
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


class DevicePrewarm:
    """Device-cache prewarm + PortAudio warm-up for :class:`Recorder`.

    Owns the device-prewarm bodies extracted from :class:`Recorder`
    (Phase 4.5 completion). See the module docstring for the
    collaborator-pattern rationale.
    """

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        # import (``recorder`` imports this module at module top to
        # construct this class in ``RecorderInitMixin``).
        self._recorder = recorder

    def prewarm_device_cache(self) -> None:
        """Spawn a best-effort daemon thread to populate ``DeviceManager._device_list_cache``.

        PERF (recorder hot-path): ``start()``'s device-enumeration block
        performs several ``sd.query_devices()`` RPCs per candidate (50-200ms
        each on Windows MME). ``DeviceManager._refresh_device_list`` is the
        cached path (30s TTL, OS-event-invalidated by
        ``MicrophoneDeviceWatcher``), but the cache is cold at construction
        time. Pre-warming it on a background thread means that by the time
        the user presses the dictation hotkey (typically seconds-to-minutes
        after app launch), the cache is warm and ``cached_max_input_channels``
        returns instantly.

        The thread is a one-shot daemon (no stop mechanism, no join needed).
        If PortAudio is unavailable (headless CI, no audio HW), the cache
        stays empty and ``start()`` falls back to direct
        ``sd.query_devices()`` calls — no regression.

        In addition to warming the device-list cache, the prewarm thread
        also opens a brief ``sd.InputStream`` against the configured mic
        (via :meth:`prewarm_input_stream`). This validates the device, warms
        PortAudio's internal device-state cache (so the first ``start()``
        doesn't pay the full Pa_OpenStream + Pa_StartStream cost), and
        surfaces permission errors at app launch instead of at first
        hotkey. Failures are logged at INFO and never propagated — the
        prewarm is purely best-effort.
        """

        def _warm() -> None:
            try:
                self._recorder._devices._refresh_device_list()
            except Exception:
                log.debug("[RECORDING] device cache pre-warm failed", exc_info=True)
            # Phase 2: briefly open + start + stop + close an InputStream
            # against the configured mic. This is the actual "warm"
            # operation — the device-list cache only avoids query RPCs,
            # not the open/start cost. See ``prewarm_input_stream`` for
            # the rationale and timeout guard.
            #
            # Routed through ``recorder._prewarm_input_stream()`` (the
            # documented Recorder delegator) — NOT this collaborator's
            # method directly — so the class-level test patch
            # (``monkeypatch.setattr(Recorder, "_prewarm_input_stream", ...)``)
            # keeps intercepting the prewarm probe.
            self._recorder._prewarm_input_stream()

        threading.Thread(
            target=_warm,
            name="recorder-device-cache-prewarm",
            daemon=True,
        ).start()
        # the prewarm thread is intentionally NOT routed through
        # ``recorder._spawn_device_thread`` because it's spawned from
        # ``__init__`` (before ``_thread_registry`` could be wired by a
        # caller) and is a one-shot best-effort daemon. The disconnect-path
        # spawns ARE routed through the helper for registry + single-flight.

    def prewarm_input_stream(self, *, timeout_s: float = 2.0) -> None:
        """Briefly open + start + stop + close an InputStream to warm PortAudio.

        Resolves the configured mic via ``_resolve_device()``, opens a
        brief ``sd.InputStream(...)`` with the resolved sample rate, calls
        ``stream.start()`` then immediately ``stream.stop()`` +
        ``stream.close()``. This validates the device, warms PortAudio's
        internal device-state cache, and surfaces permission errors at
        app launch instead of at first hotkey press.

        The open/start/stop/close sequence runs on a NESTED daemon thread
        joined with a 2s timeout — if the device is stuck (e.g. a flaky
        BT headset), the prewarm thread returns without blocking process
        startup. The nested thread is a daemon so it never blocks process
        exit. Failures are logged at INFO (not WARNING) because the
        prewarm is purely best-effort: a failure here is recovered by the
        normal ``start()`` candidate loop on the first hotkey press.

        No ``_stream_lifecycle_lock`` is acquired: the prewarm opens a
        THROWAWAY stream (local to this method) and does NOT touch
        ``recorder._stream`` (owned by
        :class:`.stream_lifecycle.StreamLifecycle`), so the lock (which
        protects ``recorder._stream`` from concurrent ``_teardown_stream``
        / ``_handle_device_disconnect``) is not needed. Acquiring the lock
        here would block tests that hold the lock for setup.
        """
        recorder = self._recorder
        result: dict[str, Any] = {"done": threading.Event(), "ok": False, "err": None}

        def _do_open() -> None:
            try:
                device = recorder._devices._resolve_device()
                candidate_sr, _dev_info = recorder._devices._resolve_effective_sample_rate(device)
                prewarm_stream = sd.InputStream(
                    samplerate=candidate_sr,
                    channels=1,
                    dtype="float32",
                    device=device,
                    # No callback — the stream is opened only to warm
                    # PortAudio's device state and validate permissions.
                    # Passing ``callback=None`` makes sounddevice use an
                    # internal no-op callback (PortAudio still
                    # initializes the stream + allocates buffers).
                    callback=None,
                    blocksize=_AUDIO_BLOCKSIZE,
                    latency="low",
                )
                prewarm_stream.start()
                try:
                    prewarm_stream.stop()
                finally:
                    with contextlib.suppress(Exception):
                        prewarm_stream.close()
                log.info(
                    "[RECORDING] Input stream prewarm succeeded: device=[%s] samplerate=%d",
                    device if device is not None else "default",
                    candidate_sr,
                )
                result["ok"] = True
            except Exception as e:
                result["err"] = e
            finally:
                result["done"].set()

        worker = threading.Thread(
            target=_do_open,
            name="recorder-stream-prewarm",
            daemon=True,
        )
        worker.start()
        # Bound the wait so a stuck device doesn't stall the prewarm
        # thread (which itself is a daemon — the wait is defensive
        # against the rare case where the prewarm thread was joined
        # by a caller that expected it to terminate quickly).
        if not result["done"].wait(timeout=timeout_s):
            log.info(
                "[RECORDING] Input stream prewarm timed out after %.1fs "
                "(device may be stuck — the first start() will retry)",
                timeout_s,
            )
            return
        if not result["ok"] and result["err"] is not None:
            log.info(
                "[RECORDING] Stream prewarm skipped: %s",
                result["err"],
            )

    def cached_max_input_channels(self, device: int | None) -> int:
        """Return ``max_input_channels`` for ``device`` from the cached device list.

        PERF (recorder hot-path): avoids a 50-200ms ``sd.query_devices()``
        RPC per candidate on the ``start()`` critical path. The cache is
        owned by ``DeviceManager._refresh_device_list`` (30s TTL,
        invalidated on OS device plug/unplug events by
        ``MicrophoneDeviceWatcher``) and pre-warmed by
        :meth:`prewarm_device_cache` in ``__init__``.

        Falls back to ``sd.query_devices(kind="input")`` for
        ``device=None`` (the cache lists all input devices but does not
        track which one is the OS default) and to ``1`` (mono) when the
        device is not in the cache (e.g. a USB mic that was just plugged
        in and the cache hasn't been invalidated yet — the next
        iteration's ``sd.InputStream`` open will retry).
        """
        if device is None:
            # Cache doesn't track OS default; fall back to a single direct
            # query (one RPC, only on the default-device path which is the
            # minority case — most users configure an explicit mic index).
            try:
                info = sd.query_devices(kind="input")
                return int(info.get("max_input_channels", 1) or 1)
            except Exception:
                return 1
        try:
            for info in self._recorder._devices._refresh_device_list():
                if info.get("index") == device:
                    return int(info.get("max_input_channels", 1) or 1)
        except (KeyError, TypeError, ValueError, AttributeError, OSError):
            # PortAudio query failed, device dict shape drift, or
            # ``_devices`` not yet initialized. Fall back to 1 channel
            # (PortAudio's default).
            pass
        return 1

    def classify_portaudio_open_error(self, exc: BaseException) -> None:
        """Re-raise an OSError-from-PortAudio as a typed
        :class:`MicrophonePermissionDeniedError` when the OS reports
        the microphone permission as ``DENIED`` or ``PROMPT`` AND the
        OSError message matches one of the known PortAudio
        permission-denial substrings.

        Non-OSError exceptions are passed through unchanged. OSErrors
        whose message doesn't match any substring are passed through
        unchanged (likely a hardware fault, not a permission issue).
        OSErrors that match the substring but whose permission state
        is ``GRANTED`` / ``UNKNOWN`` are passed through unchanged
        (avoid false-positive permission prompts when the real cause
        is hardware, or pyobjc is missing on macOS so we can't be sure).
        """
        recorder = self._recorder
        if not isinstance(exc, OSError):
            return
        msg = str(exc).lower()
        if not any(pat.lower() in msg for pat in recorder._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS):
            return
        from voice_typer.server import permissions
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        state = permissions.check_microphone_permission()
        if state == permissions.MicrophonePermissionState.DENIED:
            raise MicrophonePermissionDeniedError(
                "PortAudio reports microphone permission denied",
                state="denied",
            ) from exc
        if state == permissions.MicrophonePermissionState.PROMPT:
            raise MicrophonePermissionDeniedError(
                "Microphone permission not yet determined - PortAudio open failed",
                state="prompt",
            ) from exc
        return
