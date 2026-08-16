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
``inspect.getsource(Recorder._handle_device_disconnect)`` for the
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

import collections
import contextlib
import logging
import time
from typing import TYPE_CHECKING

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


# ``retune_audio_processor`` consolidates the inline retune block
# that was duplicated between ``Recorder.start()`` (in
# ``_recorder_split.py``) and ``DisconnectHandler.restart_stream()``. The
# two copies had drifted (the start-path copy included an info log on the
# rebuild_from_config fallback; the disconnect-path copy omitted it), and
# any fix to the 3-level fallback chain (set_sample_rate →
# rebuild_from_config → log-and-continue) had to be applied in two places.
# The ``context`` parameter substitutes for the divergent log messages
# ("on start" vs "on hot-plug restart").
def retune_audio_processor(
    proc: object,
    effective_sr: int,
    config: object,
    *,
    context: str = "on start",
) -> None:
    """Retune an :class:`AudioProcessor` to a new device's native sample rate.

        Strategy (mirrors ``AudioQualityController._rebuild_audio_processor``):
          1. If ``proc._sample_rate`` already equals ``effective_sr``, no-op.
    2. If ``proc.set_sample_rate`` exists (post-), call it with
             ``effective_sr``. It atomically swaps the chain AND updates
             ``_sample_rate``, so a single call is sufficient.
    3. Else (pre- fallback or a spec-limited test double), call
             ``proc.rebuild_from_config(config)``.
          4. Guard every step with try/except so a buggy AudioProcessor can't
             break the recording-start / hot-plug recovery critical path.
    """
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
                "[RECORDING] AudioProcessor.set_sample_rate(%d) called %s — chain retuned to device native rate",
                effective_sr,
                context,
            )
        except Exception:
            log.warning(
                "[RECORDING] retune_audio_processor failed %s — "
                "set_sample_rate(%d) failed; per-chunk resample will run on the worker thread",
                context,
                effective_sr,
                exc_info=True,
            )
    else:
        try:
            proc.rebuild_from_config(config)  # type: ignore[attr-defined]
            log.info(
                "[RECORDING] AudioProcessor.rebuild_from_config called %s — "
                "chain rebuilt (fallback, set_sample_rate unavailable)",
                context,
            )
        except Exception:
            log.warning(
                "[RECORDING] retune_audio_processor failed %s — "
                "rebuild_from_config failed; filter coefficients may be mistuned",
                context,
                exc_info=True,
            )


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
    ``Recorder._handle_device_disconnect`` so the  source-inspection
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
            # BT headsets that drop and reconnect within the
            # detection→restart-scheduling→restart-execution window
            # (~50-500ms; BT link-manager reconnection is 200-800ms)
            # often reappear at the SAME PortAudio index. The previous
            # ``[1:]`` slice skipped that index unconditionally, so the
            # loop fell through with ``_restart_device = None`` and the
            # recorder silently switched to ``device=None`` (OS default
            # = laptop built-in mic). Try the original index FIRST —
            # if ``sd.query_devices()`` succeeds AND the device name
            # still matches, use it (the device reconnected). Only
            # fall through to ``[1:]`` alternates if the original
            # index is gone or renamed.
            _original_index = _named_candidates[0] if _named_candidates else None
            _expected_name = ""
            if _original_index is not None:
                try:
                    _orig_info = sd.query_devices(_original_index)
                    _expected_name = str(_orig_info.get("name", "")).strip().lower()
                    if _expected_name:
                        # The original index is present and named —
                        # the device likely reconnected. Use it.
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
                except Exception:
                    log.debug(
                        "[RECORDING] Restart: original device index %s is gone (BT not yet reconnected?)",
                        _original_index,
                    )
            # Try the alternates only if the original index didn't
            # reconnect (or had no name).
            if _restart_device is None:
                for _cand in _named_candidates[1:]:
                    try:
                        _cand_info = sd.query_devices(_cand)
                        # Name-match check: confirm the device at this
                        # alternate index is the same physical device
                        # (same name) — guards against PortAudio
                        # renumbering pointing the index at a different
                        # device after hot-swap.
                        _cand_name = str(_cand_info.get("name", "")).strip().lower()
                        if _expected_name and _cand_name and _cand_name != _expected_name:
                            continue
                        _restart_device = _cand
                        log.info(
                            "[RECORDING] Restart: found same-named device at alternate index %s",
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
                blocksize=_AUDIO_BLOCKSIZE,
                # Request the host API's "low" latency hint
                # (mirrors the primary open_stream_for_candidates call in
                # stream_lifecycle.py; PortAudio silently falls back if
                # unavailable).
                latency="low",
                # AUDIO-HOT: finished_callback detects unexpected stream termination
                finished_callback=recorder._stream_finished_callback,
            )
            # flush stale-rate ring-buffer contents BEFORE
            # ``stream.start()``. Pre-fix, the zeroing + clear lived
            # AFTER ``stream.start()`` (between start and clear, the
            # new stream's PortAudio callback fired ~1-3 times at 16
            # Hz × ~60ms window ≈ 1 chunk, pushing fresh NEW-rate
            # audio into ``_ring_buffer`` — which the subsequent
            # ``.clear()`` indiscriminately zeroed along with the
            # intended OLD-rate chunks). Moving the clear BEFORE
            # ``stream.start()`` means only pre-disconnect old-rate
            # chunks are cleared; the new stream's first chunks land
            # in an empty ring buffer and are preserved.
            #
            # SEC-audit-008: zero each chunk's numpy array BEFORE
            # ``.clear()`` so the user's voice data doesn't linger in
            # process memory after the deque reference is dropped (the
            # bare ``.clear()`` only drops references, leaving the
            # underlying float32 arrays intact until GC). Mirrors the
            # preroll-buffer pattern in stop()/discard() (see
            # ``recorder.py``'s ``_preroll_buffer`` clearing). Ring
            # buffer chunks are small (~2KB each, capacity-bounded by
            # ``_AUDIO_RING_BUFFER_CAPACITY``) so synchronous zeroing is
            # acceptable here. Ring buffer items are 5-tuples
            # ``(chunk_copy, frames, time_info, status, perf_ts)`` —
            # the numpy array is the first element. Defensive against
            # direct-array items (legacy/fallback) too.
            #
            # ``collections.deque.clear()`` is atomic under the GIL
            # and the ring buffer is single-producer (audio callback)
            # / single-consumer (worker), so clearing here without
            # the lock is safe — the worker's next ``popleft()``
            # raises ``IndexError`` and the drain loop breaks cleanly.
            for _payload in recorder._ring_buffer:
                _arr = _payload[0] if isinstance(_payload, tuple) else _payload
                if isinstance(_arr, np.ndarray):
                    _arr.fill(0)
            recorder._ring_buffer.clear()
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
                # The three disconnect-state writes MUST be inside the
                # lock. A concurrent ``_device_health_checker_loop``
                # reads/writes ``_device_disconnected`` (and the
                # retry counter) without any other synchronization —
                # writing them outside the lock let a BT-flap race
                # mask a real second disconnect: the checker would set
                # ``_device_disconnected = True`` the instant before
                # the restart set it ``False``, silently clearing the
                # new disconnect. Holding the lock ensures the
                # successful-restart state update is atomic with
                # respect to the health-checker's reads.
                recorder._actual_channels = channels
                recorder._device_disconnected = False
                # reset the retry counter on successful restart so a
                # subsequent disconnect (e.g. BT mic flapping) gets a
                # full retry budget instead of inheriting the prior
                # disconnect's count.
                recorder._device_disconnect_retries = 0
                # Flush ``_buffer`` on hot-swap restart (losing
                # pre-disconnect audio, simplest). Without this,
                # ``stop()`` resamples the entire buffer at the NEW
                # ``_effective_sr``, but pre-disconnect chunks were
                # captured at the OLD rate → pitch/speed artifacts on
                # the pre-disconnect portion (most audible when no
                # AudioProcessor is attached, since the processor's
                # per-chunk resample normally normalizes everything to
                # the chain's construction rate). Securely zero the
                # cached arrays BEFORE reassignment (mirrors
                # ``discard()``'s pattern) so the user's voice data
                # doesn't linger in process memory (SEC-audit-008).
                recorder._secure_clear_caches()
                # SEC-audit-008: swap-and-secure-clear-background for
                # ``_buffer`` — mirrors ``discard()``'s pattern in
                # ``_recorder_split.py:467-475``. The bare
                # ``.clear()`` previously used here drops all chunk
                # references WITHOUT zeroing the underlying numpy
                # arrays, leaving the user's voice data in process
                # memory until GC reclaims them (privacy regression vs.
                # the ``discard()`` path, which correctly defers zeroing
                # to the background buffer-clear worker). Swap in a
                # fresh deque and enqueue the old one onto the buffer-
                # clear worker so the hot-swap restart path returns
                # quickly while the chunks are zeroed off-thread.
                # Lazy imports (mirrors ``_recorder_split.py:387-392``)
                # to avoid a circular import: ``recorder.py`` imports
                # this module at the top of its class body.
                from voice_typer.server import recording as _recording_pkg
                from voice_typer.server.recording.recorder import (
                    DEFAULT_MAX_BUFFER_CHUNKS,
                )

                _old_buffer = recorder._buffer
                recorder._buffer = collections.deque(
                    maxlen=getattr(_old_buffer, "maxlen", DEFAULT_MAX_BUFFER_CHUNKS) or DEFAULT_MAX_BUFFER_CHUNKS
                )
                _recording_pkg._secure_clear_array_background(_old_buffer)
                # ``_secure_clear_caches`` resets the resample-path
                # caches but NOT the no-resample segment list / dirty
                # flag / cache key. Reset them explicitly so the next
                # ``take_snapshot()`` starts from a clean slate (no
                # stale segments carried over from the pre-disconnect
                # session at the old rate).
                recorder._cached_no_resample_segments = []
                recorder._cached_no_resample_concat_dirty = False
                recorder._cached_resample_key = ()
            log.info(
                "[RECORDING] Successfully restarted with %s device at %d Hz",
                "default" if _restart_device is None else f"index {_restart_device}",
                candidate_sr,
            )
            # The ``retune_audio_processor(...)``
            # call that used to live here has been removed. The chain
            # stays at its construction rate (typically
            # WHISPER_SAMPLE_RATE = 16 kHz) and the per-chunk resample
            # in ``AudioProcessor.process_chunk`` (invoked from
            # ``audio_pipeline.process_audio_chunk`` with
            # ``input_sample_rate=recorder._effective_sr``) handles the
            # native-rate → 16 kHz downsample on the worker thread.
            # Filter-chain correctness is preserved (filters built at
            # 16 kHz are fed 16 kHz audio post-resample); only the
            # redundant upfront retune is gone.
            # refresh the VAD cache because ``_effective_sr`` just changed.
            # The (up, down) resample ratio is recomputed from the new
            # ``_effective_sr`` (used as fallback until the first chunk
            # sets ``_buffer_sr``).
            recorder._refresh_vad_caches()

            # Sliding-window flap detection: we just completed a
            # SUCCESSFUL restart. Append the restart timestamp to the
            # sliding-window deque, prune entries older than the window,
            # and if the threshold is met (default: 3 restarts in 60s),
            # fire ``on_device_lost`` and clear the deque. This catches a
            # flapping BT mic that disconnects + reconnects repeatedly
            # within a short window — a real user-facing regression
            # where the per-attempt ``_device_disconnect_retries``
            # counter was reset to 0 on every successful restart (the
            # ``recorder._device_disconnect_retries = 0`` line above),
            # so the per-attempt threshold (3 or 6 retries) was NEVER
            # reached and the user never saw "Microphone disconnected".
            #
            # The sliding window is the right shape: a single
            # disconnect+restart cycle leaves the deque with 1 entry
            # (well below the threshold), so the normal recovery flow
            # is unaffected. Three restarts in 60s — indicative of a
            # real flap (BT link-manager cycle is 5-30s) — triggers the
            # callback so the UI can surface the disconnect.
            #
            # The pruning happens BEFORE the threshold check so old
            # entries (outside the window) don't inflate the count. If
            # the threshold is met, the deque is cleared so a
            # subsequent restart within the window doesn't immediately
            # re-trigger (the user has already been notified; the next
            # ``start()`` from the UI is the explicit "reset" boundary).
            #
            # The deque + threshold constants live on the Recorder
            # (initialized by ``SessionState.__init__`` — see
            # ``session_state.py`` for the rationale on why they live
            # there instead of in ``Recorder.__init__``). ``time`` is
            # imported at module top (alongside ``collections`` /
            # ``contextlib`` / ``logging``), so no deferred import is
            # needed here.
            _now = time.monotonic()
            recorder._restart_timestamps.append(_now)
            _window = recorder._flapping_window_seconds
            # Prune entries older than the window. ``deque.popleft`` is
            # O(1) and atomic under CPython's GIL; the worker thread /
            # audio callback never touch this deque (only this method
            # does, and it runs under ``_stream_lifecycle_lock``), so
            # no lock is needed beyond the one already held.
            while (
                recorder._restart_timestamps
                and recorder._restart_timestamps[0] < _now - _window
            ):
                recorder._restart_timestamps.popleft()
            if (
                len(recorder._restart_timestamps)
                >= recorder._flapping_max_restarts
            ):
                log.error(
                    "[RECORDING] Flapping device detected — %d restarts within "
                    "the %.1fs flap-detection window. Firing on_device_lost so "
                    "the UI surfaces the disconnect (the per-attempt retry "
                    "counter resets on every successful restart, so the "
                    "sliding-window flap detector is the only signal that "
                    "catches this pattern).",
                    len(recorder._restart_timestamps),
                    _window,
                )
                # Clear the deque so the next restart within the window
                # doesn't immediately re-trigger. The user has been
                # notified; the next ``start()`` (from the UI's
                # "reconnect" button) is the explicit reset boundary.
                recorder._restart_timestamps.clear()
                # Mirror the callback-resolution chain in
                # ``Recorder._handle_device_disconnect``: prefer the
                # dedicated ``on_device_lost`` callback (UI shows
                # "Microphone disconnected"); fall back to
                # ``on_silence_auto_stop`` only when ``on_device_lost``
                # is not wired (preserves the pre-fix behavior for
                # callers that haven't been updated).
                _device_lost_cb = getattr(recorder, "on_device_lost", None)
                if callable(_device_lost_cb):
                    with contextlib.suppress(Exception):
                        _device_lost_cb()
                else:
                    _silence_stop_cb = getattr(
                        recorder, "on_silence_auto_stop", None
                    )
                    if callable(_silence_stop_cb):
                        with contextlib.suppress(Exception):
                            _silence_stop_cb()
        except (AttributeError, TypeError, KeyError):
            # Programming bugs (missing attribute, wrong type, missing
            # dict key) must NOT be masked as "transient device
            # failure". Re-raise so the daemon thread's excepthook logs
            # the full traceback and the bug is visible. The previous
            # broad ``except Exception`` swallowed these as silent
            # restart failures, making real bugs look like flaky
            # hardware.
            raise
        except Exception as e:
            # Transient device failures (``sd.PortAudioError``,
            # ``OSError``, and any other non-programming-bug exception):
            # log with the full traceback and clear the disconnect flag
            # so the health-checker re-probes. We catch ``Exception``
            # rather than ``(sd.PortAudioError, OSError)`` because (a)
            # the test conftest replaces ``sounddevice`` in
            # ``sys.modules`` with a ``MagicMock``, making
            # ``sd.PortAudioError`` a non-class object that Python
            # refuses to catch (``TypeError: catching classes that do
            # not inherit from BaseException``); and (b) the
            # programming-bug re-raise clause above already surfaces
            # the bugs the finding wanted surfaced. The remaining
            # ``Exception`` catch covers PortAudio/OS errors plus any
            # other transient failure, preserving the pre-fix recovery
            # behavior.
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


np = lazy_module("numpy")
