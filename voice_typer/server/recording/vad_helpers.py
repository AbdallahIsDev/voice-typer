"""VAD property-shim mixin + VAD-method bodies for :class:`Recorder`.

The ``Recorder`` class historically exposed ~18 ``self._vad_*`` attribute
names that were simple read/write property delegators to the underlying
``self._vad`` (:class:`voice_typer.server.vad_processor.VadProcessor`)
instance — keeping external callers / tests that do
``rec._vad_state = VadState.UNKNOWN`` /
``rec._vad_consecutive_speech_frames == 1`` working without modification
after the VAD state machine was extracted to ``VadProcessor``.

These property shims are pure delegation: each getter calls
``getattr(self._vad, vad_attr)`` and each setter calls
``setattr(self._vad, vad_attr, value)``. They have no behavior of their
own, so they are ideal candidates for extraction into a mixin class.
``Recorder`` inherits from :class:`VadShimMixin` so the property names
remain available on ``Recorder`` instances unchanged.

The mapping between ``_vad_*`` attribute name and ``VadProcessor``
attribute name lives ONLY in this file (the ``_vad_`` prefix is dropped
on the ``VadProcessor`` side — e.g. ``_vad_state`` ↔ ``state``).

Patch-path compatibility
------------------------
``inspect.getsource(Recorder._vad_state)`` continues to work after the
move: ``Recorder`` inherits ``_vad_state`` from ``VadShimMixin`` and
``inspect.getsource`` follows the MRO to find the source in this file.
Tests that assert on the source string of ``Recorder``'s VAD property
shims may need to update their expected file path; tests that simply
read/write the property values keep working unchanged.

VAD-method bodies (Phase 4.5 further-split)
-------------------------------------------
The bodies of ``Recorder._refresh_vad_caches`` /
``Recorder._vad_auto_calibrate`` / ``Recorder._vad_update`` were moved
here as module-level functions (:func:`refresh_vad_caches`,
:func:`vad_auto_calibrate`, :func:`vad_update`). ``Recorder`` keeps
1-line delegator methods so existing call sites, subclass overrides,
and ``inspect.getsource(Recorder._vad_update)`` regression tests
(notably ``test_grey_zone_does_not_reset_counters`` in
``tests/regressions/audio_test.py``) keep working — the pinned phrases
"Grey zone (between speech and silence thresholds)", "pass", and
"State transitions" remain in the delegator's docstring on
``Recorder._vad_update``.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import SILERO_VAD_SAMPLE_RATES, WHISPER_SAMPLE_RATE

if TYPE_CHECKING:
    from voice_typer.server.vad_processor import VadState


class VadShimMixin:
    """Mixin: ``_vad_*`` property delegators to ``self._vad``.

    ``Recorder`` inherits from this mixin so the historical
    ``self._vad_state`` / ``self._vad_consecutive_speech_frames`` / ...
    attribute names keep working after the VAD state machine was moved
    to :class:`voice_typer.server.vad_processor.VadProcessor`.

    The mixin assumes the host class defines ``self._vad`` (a
    ``VadProcessor`` instance) before any of these properties are
    accessed. ``Recorder.__init__`` constructs ``self._vad`` early, so
    this contract holds for all post-construction access.
    """

    @staticmethod
    def _make_vad_property(vad_attr: str) -> property:
        """Factory: build a read/write property delegating to ``self._vad``."""

        def getter(self: Any) -> Any:
            return getattr(self._vad, vad_attr)

        def setter(self: Any, value: Any) -> None:
            setattr(self._vad, vad_attr, value)

        return property(getter, setter)

    _vad_state = _make_vad_property("state")
    _vad_consecutive_speech_frames = _make_vad_property("consecutive_speech_frames")
    _vad_consecutive_silence_frames = _make_vad_property("consecutive_silence_frames")
    _vad_speech_threshold_db = _make_vad_property("speech_threshold_db")
    _vad_silence_threshold_db = _make_vad_property("silence_threshold_db")
    _vad_speech_frames = _make_vad_property("speech_frames")
    _vad_silence_frames = _make_vad_property("silence_frames")
    _vad_hangover_frames = _make_vad_property("hangover_frames")
    _use_silero_vad = _make_vad_property("use_silero_vad")
    _vad_speech_threshold = _make_vad_property("speech_threshold")
    _vad_silence_threshold = _make_vad_property("silence_threshold")
    _silero_available = _make_vad_property("silero_available")
    _vad_calibration_duration = _make_vad_property("calibration_duration")
    _vad_calibration_rms_values = _make_vad_property("calibration_rms_values")
    _vad_calibrated = _make_vad_property("calibrated")
    _vad_calibration_status = _make_vad_property("calibration_status")
    _vad_enabled_cached = _make_vad_property("vad_enabled_cached")
    _vad_enabled_cache_ts = _make_vad_property("vad_enabled_cache_ts")

    del _make_vad_property  # don't leak the helper into the class namespace

    @property
    def _vad_enabled(self) -> bool:
        """Whether VAD should run based on current audio enhancement state.

        Delegates to ``self._vad.vad_enabled`` (``VadProcessor``'s cached
        property with 5s TTL safety net + explicit refresh via
        ``on_config_changed()``). Behavior is preserved bit-for-bit from
        the pre-refactor inline implementation.

        VAD-GATE (Task 4): ensures that if the user changes the audio
        preset to "Off" while the Recorder exists (or mid-session), the
        VAD gate reflects the current config state.

        PERF-02 (c-review): previously a dynamic @property that
        re-evaluated 6 ``getattr()`` calls on every access (read 3× per
        chunk × 16 Hz = 288 getattr/sec for a value that only changes
        when the user toggles a Settings UI switch). Now returns a
        cached value refreshed by ``on_config_changed()`` (the explicit
        hook) with a 5-second TTL safety net so a missed config-change
        notification cannot permanently wedge the cache.
        """
        return self._vad.vad_enabled


# VAD method bodies (extracted from ``Recorder``) ──────────────────────
#
# These module-level functions take a ``recorder`` argument (the
# ``Recorder`` instance) and read/write its ``_vad`` / ``_cached_vad_*``
# / ``_buffer_sr`` / ``_effective_sr`` / ``_recording_start_time``
# attributes directly. ``Recorder`` keeps thin 1-line delegator methods
# (see ``recorder.py``) so call sites, subclass overrides, and
# ``inspect.getsource(Recorder.X)`` regression tests keep working.


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
    recorder._cached_vad_enabled = recorder._vad_enabled
    recorder._cached_use_silero_vad = recorder._use_silero_vad
    recorder._cached_silero_available = recorder._silero_available
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
    """
    if not recorder._vad_enabled:
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
    hysteresis transitions. The historical ``self._vad_*`` attribute
    names (e.g. ``_vad_consecutive_speech_frames``) remain accessible
    on ``Recorder`` via property shims that read/write through to
    ``recorder._vad``.

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
