"""Device-disconnect recovery for :class:`Recorder` (extracted from ``recorder.py``).

The bulk of the stream-restart logic that previously lived in
``Recorder._handle_device_disconnect`` is moved here. The collaborator
pattern mirrors :class:`.device_manager.DeviceManager`:

- :class:`DisconnectHandler` is constructed by ``Recorder.__init__`` with a
  back-reference to the owning ``Recorder`` (``DisconnectHandler(recorder)``).
- The collaborator accesses *shared* state that lives on ``Recorder`` and is
  NOT moved here: ``self._recorder._stream``, ``self._recorder._stream_lifecycle_lock``,
  ``self._recorder._effective_sr``, ``self._recorder._actual_channels``,
  ``self._recorder._buffer_sr``, ``self._recorder._audio_processor``,
  ``self._recorder.config``, the silence-timer fields, etc.

Source-inspection invariants
----------------------------
Three regression tests in ``tests/test_recorder_worker_lifecycle.py``
(``test_handle_device_disconnect_bouncer_intact``,
``test_handle_device_disconnect_restart_uses_lock``,
``test_handle_device_disconnect_rechecks_bouncer_under_lock``) inspect
``inspect.getsource(Recorder._handle_device_disconnect)`` for the GT-24
bouncer comparisons (``_captured_generation != self._stop_generation``,
``_recording_event.is_set()``) and the
``with self._stream_lifecycle_lock:`` block. Those structural elements
THEREFORE stay on ``Recorder._handle_device_disconnect``; only the
device-resolution + stream-open + state-update block (the heavy ~175 LOC
inside the lock) is moved here as :meth:`DisconnectHandler.restart_stream`.

Patch-path compatibility
------------------------
Tests use ``monkeypatch.setattr(recording.sd, "InputStream", fake)`` and
similar to inject fake sounddevice behavior. The ``sd`` lazy-module proxy
in this module re-resolves ``sys.modules`` on every attribute access (see
``voice_typer/server/_lazy_import.py``), so the patch on the package-level
``recording.sd`` propagates here automatically — no ``_recording_pkg.sd``
indirection needed.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import numpy as np

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

if TYPE_CHECKING:
    from .recorder import Recorder


class DisconnectHandler:
    """Handles audio device hot-swap stream restart for :class:`Recorder`.

    Extracted from the body of ``Recorder._handle_device_disconnect`` (the
    ~175-LOC stream-restart block that runs under
    ``_stream_lifecycle_lock``). The handler resolves a fallback device
    (the user's configured mic by name, else the OS default), opens a
    fresh ``sd.InputStream``, assigns it to ``recorder._stream``, and
    refreshes the post-restart state (effective sample rate, silence
    timer, AudioProcessor tuning, VAD caches).

    The bouncer checks (``_captured_generation != self._stop_generation``,
    ``_recording_event.is_set()``) and the
    ``with self._stream_lifecycle_lock:`` acquisition STAY on
    ``Recorder._handle_device_disconnect`` so the GT-24 source-inspection
    regression tests continue to pin the lock-scope invariant.
    """

    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def restart_stream(self, _captured_generation: int) -> None:
        """Open a fresh ``sd.InputStream`` on a fallback device.

        Runs under ``recorder._stream_lifecycle_lock`` (acquired by the
        caller, ``Recorder._handle_device_disconnect``). The caller has
        already re-checked the bouncer conditions under the lock, so this
        method proceeds directly to device resolution + stream open +
        state update.

        On failure, the exception is logged and
        ``recorder._device_disconnected`` is cleared so the next
        health-checker cycle re-probes (preserves the pre-extraction
        behavior — see the original ``except Exception`` branch).
        """
        recorder = self._recorder
        # Medium: PortAudio device IDs are not stable across hot-swap on
        # Windows MME. Pre-fix, the restart always used ``device=None``
        # (OS default), ignoring the user's configured mic. If the user
        # had explicitly selected a non-default mic (e.g. a USB headset)
        # and it disconnected momentarily (BT reconnection), the recorder
        # silently switched to the laptop built-in mic. Try the user's
        # configured mic (by name) first; only fall back to
        # ``device=None`` if no same-named device is found.
        _restart_device = None
        _configured_device = recorder._resolve_device()
        if _configured_device is not None:
            _named_candidates = recorder._same_physical_microphone_candidates(_configured_device)
            # The first candidate is the original device index; skip
            # it (it just disconnected). Try the alternates.
            for _cand in _named_candidates[1:]:
                try:
                    sd.query_devices(_cand)
                    _restart_device = _cand
                    log.info(
                        "[RECORDING] Restart: found same-named device at index %s",
                        _cand,
                    )
                    break
                except Exception:
                    continue
        if _restart_device is None:
            log.info("[RECORDING] Restart: no same-named device found, falling back to OS default")

        # Try to open with the resolved device (configured-by-name or
        # OS default).
        try:
            candidate_sr, _ = recorder._resolve_effective_sample_rate(_restart_device)
            # AUDIO-CH (revised): The previous code did
            # ``channels = min(1, default_dev.get("max_input_channels", 1))``
            # which ALWAYS returned 1 for any valid device (min(1, N>=1) == 1).
            # This meant a stereo-capable device was always reopened as mono,
            # losing the second channel even when the user wanted stereo.
            #
            # We now use the device's actual max_input_channels, clamped to
            # [1, 2] (we never need more than 2 channels for voice recording,
            # and ASR pipelines expect mono or stereo). If the device reports
            # 0 channels (broken driver), we fall back to 1 (mono).
            # See FORENSIC_REVIEW_COMPLETE.md → AUDIO-HOT.
            try:
                if _restart_device is None:
                    default_dev = sd.query_devices(kind="input")
                else:
                    default_dev = sd.query_devices(_restart_device)
                max_ch = int(default_dev.get("max_input_channels", 1) or 1)
                if max_ch < 1:
                    max_ch = 1
                elif max_ch > 2:
                    max_ch = 2
                channels = max_ch
            except Exception:
                channels = 1

            stream = sd.InputStream(
                samplerate=candidate_sr,
                channels=channels,
                dtype=np.float32,
                device=_restart_device,  # configured-by-name or None
                callback=recorder._current_callback,
                blocksize=512,
                # AUDIO-HOT: finished_callback detects unexpected stream termination
                finished_callback=recorder._stream_finished_callback,
            )
            stream.start()
            # re-check the stop_generation under the stream-lifecycle
            # lock BEFORE assigning ``recorder._stream``. A concurrent
            # ``stop()`` could have bumped the generation between our
            # earlier bouncer check (top of the locked block) and this
            # assignment; assigning ``recorder._stream`` anyway would
            # leak the new stream (stop() already tore down the old one
            # and would not see this new one) and leave a zombie
            # callback running. If the generation changed, close the
            # new stream and bail out.
            if _captured_generation != recorder._stop_generation:
                log.debug(
                    "[RECORDING] Disconnect restart aborted — "
                    "stop_generation changed (%d != %d) before stream assignment",
                    _captured_generation,
                    recorder._stop_generation,
                )
                with contextlib.suppress(Exception):
                    stream.close()
                return
            recorder._stream = stream
            with recorder._lock:
                recorder._effective_sr = candidate_sr
                # reset the silence timer so a hot-swap recovery does
                # not immediately trigger an auto-stop. Previously the
                # silence timer accumulated during the disconnect (no
                # audio was arriving) and was not reset on recovery --
                # the next chunk after recovery would push the timer
                # past ``stop_on_silence_seconds`` and fire
                # ``on_silence_auto_stop`` even though the user was
                # actively speaking into the new device.
                recorder._silence_timer = 0.0
                recorder._silence_start_time = None
                recorder._silence_warning_count = 0
                # reset ``_buffer_sr`` so the new session's first chunk
                # sets it fresh (the prior session's rate may differ
                # from the new device's rate).
                recorder._buffer_sr = None
            recorder._actual_channels = channels
            recorder._device_disconnected = False
            # reset the retry counter on successful restart so a
            # subsequent disconnect (e.g. BT mic flapping) gets a full
            # retry budget instead of inheriting the prior disconnect's
            # count.
            recorder._device_disconnect_retries = 0
            log.info(
                "[RECORDING] Successfully restarted with %s device at %d Hz",
                "default" if _restart_device is None else f"index {_restart_device}",
                candidate_sr,
            )
            # High: retune the AudioProcessor's chain to the new device's
            # native rate so filter coefficients are tuned correctly
            # (XV-31 mitigation) and the per-chunk ``process_chunk`` call
            # avoids the RT-thread resample branch. Mirrors the start()
            # retune logic. Guard with try/except so a buggy
            # AudioProcessor can't break the recovery.
            if recorder._audio_processor is not None:
                _proc_sr = getattr(recorder._audio_processor, "_sample_rate", None)
                if _proc_sr is not None and int(_proc_sr) != int(candidate_sr):
                    _set_sr = getattr(recorder._audio_processor, "set_sample_rate", None)
                    if callable(_set_sr):
                        try:
                            _set_sr(int(candidate_sr))
                            log.info(
                                "[RECORDING] AudioProcessor.set_sample_rate(%d) called on hot-plug restart",
                                candidate_sr,
                            )
                        except Exception:
                            log.warning(
                                "[RECORDING] AudioProcessor.set_sample_rate(%d) failed on restart",
                                candidate_sr,
                                exc_info=True,
                            )
                    else:
                        try:
                            recorder._audio_processor.rebuild_from_config(recorder.config)
                        except Exception:
                            log.warning(
                                "[RECORDING] AudioProcessor.rebuild_from_config failed on restart",
                                exc_info=True,
                            )
            # refresh the VAD cache because ``_effective_sr`` (and
            # possibly the processor's ``_sample_rate``) just changed.
            # The (up, down) resample ratio is recomputed from the new
            # ``_effective_sr`` (used as fallback until the first chunk
            # sets ``_buffer_sr``).
            recorder._refresh_vad_caches()
        except Exception as e:
            # use ``log.exception`` so the full traceback is captured
            # (the previous ``log.error("...: %s", e)`` form lost the
            # traceback — only the exception's str() was logged, making
            # remote debugging of disconnect-restart failures much
            # harder).
            log.exception("[RECORDING] Failed to restart with default device: %s", e)
            # High: clear the disconnect flag so the next health-checker
            # cycle (30s) re-probes. Pre-fix, the except branch left
            # ``_device_disconnected=True`` forever — the health-checker's
            # ``if self._device_disconnected: continue`` skip meant the
            # recorder never auto-recovered even if the user plugged in
            # a new mic. The retry counter is NOT reset here (only on
            # successful restart or max-retries reached) so the retry
            # budget still degrades across consecutive failures.
            recorder._device_disconnected = False
