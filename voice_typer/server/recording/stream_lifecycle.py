"""PortAudio stream open/teardown for :class:`Recorder` (extracted from ``recorder.py``).

Extracted from :mod:`.recorder` to shrink the
3772-LOC ``recorder.py`` god class (see  in ``review.md``).
Owns the stream-open candidate-iteration loop, the all-devices
fallback loop, the PortAudio callback closure construction, and the
stream teardown body (inside the lifecycle lock).

Collaborator pattern
--------------------
:class:`StreamLifecycle` is constructed by ``Recorder.__init__`` with a
back-reference to the owning ``Recorder`` instance
(``StreamLifecycle(recorder)``). The collaborator reference is used to
access *shared* state that lives on ``Recorder`` and is NOT moved here:

- ``self._stream``: the PortAudio InputStream (owned by THIS
  lifecycle, STATE-OWNERSHIP; the recorder-level slot was removed)
- ``self._recorder.config``: for ``microphone`` / ``sample_rate``
- ``self._recorder._effective_sr`` / ``_actual_channels`` /
  ``_buffer_sr``: sample-rate tracking
- ``self._recorder._audio_processor``: filter chain (for set_sample_rate)
- ``self._recorder._classify_portaudio_open_error``, error classifier
- ``self._recorder._devices._resolve_effective_sample_rate``, sample-rate resolver (DeviceManager)
- ``self._recorder._devices._all_input_device_candidates``, last-resort device list (DeviceManager)
- ``self._recorder._recording_event``: recording gate
- ``self._recorder._audio_callback_dispatch``: the real-time callback (delegates to AudioCallbackDispatcher)
- ... and any other state referenced in the extracted bodies

Patch-path compatibility
------------------------
Tests use ``monkeypatch.setattr(recording.sd, "InputStream", fake)`` and
similar to inject fake sounddevice behavior. The lazy ``sd`` proxy
re-resolves ``sys.modules`` on every access, so the patch propagates
here automatically, no ``_recording_pkg.sd`` indirection needed.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import (
    _TEARDOWN_CALLBACK_DRAIN_BUDGET_S,
    _TEARDOWN_CALLBACK_POLL_INTERVAL_S,
    SILERO_VAD_SAMPLE_RATES,
    scaled_audio_blocksize,
)
from voice_typer.server._lazy_import import lazy_module

# PERF-COLDSTART-001: lazy import, sounddevice loads the PortAudio C
sd = lazy_module("sounddevice")

# All submodules use the package-level logger so log records propagate
log = logging.getLogger("voice_typer.server.recording")

if TYPE_CHECKING:
    pass


class StreamLifecycle:
    """PortAudio stream open/teardown for :class:`Recorder`.

    Extracted from :mod:`.recorder`. See the module
        docstring for the collaborator-pattern rationale.
    """

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        self._recorder = recorder
        # STATE-OWNERSHIP: the PortAudio ``InputStream`` slot
        self._stream: Any = None

    def open_stream_for_candidates(
        self,
        recorder: Any,
        candidates: list[Any],
        callback: Any,
        effective_sr: int,
        last_error: Exception | None,
    ) -> tuple[Any, int, Exception | None]:
        """Open an ``sd.InputStream`` for each candidate device (a
        ``StreamLifecycle`` method invoked directly by
        ``_recorder_split.start_recording``; the historical
        ``Recorder._open_stream_for_candidates`` pure delegator was removed).

        Try opening an :class:`sd.InputStream` for each candidate device
        in turn. Returns ``(selected_device, effective_sr, last_error)``.
        On success, ``self._stream`` (the lifecycle-owned slot) is the
        opened stream, ``recorder._effective_sr`` is updated under the lock, and
        ``recorder._actual_channels`` stores the negotiated channel count.
        On failure, ``self._stream`` remains ``None`` and ``last_error``
        holds the most recent exception.

        The candidate loop is the primary device-enumeration path. If
        every candidate fails, :meth:`Recorder.start` falls back to
        :meth:`open_stream_fallback` (all input devices).
        """
        selected_device: Any = None
        for candidate in candidates:
            candidate_sr, dev_info_extra = recorder._devices._resolve_effective_sample_rate(candidate)

            if dev_info_extra:
                log.info(
                    "[RECORDING] Using device: [%s] %s | host_api=%s | native_rate=%d | effective_rate=%d",
                    candidate if candidate is not None else "default",
                    dev_info_extra["name"],
                    dev_info_extra["host_api_name"],
                    dev_info_extra["native_rate"],
                    candidate_sr,
                )

            stream = None
            try:
                # stream, defensive against misconfig, not missing attr.
                config_channels = int(recorder.config.recording_channels or 1)
                channels = config_channels if config_channels > 0 else 1
                try:
                    # PERF: consult the cached device list (pre-warmed in
                    max_ch = recorder._cached_max_input_channels(candidate)
                    if config_channels <= 0:
                        # 0 = auto-detect: prefer mono, fallback to device default
                        if max_ch >= 2:
                            channels = 2  # prefer stereo if available, downmix in callback
                        elif max_ch == 1:
                            channels = 1
                    elif channels > max_ch:
                        channels = max(1, max_ch)  # don't request more than device supports
                except Exception:
                    # Channel probe failure falls back to the device default;
                    log.debug(
                        "[RECORDING] channel probe failed for device %r, using default channel count",
                        candidate,
                        exc_info=True,
                    )

                stream = sd.InputStream(
                    samplerate=candidate_sr,
                    channels=channels,
                    dtype=np.float32,
                    device=candidate,
                    callback=callback,
                    # Rate-scaled: request ~32 ms blocks so each
                    blocksize=scaled_audio_blocksize(candidate_sr),
                    # Request the host API's "low" latency hint.
                    latency="low",
                    # AUDIO-HOT: finished_callback detects unexpected stream termination
                    finished_callback=recorder._stream_finished_callback,
                )
                stream.start()

                # AUDIO-BT: detect Bluetooth HFP profile (8/16 kHz).
                try:
                    actual_sr = int(stream.samplerate) if hasattr(stream, "samplerate") else candidate_sr
                    if actual_sr in SILERO_VAD_SAMPLE_RATES and actual_sr != candidate_sr:
                        # AUDIO-BT: detecting a Bluetooth HFP (hands-free
                        log.info(
                            "[RECORDING] Bluetooth HFP profile detected: actual sample rate "
                            "%d Hz differs from requested %d Hz. Audio quality will be limited. "
                            "Consider disabling the hands-free telephony profile in Bluetooth "
                            "settings for better quality.",
                            actual_sr,
                            candidate_sr,
                        )
                except Exception:
                    # BT quality detection is advisory only, but a persistent
                    log.debug("[RECORDING] Bluetooth HFP profile probe failed", exc_info=True)

                # AUDIO-CH: store actual channel count for callback
                recorder._actual_channels = channels
            except Exception as e:
                last_error = e
                log.warning(
                    "[RECORDING] Failed to open input device [%s]: %s",
                    candidate if candidate is not None else "default",
                    e,
                )
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()
                self._stream = None
                continue

            self._stream = stream
            # guard _effective_sr writes with the lock because
            with recorder._audio_pipeline._lock:
                recorder._effective_sr = candidate_sr
            selected_device = candidate
            effective_sr = candidate_sr
            break

        return selected_device, effective_sr, last_error

    def open_stream_fallback(
        self,
        recorder: Any,
        candidates: list[Any],
        callback: Any,
        effective_sr: int,
        last_error: Exception | None,
    ) -> tuple[Any, int, bool, Exception | None]:
        """Last-resort fallback over every available input device (a
        ``StreamLifecycle`` method invoked directly by
        ``_recorder_split.start_recording``; the historical
        ``Recorder._open_stream_fallback`` pure delegator was removed).

        Try every available input device not already in ``candidates``
        (the already-tried list) as a last-resort fallback. Returns
        ``(selected_device, effective_sr, used_fallback, last_error)``.
        On success, ``self._stream`` (lifecycle-owned) is the opened stream and
        ``recorder._effective_sr`` is updated under the lock.
        ``used_fallback`` is ``True`` if a fallback device opened
        successfully, ``False`` otherwise (so the caller can distinguish
        the primary-success and fallback-success cases, only the
        fallback-success case persists the new device index to config).
        """
        selected_device: Any = None
        used_fallback = False
        log.warning(
            "[RECORDING] All devices matching configured mic failed. Trying all available input devices as fallback."
        )
        all_candidates = recorder._devices._all_input_device_candidates()
        # Remove already-tried devices
        tried_set = set(str(c) for c in candidates)
        all_candidates = [c for c in all_candidates if str(c) not in tried_set]

        for candidate in all_candidates:
            candidate_sr, dev_info_extra = recorder._devices._resolve_effective_sample_rate(candidate)

            if dev_info_extra:
                log.info(
                    "[RECORDING] Fallback device: [%s] %s | host_api=%s | native_rate=%d | effective_rate=%d",
                    candidate,
                    dev_info_extra["name"],
                    dev_info_extra["host_api_name"],
                    dev_info_extra["native_rate"],
                    candidate_sr,
                )

            stream = None
            try:
                # AUDIO-CH: also query channels for fallback devices.
                fb_channels = 1
                try:
                    fb_max_ch = recorder._cached_max_input_channels(candidate)
                    if fb_max_ch >= 2:
                        fb_channels = 2
                except Exception:
                    # Same channel-probe failure contract as the primary
                    log.debug(
                        "[RECORDING] channel probe failed for fallback device %r, using mono",
                        candidate,
                        exc_info=True,
                    )

                stream = sd.InputStream(
                    samplerate=candidate_sr,
                    channels=fb_channels,
                    dtype=np.float32,
                    device=candidate,
                    callback=callback,
                    # Rate-scaled: ~32 ms blocks, same rationale
                    blocksize=scaled_audio_blocksize(candidate_sr),
                    # Request the host API's "low" latency hint
                    latency="low",
                    # AUDIO-HOT: finished_callback detects unexpected stream termination
                    finished_callback=recorder._stream_finished_callback,
                )
                stream.start()
            except Exception as e:
                last_error = e
                log.warning(
                    "[RECORDING] Fallback device [%s] also failed: %s",
                    candidate,
                    e,
                )
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()
                continue

            self._stream = stream
            # guard _effective_sr writes with the lock.
            with recorder._audio_pipeline._lock:
                recorder._effective_sr = candidate_sr
            selected_device = candidate
            effective_sr = candidate_sr
            used_fallback = True
            # (pyrefly): ``dev_info_extra`` is typed
            fb_name = dev_info_extra["name"] if dev_info_extra else "(unknown)"
            log.info(
                "[RECORDING] Fallback succeeded with device [%s] %s",
                candidate,
                fb_name,
            )
            break

        return selected_device, effective_sr, used_fallback, last_error

    def build_audio_callback(self, recorder: Any) -> Any:
        """Construct the PortAudio callback closure for this session (a
        ``StreamLifecycle`` method invoked directly by
        ``_recorder_split.start_recording``; the historical
        ``Recorder._build_audio_callback`` pure delegator was removed).

                Construct the PortAudio callback closure for this session.

        The PortAudio callback is a thin wrapper around
                :meth:`Recorder._audio_callback_dispatch`. The dispatch method
                does ONLY pre-roll capture + ring buffer push + worker signal —
                all heavy work (filter chain, VAD, resample, state machine) is
                done by the audio worker thread. See
                ``Recorder._audio_callback_dispatch`` /
                ``AudioCallbackDispatcher.audio_worker_loop`` /
                ``AudioPipeline.process_audio_chunk`` for the full architecture.

                The closure captures ``recorder`` only, no other start()-locals
               , so it is safe to extract from ``start()`` into a helper that
                returns the closure. ``recorder._current_callback`` is set here
                so :meth:`Recorder._handle_device_disconnect` can re-bind the
                same callback when restarting the stream.
        """

        def callback(indata, frames, time_info, status):
            # guard flag for in-flight callback.
            recorder._is_in_audio_callback.set()
            try:
                recorder._audio_callback_dispatch(indata, frames, time_info, status)
            finally:
                recorder._is_in_audio_callback.clear()

        # AUDIO-HOT: store callback reference for device restart
        recorder._current_callback = callback
        return callback

    def teardown_stream_body(self, recorder: Any, *, force: bool = False) -> None:
        """Body of :meth:`Recorder._teardown_stream` (inside the
                ``_stream_lifecycle_lock`` block, the lock acquisition stays on
                ``Recorder`` for source-inspection contracts).

                Stop + close the PortAudio stream, draining any in-flight
                callback.

        17-H-: extracted from ``stop()`` so ``discard()`` shares the
                same callback-drain contract. Without the poll, ``discard()``
                could call ``stream.close()`` while the audio callback (firing
                ~16×/s) was still running, risking use-after-free or deadlock
                when ESC-cancel landed mid-callback.

                Behavior:
                  1. If ``self._stream`` is None, return immediately (idempotent).
                  2. Call ``stream.stop()`` (CLEAN) or ``stream.abort()`` (force)
                     to halt PortAudio's callback dispatch.
                  3. Poll ``_is_in_audio_callback`` for up to 300ms (5ms interval)
                     until the in-flight callback (if any) returns.
                  4. Call ``stream.close()`` to free PortAudio resources.
                  5. Set ``self._stream = None``.

                Idempotent: safe to call when the stream is already None (e.g.
                when ``discard()`` is invoked twice, or after ``stop()``).

        ``force=True`` selects the disconnect-recovery path. When
                the device is KNOWN to be gone (called from
                ``Recorder._handle_device_disconnect``), ``stream.stop()``
                blocks indefinitely waiting for pending buffers that will
                never drain. ``stream.abort()`` returns immediately
                (PortAudio discards the buffers). Both ``abort()`` and
                ``close()`` are best-effort on the force path, failures
                are suppressed so the disconnect-recovery critical path
                can't be blocked by a stuck PortAudio stream, and
                ``_stream`` is always cleared so the next ``start()``
                opens a fresh stream. The CLEAN path (``force=False``,
                the default, used by ``stop()`` / ``discard()`` /
                ``__del__``) keeps ``stream.stop()`` + ``stream.close()``
                with exception propagation for graceful drain.

        the caller (``Recorder._teardown_stream``) wraps this body
                in ``recorder._stream_lifecycle_lock`` (acquired with non-blocking
                ``acquire(blocking=False)``) so a concurrent
                ``_handle_device_disconnect`` restart block cannot mutate
                ``self._stream`` mid-teardown (and vice-versa). The lock
        acquisition stays on ``Recorder`` so the  source-inspection
                regression tests (``tests/test_recorder_worker_lifecycle.py``)
                continue to pin the lock-scope invariant on
                ``Recorder._teardown_stream``.
        """
        if not self._stream:
            return
        if force:
            # Known-dead-device path (disconnect handler).
            with contextlib.suppress(Exception):
                self._stream.abort()
            # The drain poll is moot after ``abort()`` (PortAudio
            if recorder._is_in_audio_callback.is_set():
                _deadline = time.perf_counter() + _TEARDOWN_CALLBACK_DRAIN_BUDGET_S
                while recorder._is_in_audio_callback.is_set():
                    remaining = _deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    time.sleep(min(_TEARDOWN_CALLBACK_POLL_INTERVAL_S, remaining))
            with contextlib.suppress(Exception):
                self._stream.close()
            self._stream = None
            return
        # CLEAN path (stop from hotkey / discard / __del__), graceful
        self._stream.stop()
        # wait briefly for any in-flight audio
        if recorder._is_in_audio_callback.is_set():
            _deadline = time.perf_counter() + _TEARDOWN_CALLBACK_DRAIN_BUDGET_S
            while recorder._is_in_audio_callback.is_set():
                remaining = _deadline - time.perf_counter()
                if remaining <= 0:
                    break
                time.sleep(min(_TEARDOWN_CALLBACK_POLL_INTERVAL_S, remaining))
        self._stream.close()
        self._stream = None


np = lazy_module("numpy")
