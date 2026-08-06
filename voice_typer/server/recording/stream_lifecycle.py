"""PortAudio stream open/teardown for :class:`Recorder` (extracted from ``recorder.py``).

Phase 4.5 — extracted from :mod:`.recorder` to shrink the
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

- ``self._recorder._stream`` — the PortAudio InputStream
- ``self._recorder.config`` — for ``microphone`` / ``sample_rate``
- ``self._recorder._effective_sr`` / ``_actual_channels`` /
  ``_buffer_sr`` — sample-rate tracking
- ``self._recorder._audio_processor`` — filter chain (for set_sample_rate)
- ``self._recorder._classify_portaudio_open_error`` — error classifier
- ``self._recorder._resolve_effective_sample_rate`` — sample-rate resolver (delegates to DeviceManager)
- ``self._recorder._all_input_device_candidates`` — last-resort device list (delegates to DeviceManager)
- ``self._recorder._recording_event`` — recording gate
- ``self._recorder._audio_callback_dispatch`` — the real-time callback (delegates to AudioCallbackDispatcher)
- ... and any other state referenced in the extracted bodies

Patch-path compatibility
------------------------
Tests use ``monkeypatch.setattr(recording.sd, "InputStream", fake)`` and
similar to inject fake sounddevice behavior. The lazy ``sd`` proxy
re-resolves ``sys.modules`` on every access, so the patch propagates
here automatically — no ``_recording_pkg.sd`` indirection needed.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from voice_typer.server._audio_constants import (
    _AUDIO_BLOCKSIZE,
    _TEARDOWN_CALLBACK_DRAIN_BUDGET_S,
    _TEARDOWN_CALLBACK_POLL_INTERVAL_S,
    SILERO_VAD_SAMPLE_RATES,
)
from voice_typer.server._lazy_import import lazy_module

# PERF-COLDSTART-001: lazy import — sounddevice loads the PortAudio C
# library at import time. The lazy proxy re-resolves ``sys.modules`` on
# every attribute access, so test patches of the form
# ``monkeypatch.setattr(recording.sd, "InputStream", fake)`` propagate
# here automatically.
sd = lazy_module("sounddevice")

# All submodules use the package-level logger so log records propagate
# to ``caplog.at_level(..., logger="voice_typer.server.recording")`` in
# tests.
log = logging.getLogger("voice_typer.server.recording")

if TYPE_CHECKING:
    pass


class StreamLifecycle:
    """PortAudio stream open/teardown for :class:`Recorder`.

    Phase 4.5 — extracted from :mod:`.recorder`. See the module
        docstring for the collaborator-pattern rationale.
    """

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        # import (``recorder`` imports ``stream_lifecycle`` at module top
        # to construct this class in ``Recorder.__init__``).
        self._recorder = recorder

    def open_stream_for_candidates(
        self,
        recorder: Any,
        candidates: list[Any],
        callback: Any,
        effective_sr: int,
        last_error: Exception | None,
    ) -> tuple[Any, int, Exception | None]:
        """Body of :meth:`Recorder._open_stream_for_candidates`.

        Try opening an :class:`sd.InputStream` for each candidate device
        in turn. Returns ``(selected_device, effective_sr, last_error)``.
        On success, ``recorder._stream`` is the opened stream,
        ``recorder._effective_sr`` is updated under the lock, and
        ``recorder._actual_channels`` stores the negotiated channel count.
        On failure, ``recorder._stream`` remains ``None`` and ``last_error``
        holds the most recent exception.

        The candidate loop is the primary device-enumeration path. If
        every candidate fails, :meth:`Recorder.start` falls back to
        :meth:`open_stream_fallback` (all input devices).
        """
        selected_device: Any = None
        for candidate in candidates:
            candidate_sr, dev_info_extra = recorder._resolve_effective_sample_rate(candidate)

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
                # AUDIO-CH: query device's max input channels.
                # If device only supports stereo, use channels=2
                # and convert to mono in the callback via _ensure_mono.
                # If config.recording_channels > 0, use that value
                # instead of auto-detecting (allows user override).
                # recording_channels is a Config dataclass field
                # (default 1) — always present on a real Config instance,
                # so the getattr fallback could never fire. The ``or 1``
                # guard is preserved because recording_channels=0 is an
                # invalid misconfig that would produce a zero-channel
                # stream — defensive against misconfig, not missing attr.
                config_channels = int(recorder.config.recording_channels or 1)
                channels = config_channels if config_channels > 0 else 1
                try:
                    # PERF: consult the cached device list (pre-warmed in
                    # ``__init__`` via ``_prewarm_device_cache``) instead
                    # of issuing a fresh ``sd.query_devices()`` RPC per
                    # candidate. Each RPC is 50-200ms on Windows MME; with
                    # 1-3 candidates the savings are 1-3 RPCs on the
                    # hotkey critical path.
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
                    pass

                stream = sd.InputStream(
                    samplerate=candidate_sr,
                    channels=channels,
                    dtype=np.float32,
                    device=candidate,
                    callback=callback,
                    # VAD-001: request 512-sample blocks so Silero VAD
                    # gets the exact chunk size it expects. PortAudio
                    # may still deliver a different size on some drivers,
                    # but vad.py now pads/truncates to handle that.
                    blocksize=_AUDIO_BLOCKSIZE,
                    # Request the host API's "low" latency hint.
                    # On ALSA/CoreAudio/WASAPI this selects the smallest
                    # viable buffer (10-20 ms end-to-end callback latency).
                    # PortAudio silently falls back to the default if the
                    # requested latency is unavailable (PA clamps
                    # suggestedLatency to [0, defaultLowInputLatency]).
                    latency="low",
                    # AUDIO-HOT: finished_callback detects unexpected stream termination
                    finished_callback=recorder._stream_finished_callback,
                )
                stream.start()

                # AUDIO-BT: detect Bluetooth HFP profile (8/16 kHz).
                # After opening the stream, check if the actual sample
                # rate differs from requested and is 8000 or 16000.
                try:
                    actual_sr = int(stream.samplerate) if hasattr(stream, "samplerate") else candidate_sr
                    if actual_sr in SILERO_VAD_SAMPLE_RATES and actual_sr != candidate_sr:
                        # AUDIO-BT: detecting a Bluetooth HFP (hands-free
                        # telephony) profile is EXPECTED behaviour for a BT
                        # headset — it is not a fault or misconfiguration.
                        # Demoted from WARNING to INFO so the default log
                        # isn't littered with a non-error on every BT mic
                        # connection.
                        log.info(
                            "[RECORDING] Bluetooth HFP profile detected: actual sample rate "
                            "%d Hz differs from requested %d Hz. Audio quality will be limited. "
                            "Consider disabling the hands-free telephony profile in Bluetooth "
                            "settings for better quality.",
                            actual_sr,
                            candidate_sr,
                        )
                except Exception:
                    pass

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
                recorder._stream = None
                continue

            recorder._stream = stream
            # guard _effective_sr writes with the lock because
            # snapshot() reads it under the lock from another thread.
            with recorder._lock:
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
        """Body of :meth:`Recorder._open_stream_fallback`.

        Try every available input device not already in ``candidates``
        (the already-tried list) as a last-resort fallback. Returns
        ``(selected_device, effective_sr, used_fallback, last_error)``.
        On success, ``recorder._stream`` is the opened stream and
        ``recorder._effective_sr`` is updated under the lock.
        ``used_fallback`` is ``True`` if a fallback device opened
        successfully, ``False`` otherwise (so the caller can distinguish
        the primary-success and fallback-success cases — only the
        fallback-success case persists the new device index to config).
        """
        selected_device: Any = None
        used_fallback = False
        log.warning(
            "[RECORDING] All devices matching configured mic failed. Trying all available input devices as fallback."
        )
        all_candidates = recorder._all_input_device_candidates()
        # Remove already-tried devices
        tried_set = set(str(c) for c in candidates)
        all_candidates = [c for c in all_candidates if str(c) not in tried_set]

        for candidate in all_candidates:
            candidate_sr, dev_info_extra = recorder._resolve_effective_sample_rate(candidate)

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
                # PERF: use the cached lookup (same rationale as the
                # primary candidate loop above) — the fallback path
                # iterates ALL input devices, so per-candidate RPC
                # savings compound quickly here.
                fb_channels = 1
                try:
                    fb_max_ch = recorder._cached_max_input_channels(candidate)
                    if fb_max_ch >= 2:
                        fb_channels = 2
                except Exception:
                    pass

                stream = sd.InputStream(
                    samplerate=candidate_sr,
                    channels=fb_channels,
                    dtype=np.float32,
                    device=candidate,
                    callback=callback,
                    # VAD-001: request 512-sample blocks for Silero VAD
                    blocksize=_AUDIO_BLOCKSIZE,
                    # Request the host API's "low" latency hint
                    # (mirrors the primary open_stream_for_candidates call;
                    # PortAudio silently falls back if unavailable).
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

            recorder._stream = stream
            # guard _effective_sr writes with the lock.
            with recorder._lock:
                recorder._effective_sr = candidate_sr
            selected_device = candidate
            effective_sr = candidate_sr
            used_fallback = True
            # (pyrefly): ``dev_info_extra`` is typed
            # ``dict | None`` because ``_resolve_effective_sample_rate``
            # may return None when PortAudio can't enumerate the
            # device. The earlier ``if dev_info_extra:`` gate
            # protects the first access (logging at line ~1505),
            # but this post-success log was unguarded — calling
            # ``["name"]`` on None would raise ``TypeError`` here
            # after a *successful* stream open. Fall back to a
            # placeholder so the log line still fires.
            fb_name = dev_info_extra["name"] if dev_info_extra else "(unknown)"
            log.info(
                "[RECORDING] Fallback succeeded with device [%s] %s",
                candidate,
                fb_name,
            )
            break

        return selected_device, effective_sr, used_fallback, last_error

    def build_audio_callback(self, recorder: Any) -> Any:
        """Body of :meth:`Recorder._build_audio_callback`.

                Construct the PortAudio callback closure for this session.

        The PortAudio callback is a thin wrapper around
                :meth:`Recorder._audio_callback_dispatch`. The dispatch method
                does ONLY pre-roll capture + ring buffer push + worker signal —
                all heavy work (filter chain, VAD, resample, state machine) is
                done by the audio worker thread. See
                ``Recorder._audio_callback_dispatch`` / ``_audio_worker_loop``
                / ``_process_audio_chunk`` for the full architecture.

                The closure captures ``recorder`` only — no other start()-locals
                — so it is safe to extract from ``start()`` into a helper that
                returns the closure. ``recorder._current_callback`` is set here
                so :meth:`Recorder._handle_device_disconnect` can re-bind the
                same callback when restarting the stream.
        """

        def callback(indata, frames, time_info, status):
            # guard flag for in-flight callback.
            # _teardown_stream() polls this flag for up to 300ms before
            # calling stream.close() to avoid use-after-free if the
            # callback is still running. With the RT-safe refactor, the
            # callback is ~10µs (copy + deque append + Event.set), so
            # the flag is almost always clear by the time teardown runs.
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
                ``_stream_lifecycle_lock`` block — the lock acquisition stays on
                ``Recorder`` for source-inspection contracts).

                Stop + close the PortAudio stream, draining any in-flight
                callback.

        17-H-: extracted from ``stop()`` so ``discard()`` shares the
                same callback-drain contract. Without the poll, ``discard()``
                could call ``stream.close()`` while the audio callback (firing
                ~16×/s) was still running — risking use-after-free or deadlock
                when ESC-cancel landed mid-callback.

                Behavior:
                  1. If ``recorder._stream`` is None, return immediately (idempotent).
                  2. Call ``stream.stop()`` (CLEAN) or ``stream.abort()`` (force)
                     to halt PortAudio's callback dispatch.
                  3. Poll ``_is_in_audio_callback`` for up to 300ms (5ms interval)
                     until the in-flight callback (if any) returns.
                  4. Call ``stream.close()`` to free PortAudio resources.
                  5. Set ``recorder._stream = None``.

                Idempotent: safe to call when the stream is already None (e.g.
                when ``discard()`` is invoked twice, or after ``stop()``).

        ``force=True`` selects the disconnect-recovery path. When
                the device is KNOWN to be gone (called from
                ``Recorder._handle_device_disconnect``), ``stream.stop()``
                blocks indefinitely waiting for pending buffers that will
                never drain. ``stream.abort()`` returns immediately
                (PortAudio discards the buffers). Both ``abort()`` and
                ``close()`` are best-effort on the force path — failures
                are suppressed so the disconnect-recovery critical path
                can't be blocked by a stuck PortAudio stream, and
                ``_stream`` is always cleared so the next ``start()``
                opens a fresh stream. The CLEAN path (``force=False``,
                the default — used by ``stop()`` / ``discard()`` /
                ``__del__``) keeps ``stream.stop()`` + ``stream.close()``
                with exception propagation for graceful drain.

        the caller (``Recorder._teardown_stream``) wraps this body
                in ``recorder._stream_lifecycle_lock`` (acquired with non-blocking
                ``acquire(blocking=False)``) so a concurrent
                ``_handle_device_disconnect`` restart block cannot mutate
                ``recorder._stream`` mid-teardown (and vice-versa). The lock
        acquisition stays on ``Recorder`` so the  source-inspection
                regression tests (``tests/test_recorder_worker_lifecycle.py``)
                continue to pin the lock-scope invariant on
                ``Recorder._teardown_stream``.
        """
        if not recorder._stream:
            return
        if force:
            # Known-dead-device path (disconnect handler).
            # ``abort()`` returns immediately without waiting for
            # pending buffers to drain — unlike ``stop()`` which blocks
            # indefinitely on a dead device. Both ``abort()`` and
            # ``close()`` are best-effort here: the device is already
            # gone, so failures are suppressed to keep the recovery
            # critical path moving, and ``_stream`` is always cleared
            # so the next ``start()`` opens a fresh stream.
            with contextlib.suppress(Exception):
                recorder._stream.abort()
            # The drain poll is moot after ``abort()`` (PortAudio
            # guarantees no further callback dispatch), but kept as a
            # safety net for any in-flight callback that started before
            # ``abort()`` took effect. Fast-path: skip the deadline
            # computation + poll loop entirely when the callback flag
            # is already clear on the first check (the common case —
            # the RT callback is ~10µs so the flag is almost always
            # clear by the time teardown runs).
            if recorder._is_in_audio_callback.is_set():
                _deadline = time.perf_counter() + _TEARDOWN_CALLBACK_DRAIN_BUDGET_S
                while recorder._is_in_audio_callback.is_set():
                    remaining = _deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    time.sleep(min(_TEARDOWN_CALLBACK_POLL_INTERVAL_S, remaining))
            with contextlib.suppress(Exception):
                recorder._stream.close()
            recorder._stream = None
            return
        # CLEAN path (stop from hotkey / discard / __del__) — graceful
        # drain via ``stop()`` so pending buffers complete before
        # ``close()``. Exceptions from ``stop()`` / ``close()`` propagate
        # to the caller (``Recorder._teardown_stream`` → its ``finally``
        # releases the lock).
        recorder._stream.stop()
        # wait briefly for any in-flight audio
        # callback to complete before closing the stream. This prevents
        # PortAudio from calling the callback during/after stream.stop()
        # which can cause use-after-free or deadlock.
        #
        # PERF- (Round 0): the previous "exponential backoff"
        # implementation was inverted. It used::
        #
        #     if self._is_in_audio_callback.wait(timeout=_timeout):
        #         break  # callback completed
        #
        # but ``threading.Event.wait(timeout)`` returns ``True`` when the
        # flag is *set* — and the flag is set while the callback is
        # *running* (see lines 1082/1086: set on entry, clear on exit).
        # So the loop broke immediately when the callback WAS running
        # (defeating the safety guard) and blocked for the full
        # 20+30+50+80+130+200 = 510ms when the callback was NOT running
        # (the common case).  Every dictation paid a half-second penalty.
        #
        # The fix: poll for the flag to become *clear* (callback not
        # running), with a 5ms interval and a 300ms hard budget (matching
        # the original 6×50ms worst case).  On a healthy system the flag
        # is already clear on the first check → 0ms wait.  When the
        # callback genuinely runs past ``stream.stop()``, the poll loop
        # waits for it to finish (restoring the
        # safety contract).
        # magic numbers extracted to module constants
        # (``_TEARDOWN_CALLBACK_DRAIN_BUDGET_S`` /
        # ``_TEARDOWN_CALLBACK_POLL_INTERVAL_S``) so they can be tuned /
        # referenced from tests without grep-and-replace.
        #
        # Fast-path: skip the deadline computation + poll loop entirely
        # when the callback flag is already clear on the first check.
        # The existing ``while`` loop already short-circuits on the
        # first iteration (``is_set()`` returns False → body never
        # runs), but the explicit ``if`` guard also skips the
        # ``time.perf_counter()`` call + deadline arithmetic — a tiny
        # but non-zero saving on every stop() (the common case is that
        # the RT callback is ~10µs and has already returned by the time
        # teardown runs). On a healthy system the fast-path fires
        # ~100% of the time; the slow path only fires when the callback
        # is genuinely in-flight (e.g. a slow driver callback past
        # ``stream.stop()``).
        if recorder._is_in_audio_callback.is_set():
            _deadline = time.perf_counter() + _TEARDOWN_CALLBACK_DRAIN_BUDGET_S
            while recorder._is_in_audio_callback.is_set():
                remaining = _deadline - time.perf_counter()
                if remaining <= 0:
                    break
                time.sleep(min(_TEARDOWN_CALLBACK_POLL_INTERVAL_S, remaining))
        recorder._stream.close()
        recorder._stream = None
