"""VAD-method bodies for the ``Recorder`` VAD pipeline.

The historical ``Recorder`` class exposed ~18 ``self._vad_*`` attribute
names that were simple read/write property delegators to the underlying
``self._vad`` (:class:`voice_typer.server.vad_processor.VadProcessor`)
instance. Those shims were REMOVED (test-backward-compat cleanup) — the
VAD state is owned by the ``VadProcessor`` and every consumer (production
collaborators and tests) accesses it through ``recorder._vad.<attr>``
directly.

VAD-method bodies (Phase 4.5 further-split)
-------------------------------------------
The bodies of ``Recorder._refresh_vad_caches`` /
``Recorder._vad_auto_calibrate`` / ``Recorder._vad_update`` live here as
module-level functions (:func:`refresh_vad_caches`,
:func:`vad_auto_calibrate`, :func:`vad_update`). Production call sites
(``_recorder_split.start_recording``, ``audio_pipeline``) invoke these
functions directly.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import SILERO_VAD_SAMPLE_RATES, WHISPER_SAMPLE_RATE

if TYPE_CHECKING:
    from voice_typer.server.vad_processor import VadState


# VAD method bodies (extracted from ``Recorder``) ──────────────────────
#
# These module-level functions take a ``recorder`` argument (the
# ``Recorder`` instance) and read/write its ``_vad`` / ``_cached_vad_*``
# / ``_buffer_sr`` / ``_effective_sr`` / ``_recording_start_time``
# attributes directly.


def refresh_vad_caches(recorder: Any) -> None:
    """Refresh per-chunk VAD caches on ``recorder``.

    Called by ``Recorder.start()`` and ``Recorder.on_config_changed()``
    so the audio worker hot path (16 Hz) reads cached scalars instead of
    dispatching 3 property lookups per chunk × 16 Hz = 48 lookups/sec
    for values that only change on config edits.

    Also computes the (up, down) integer ratio for the VAD resample
    path. The ratio is derived from ``_buffer_sr`` (the
    post-process_chunk rate set by ``_process_audio_chunk``). When
    ``_buffer_sr`` is 8000 or 16000, no resample is needed and the
    cache is set to ``None``. When ``_buffer_sr`` is something else
    (e.g. 48000 — happens when no AudioProcessor is attached and the
    device's native rate is non-16 kHz), the cache stores the (up,
    down) integers so the per-chunk VAD path avoids recomputing
    ``math.gcd``.

    ``_buffer_sr`` may be ``None`` at ``start()`` time (before the
    first chunk arrives); we fall back to ``_effective_sr`` for the
    cache key. If the actual ``_buffer_sr`` set by the first
    ``_process_audio_chunk`` differs, the cache is refreshed lazily
    inside ``_process_audio_chunk``.
    """
    recorder._cached_vad_enabled = recorder._vad.vad_enabled
    recorder._cached_use_silero_vad = recorder._vad.use_silero_vad
    recorder._cached_silero_available = recorder._vad.silero_available
    # the VAD branch decision must use ``_buffer_sr`` (the
    # post-process_chunk rate) instead of ``_effective_sr`` (the
    # device's native rate). When a processor is active,
    # ``_buffer_sr == 16000`` and the VAD branch is skipped entirely
    # — no double-resample.
    vad_sr = recorder._buffer_sr if recorder._buffer_sr is not None else recorder._effective_sr
    if vad_sr is not None and vad_sr not in SILERO_VAD_SAMPLE_RATES and vad_sr > 0:
        gcd = math.gcd(int(vad_sr), WHISPER_SAMPLE_RATE)
        recorder._cached_vad_resample_up_down = (
            WHISPER_SAMPLE_RATE // gcd,
            int(vad_sr) // gcd,
        )
    else:
        recorder._cached_vad_resample_up_down = None
    recorder._cached_vad_resample_sr = vad_sr


def vad_auto_calibrate(recorder: Any, chunk_rms: float, chunk_duration: float) -> None:
    """Auto-calibrate VAD thresholds based on ambient noise floor.

    Delegates to ``recorder._vad.auto_calibrate(chunk_rms, elapsed,
    chunk_duration)``. The ``elapsed`` argument is computed here from
    ``recorder._recording_start_time`` (which is a Recorder-owned
    attribute, not a VadProcessor one) so VadProcessor stays
    clock-agnostic and unit-testable.

    During the first ``_vad_calibration_duration`` seconds of
    recording, we collect RMS values to determine the ambient noise
    floor. Then we set speech/silence thresholds relative to it.

    VAD-GATE (Task 4): VadProcessor.auto_calibrate also gates on
    vad_enabled, but we short-circuit here too so we don't even call
    time.perf_counter() on every chunk in raw mode.

    PERF: read ``recorder._cached_vad_enabled`` (the cached scalar set
    by ``refresh_vad_caches`` at ``Recorder.start()`` /
    ``on_config_changed()``) instead of the dynamic ``_vad_enabled``
    property (which does a 5 s TTL cache lookup involving
    ``time.perf_counter()``). The cached scalar is always initialized
    to ``False`` in ``Recorder.__init__`` and refreshed before the
    first chunk arrives — see the ``refresh_vad_caches`` docstring
    above. Matches the pattern already used at
    ``audio_pipeline.py:475`` on the same per-chunk hot path.

    Cached-scalar gate: the gate previously read BOTH
    ``_cached_vad_enabled`` AND ``_vad_enabled`` via ``and not ...``,
    which DEFEATED the cached-scalar optimization. When the cache was
    ``False`` (the default cold-start value) and the dynamic property
    returned ``True`` (the post-config-change real value), the gate
    fell through to the dynamic lookup on EVERY chunk — re-introducing
    the ``time.perf_counter()`` cost the cache was meant to eliminate,
    and breaking the contract that the cached scalar is the sole
    arbiter of the VAD gate on the 16 Hz hot path. Fix: gate on the
    cached scalar ONLY. ``refresh_vad_caches`` always sets the scalar
    before chunks arrive (called by ``Recorder.start()`` /
    ``on_config_changed()``), so the dynamic ``_vad_enabled`` property
    is no longer needed as a fallback on the per-chunk path. The
    property remains the source of truth for ``refresh_vad_caches``'s
    own refresh.
    """
    if not recorder._cached_vad_enabled:
        return
    elapsed = time.perf_counter() - recorder._recording_start_time
    recorder._vad.auto_calibrate(chunk_rms, elapsed, chunk_duration)


def vad_update(
    recorder: Any,
    chunk_rms_db: float,
    vad_prob: float | None = None,
) -> VadState:
    """Update the VAD state machine based on the current frame's VAD signal.

    Delegates to ``recorder._vad.update_frame(chunk_rms_db, vad_prob)``.
    The VadProcessor owns the state-machine counters, thresholds, and
    hysteresis transitions; tests and consumers access that state via
    ``recorder._vad.<attr>`` (e.g. ``recorder._vad.consecutive_speech_frames``
    — the historical ``Recorder`` property shims were removed).

    Uses hysteresis — transitioning from SILENCE to SPEECH requires N
    consecutive loud frames, while SPEECH to SILENCE requires M
    consecutive quiet frames (hangover period). This prevents rapid
    toggling at the boundary.

    When Silero VAD is enabled and a probability is provided, uses the
    VAD probability for speech/silence determination instead of RMS dB.
    Falls back to RMS-based detection if vad_prob is None.

    VAD-GATE (Task 4): returns ``VadState.UNKNOWN`` immediately when
    VAD is disabled (all audio enhancements off). The caller's
    silence-timer logic sees UNKNOWN and treats it as "not silence"
    (no silence warnings, no VAD-based auto-stop).

    Grey zone (between speech and silence thresholds). Standard VAD
    hysteresis: leave counters unchanged so a long run of grey-zone
    chunks doesn't discard accumulated frame history. Implemented in
    ``VadProcessor.update_frame`` as a ``pass`` branch — no counter
    resets. State transitions with hysteresis are also implemented
    there.
    """
    # State transitions: delegated to VadProcessor.update_frame.
    return recorder._vad.update_frame(chunk_rms_db, vad_prob)
