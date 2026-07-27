"""VAD property-shim mixin for :class:`Recorder`.

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
"""

from __future__ import annotations

from typing import Any


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
