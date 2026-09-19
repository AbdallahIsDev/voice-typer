"""Device-disconnect recovery for Recorder."""

from __future__ import annotations

import collections
import contextlib
import logging
import threading
import time
from typing import Any

# The mid-session device-reconnect restart opens its stream with the
from voice_typer.server._audio_constants import scaled_audio_blocksize
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.recording.session_state import coerce_max_recording_time
from voice_typer.server.recording.vad_helpers import refresh_vad_caches

# PERF-COLDSTART-001: lazy import, sounddevice loads the PortAudio C
sd = lazy_module("sounddevice")

# All submodules use the package-level logger so log records propagate
log = logging.getLogger("voice_typer.server.recording")


# ``retune_audio_processor`` consolidates the inline retune block
def retune_audio_processor(
    proc: object,
    effective_sr: int,
    config: object,
    *,
    context: str = "on start",
) -> None:
    """Retune an :class:`AudioProcessor` to a new device's native sample rate."""
    if proc is None:
        return
    _proc_sr = getattr(proc, "_sample_rate", None)
    if _proc_sr is None or int(_proc_sr) == int(effective_sr):
        return
    _set_sr = getattr(proc, "set_sample_rate", None)
    if callable(_set_sr):
        try:
            _set_sr(int(effective_sr))
            log.info(
                "[RECORDING] AudioProcessor.set_sample_rate(%d) called %s, chain retuned to device native rate",
                effective_sr,
                context,
            )
        except Exception:
            log.warning(
                "[RECORDING] retune_audio_processor failed %s, "
                "set_sample_rate(%d) failed; per-chunk resample will run on the worker thread",
                context,
                effective_sr,
                exc_info=True,
            )
    else:
        try:
            proc.rebuild_from_config(config)  # type: ignore[attr-defined]
            log.info(
                "[RECORDING] AudioProcessor.rebuild_from_config called %s, "
                "chain rebuilt (fallback, set_sample_rate unavailable)",
                context,
            )
        except Exception:
            log.warning(
                "[RECORDING] retune_audio_processor failed %s, "
                "rebuild_from_config failed; filter coefficients may be mistuned",
                context,
                exc_info=True,
            )


class DisconnectHandler:
    """Handles audio device hot-swap stream restart for :class:`Recorder`."""

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        self._recorder = recorder
        self._single_flight_lock = threading.Lock()
        self._single_flight_running: bool = False

    def spawn_device_thread(
        self,
        recorder: Any,
        name: str,
        target: Any,
        kwargs: dict[str, Any] | None = None,
        *,
        single_flight: bool = False,
    ) -> bool:
        """Spawn a daemon device-path thread, registered with the thread registry."""
        if single_flight:
            # STATE-OWNERSHIP: the single-flight lock + flag live on
            with self._single_flight_lock:
                # if the disconnect flag was already cleared
                if not recorder._devices._device_disconnected:
                    self._single_flight_running = False
                if self._single_flight_running:
                    log.debug(
                        "[RECORDING] %s spawn suppressed, handler already running (single-flight)",
                        name,
                    )
                    return False
                self._single_flight_running = True

            def _guarded_target(**kw: Any) -> None:
                try:
                    target(**kw)
                finally:
                    with self._single_flight_lock:
                        self._single_flight_running = False

            _target: Any = _guarded_target
        else:
            _target = target

        try:
            _thread = threading.Thread(
                target=_target,
                kwargs=kwargs or {},
                name=name,
                daemon=True,
            )
            _thread.start()
        except Exception:
            log.debug("[RECORDING] %s spawn failed", name, exc_info=True)
            # If single_flight flagged us as running but the spawn
            if single_flight:
                with self._single_flight_lock:
                    self._single_flight_running = False
            return False

        # register with thread_registry when available so
        if recorder._thread_registry is not None:
            try:
                recorder._thread_registry.register(
                    name=name,
                    thread=_thread,
                    stop_event=None,
                    join_timeout=0.5,
                )
            except Exception:
                log.debug(
                    "[RECORDING] %s thread_registry.register failed",
                    name,
                    exc_info=True,
                )
        return True

    def stream_finished_callback_body(self, recorder: Any) -> None:
        """Body of :meth:`Recorder._stream_finished_callback`.

        Promoted from ``Recorder._stream_finished_callback`` (Phase 4.5
        completion), the body is unchanged. ``recorder._stream_finished_callback``
        (the documented 1-line delegator, the sounddevice
        ``finished_callback`` target) routes here.

        ``_spawn_device_thread`` is invoked through
        ``recorder._spawn_device_thread(...)`` (the Recorder-level seam)
        so the instance-level ``MagicMock`` interception in
        ``tests/test_recorder_worker_lifecycle.py::TestStreamFinishedCallbackGeneration``
        keeps working.

        sounddevice's finished_callback fires when the PortAudio stream
        stops for any reason: including device disconnection, driver
        error, or explicit stop(). We check whether we expected the
        stream to stop; if not, it was likely an unexpected device
        disconnect. Note: sd.InputStream does NOT support an
        error_callback parameter. The finished_callback is the correct
        way to detect stream termination in sounddevice. The primary
        disconnect detection is done in the audio callback via
        zero-filled indata detection (see
        ``AudioCallbackDispatcher.dispatch_callback_body``).
        """
        # Surfacing the true cause of a callback-driven stream abort.
        captured_err = recorder._capture._last_callback_error
        if captured_err is not None:
            recorder._capture._last_callback_error = None
            log.error(
                "[RECORDER] stream finished due to callback exception",
                exc_info=captured_err,
            )
            # The stream aborted because of a code bug, not a device
            return
        if recorder._devices._device_disconnected:
            return  # already handling disconnect via callback detection
        # STREAM-FIX: if stop() set this flag, the stream
        if recorder._user_stop_pending:
            return
        # If the stream stopped but we didn't call stop() ourselves,
        if recorder._stream_lifecycle._stream is not None and not recorder._recording_event.is_set():
            log.warning("[RECORDING] Stream finished unexpectedly, possible device disconnect")
            recorder._devices._device_disconnected = True
            # capture the current stop_generation so the handler
            _captured_gen = recorder._stop_generation
            # use the device-thread spawn helper so the handler is
            recorder._spawn_device_thread(
                name="stream-finished-handler",
                target=recorder._handle_device_disconnect,
                kwargs={"_captured_generation": _captured_gen},
                single_flight=True,
            )

    def restart_stream(self, _captured_generation: int) -> None:
        """Open a fresh ``sd.InputStream`` on a fallback device."""
        recorder = self._recorder
        _restart_device = None
        _configured_device = recorder._devices._resolve_device()
        if _configured_device is None:
            try:
                canonical_candidates = recorder._devices._same_physical_microphone_candidates(None)
            except Exception:
                canonical_candidates = [None]
            if canonical_candidates and canonical_candidates[0] is not None:
                _restart_device = canonical_candidates[0]
                log.info(
                    "[RECORDING] Restart: using canonical default device index %s",
                    _restart_device,
                )
        if _configured_device is not None:
            _named_candidates = recorder._devices._same_physical_microphone_candidates(_configured_device)
            # BT headsets that drop and reconnect within the
            _original_index = _named_candidates[0] if _named_candidates else None
            _expected_name = ""
            if _original_index is not None:
                _orig_info = recorder._devices._cached_device_info(_original_index)
                if _orig_info is not None:
                    _expected_name = str(_orig_info.get("name", "")).strip().lower()
                    if _expected_name:
                        # The original index is present and named —
                        _restart_device = _original_index
                        log.info(
                            "[RECORDING] Restart: original device index %s reconnected (name='%s')",
                            _original_index,
                            _orig_info.get("name", ""),
                        )
                    else:
                        log.debug(
                            "[RECORDING] Restart: original device index %s has empty name, skipping",
                            _original_index,
                        )
                else:
                    log.debug(
                        "[RECORDING] Restart: original device index %s is gone (BT not yet reconnected?)",
                        _original_index,
                    )
            # Try the alternates only if the original index didn't
            if _restart_device is None:
                for _cand in _named_candidates[1:]:
                    # Cache-first lookup (live-query fallback inside
                    _cand_info = recorder._devices._cached_device_info(_cand)
                    if _cand_info is None:
                        continue
                    # Name-match check: confirm the device at this
                    _cand_name = str(_cand_info.get("name", "")).strip().lower()
                    if _expected_name and _cand_name and _cand_name != _expected_name:
                        continue
                    _restart_device = _cand
                    log.info(
                        "[RECORDING] Restart: found same-named device at alternate index %s",
                        _cand,
                    )
                    break
        if _restart_device is None:
            log.info("[RECORDING] Restart: no same-named device found, falling back to OS default")

        # Try to open with the resolved device (configured-by-name or
        try:
            candidate_sr, _ = recorder._devices._resolve_effective_sample_rate(_restart_device)
            default_dev = recorder._devices._cached_device_info(_restart_device)
            if default_dev is not None:
                max_ch = int(default_dev.get("max_input_channels", 1) or 1)
                if max_ch < 1:
                    max_ch = 1
                elif max_ch > 2:
                    max_ch = 2
                channels = max_ch
            else:
                channels = 1

            stream = sd.InputStream(
                samplerate=candidate_sr,
                channels=channels,
                dtype=np.float32,
                device=_restart_device,  # configured-by-name or None
                callback=recorder._current_callback,
                # Rate-scaled ~32 ms blocks (same helper as the primary
                blocksize=scaled_audio_blocksize(candidate_sr),
                # Request the host API's "low" latency hint
                latency="low",
                # AUDIO-HOT: finished_callback detects unexpected stream termination
                finished_callback=recorder._stream_finished_callback,
            )
            # close-on-raise guard: from the successful
            try:
                # flush stale-rate ring-buffer contents BEFORE
                for _payload in recorder._ring_buffer:
                    _arr = _payload[0] if isinstance(_payload, tuple) else _payload
                    if isinstance(_arr, np.ndarray):
                        _arr.fill(0)
                recorder._ring_buffer.clear()
                stream.start()
                # re-check the stop_generation under the stream-lifecycle
                if _captured_generation != recorder._stop_generation:
                    log.debug(
                        "[RECORDING] Disconnect restart aborted, "
                        "stop_generation changed (%d != %d) before stream assignment",
                        _captured_generation,
                        recorder._stop_generation,
                    )
                    with contextlib.suppress(Exception):
                        stream.close()
                    return
                # STATE-OWNERSHIP: the stream slot lives on StreamLifecycle.
                recorder._stream_lifecycle._stream = stream
            except Exception:
                # Nothing below the failed statement owns the stream yet
                with contextlib.suppress(Exception):
                    stream.close()
                raise
            with recorder._audio_pipeline._lock:
                # Capture the pre-restart effective rate BEFORE the
                _prev_effective_sr = recorder._effective_sr
                recorder._effective_sr = candidate_sr
                recorder._silence_timer = 0.0
                recorder._silence_start_time = None
                recorder._silence_warning_count = 0
                # reset ``_buffer_sr`` so the new session's first chunk
                recorder._audio_pipeline._buffer_sr = None
                # The three disconnect-state writes MUST be inside the
                recorder._actual_channels = channels
                recorder._devices._device_disconnected = False
                # reset the retry counter on successful restart so a
                recorder._devices._device_disconnect_retries = 0
                # Flush ``_buffer`` on hot-swap restart (losing
                recorder._session_state.secure_clear_caches(recorder)
                # SEC-audit-008: swap-and-secure-clear-background for
                from voice_typer.server import recording as _recording_pkg
                from voice_typer.server.recording.recorder import (
                    DEFAULT_MAX_BUFFER_CHUNKS,
                )

                _old_buffer = recorder._audio_pipeline._buffer
                recorder._audio_pipeline._buffer = collections.deque(
                    maxlen=getattr(_old_buffer, "maxlen", DEFAULT_MAX_BUFFER_CHUNKS) or DEFAULT_MAX_BUFFER_CHUNKS
                )
                _recording_pkg._secure_clear_array_background(_old_buffer)
                # ``_secure_clear_caches`` resets the resample-path
                recorder._cached_no_resample_segments = []
                recorder._cached_no_resample_concat_dirty = False
                recorder._cached_resample_key = ()
                if candidate_sr != _prev_effective_sr:
                    recorder._session_state.resize_buffers_for_sample_rate(
                        recorder,
                        candidate_sr,
                        coerce_max_recording_time(getattr(recorder, "_cached_max_recording_time", 0)),
                    )
            log.info(
                "[RECORDING] Successfully restarted with %s device at %d Hz",
                "default" if _restart_device is None else f"index {_restart_device}",
                candidate_sr,
            )
            refresh_vad_caches(recorder)

            # Sliding-window flap detection: we just completed a
            _now = time.monotonic()
            recorder._restart_timestamps.append(_now)
            _window = recorder._flapping_window_seconds
            # Prune entries older than the window. ``deque.popleft`` is
            while recorder._restart_timestamps and recorder._restart_timestamps[0] < _now - _window:
                recorder._restart_timestamps.popleft()
            if len(recorder._restart_timestamps) >= recorder._flapping_max_restarts:
                log.error(
                    "[RECORDING] Flapping device detected, %d restarts within "
                    "the %.1fs flap-detection window. Firing on_device_lost so "
                    "the UI surfaces the disconnect (the per-attempt retry "
                    "counter resets on every successful restart, so the "
                    "sliding-window flap detector is the only signal that "
                    "catches this pattern).",
                    len(recorder._restart_timestamps),
                    _window,
                )
                # Clear the deque so the next restart within the window
                recorder._restart_timestamps.clear()
                _device_lost_cb = getattr(recorder, "on_device_lost", None)
                if callable(_device_lost_cb):
                    with contextlib.suppress(Exception):
                        _device_lost_cb()
                else:
                    _silence_stop_cb = getattr(recorder, "on_silence_auto_stop", None)
                    if callable(_silence_stop_cb):
                        with contextlib.suppress(Exception):
                            _silence_stop_cb()
        except (AttributeError, TypeError, KeyError):
            # Programming bugs (missing attribute, wrong type, missing
            raise
        except Exception as e:
            log.exception("[RECORDING] Failed to restart with default device: %s", e)
            recorder._devices._device_disconnected = False


np = lazy_module("numpy")
