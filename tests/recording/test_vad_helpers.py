"""Tests for ``voice_typer.server.recording.vad_helpers``.

``vad_helpers.py`` holds the module-level VAD-method bodies extracted
from the ``Recorder`` god-class (Phase 4.5 split):

- :func:`refresh_vad_caches` — per-chunk VAD cache refresh (called
  by ``start_recording`` and ``Recorder.on_config_changed()``).
- :func:`vad_auto_calibrate` — ambient-noise auto-calibration gating
  on the cached ``_cached_vad_enabled`` scalar.
- :func:`vad_update` — VAD state-machine delegation to
  ``VadProcessor.update_frame``.

The historical ``Recorder`` ``_vad_*`` property shims were removed: the
VAD state is owned by the ``VadProcessor`` instance at
``recorder._vad`` and every consumer reads/writes
``recorder._vad.<attr>`` directly. The owner-surface tests below pin
the ``VadProcessor`` attribute names that the migrated consumers
(``session_state.reset_session_state``, ``audio_pipeline``,
``regressions/test_audio.py``) rely on.
"""

from __future__ import annotations

import math
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from voice_typer.server._audio_constants import (
    SILERO_VAD_SAMPLE_RATES,
    WHISPER_SAMPLE_RATE,
)
from voice_typer.server.recording.vad_helpers import (
    refresh_vad_caches,
    vad_auto_calibrate,
    vad_update,
)
from voice_typer.server.vad_processor import VadProcessor, VadState

# ---------------------------------------------------------------------------
# Mock host — provides the attributes the module-level functions read.
# ---------------------------------------------------------------------------


class _MockRecorder:
    """Minimal host for the VAD method bodies.

    Tests set ``self._vad`` to a MagicMock (or a real ``VadProcessor``
    when state-machine behavior is needed). The module-level functions
    (``refresh_vad_caches`` / ``vad_auto_calibrate`` / ``vad_update``)
    read ``_buffer_sr`` / ``_effective_sr`` / ``_recording_start_time``
    / the ``_cached_vad_*`` caches, all of which default to ``None``.
    """

    def __init__(self, vad=None):
        self._vad = vad if vad is not None else MagicMock()
        # STATE-OWNERSHIP: the buffer sample-rate scalar lives on
        # the owning ``AudioPipeline``; production reads it via
        # ``recorder._audio_pipeline._buffer_sr``. This mock plays BOTH
        # roles, so self-delegate the pipeline attribute.
        self._audio_pipeline = self
        # Attributes read by refresh_vad_caches / vad_auto_calibrate.
        self._buffer_sr: int | None = None
        self._effective_sr: int | None = None
        self._recording_start_time: float = time.perf_counter()
        # Caches populated by refresh_vad_caches.
        self._cached_vad_enabled: bool | None = None
        self._cached_use_silero_vad: bool | None = None
        self._cached_silero_available: bool | None = None
        self._cached_vad_resample_up_down: tuple[int, int] | None = None
        self._cached_vad_resample_sr: int | None = None


def _real_vad() -> VadProcessor:
    """Build a real ``VadProcessor`` against a minimal config stand-in
    (all enhancement filters off → VAD disabled)."""
    config = SimpleNamespace(
        sample_rate=16000,
        microphone=None,
        noise_filter_highpass=False,
        noise_filter_gate=False,
        noise_filter_eq=False,
        noise_filter_compressor=False,
        noise_filter_limiter=False,
        noise_filter_notch=False,
        noise_suppression_method="none",
    )
    return VadProcessor(config)


# ---------------------------------------------------------------------------
# VadProcessor owner surface — the attribute names the migrated
# consumers (session_state / audio_pipeline / tests) rely on
# ---------------------------------------------------------------------------


class TestVadProcessorOwnerSurface:
    """``VadProcessor`` owns the VAD state under the public attribute
    names (``state``, ``consecutive_speech_frames``, ...) that all
    consumers access via ``recorder._vad.<attr>`` after the ``Recorder``
    property shims were removed. Values round-trip verbatim through the
    read/write properties (no coercion)."""

    def test_vad_state_round_trips(self):
        vad = _real_vad()
        vad.state = VadState.SILENCE
        assert vad.state is VadState.SILENCE
        vad.state = VadState.SPEECH
        assert vad.state is VadState.SPEECH

    def test_vad_consecutive_speech_frames_round_trips(self):
        vad = _real_vad()
        vad.consecutive_speech_frames = 5
        assert vad.consecutive_speech_frames == 5

    def test_vad_consecutive_silence_frames_round_trips(self):
        vad = _real_vad()
        vad.consecutive_silence_frames = 7
        assert vad.consecutive_silence_frames == 7

    def test_vad_thresholds_round_trip(self):
        # Values are clamped to a floor by the VadProcessor setters
        # (R18-F14); use values above the floor for the round-trip.
        vad = _real_vad()
        vad.speech_threshold_db = -35.5
        vad.silence_threshold_db = -55.0
        assert vad.speech_threshold_db == -35.5
        assert vad.silence_threshold_db == -55.0

    def test_vad_frame_counters_round_trip(self):
        vad = _real_vad()
        vad.speech_frames = 10
        vad.silence_frames = 20
        vad.hangover_frames = 5
        assert vad.speech_frames == 10
        assert vad.silence_frames == 20
        assert vad.hangover_frames == 5

    def test_silero_vad_flags_round_trip(self):
        vad = _real_vad()
        vad.use_silero_vad = True
        vad.silero_available = True
        vad.speech_threshold = 0.6
        vad.silence_threshold = 0.3
        assert vad.use_silero_vad is True
        assert vad.silero_available is True
        assert vad.speech_threshold == 0.6
        assert vad.silence_threshold == 0.3

    def test_vad_calibration_attrs_round_trip(self):
        vad = _real_vad()
        vad.calibration_duration = 2.0
        vad.calibration_rms_values = [0.1, 0.2, 0.3]
        vad.calibrated = True
        vad.calibration_status = "ok"
        assert vad.calibration_duration == 2.0
        assert vad.calibration_rms_values == [0.1, 0.2, 0.3]
        assert vad.calibrated is True
        assert vad.calibration_status == "ok"

    def test_vad_enabled_cache_attrs_round_trip(self):
        vad = _real_vad()
        vad.vad_enabled_cached = True
        vad.vad_enabled_cache_ts = 12345.678
        assert vad.vad_enabled_cached is True
        assert vad.vad_enabled_cache_ts == 12345.678


class TestVadEnabledProperty:
    """``VadProcessor.vad_enabled`` is the cached read-only property
    (5s TTL safety net + explicit refresh via ``on_config_changed()``)
    that the VAD gate reads via ``recorder._vad.vad_enabled``."""

    def test_vad_enabled_reflects_config(self):
        vad = _real_vad()
        # A config with no filters + "none" suppression method → VAD off.
        assert vad.vad_enabled is False
        vad._config.noise_filter_highpass = True
        vad.on_config_changed()
        assert vad.vad_enabled is True

    def test_vad_enabled_is_read_only(self):
        """``vad_enabled`` has no setter — assigning to it raises
        ``AttributeError`` (the standard property-setter behavior)."""
        vad = _real_vad()
        try:
            vad.vad_enabled = True
            raise AssertionError("vad_enabled must be read-only")
        except AttributeError:
            pass


class TestVadOwnerEdgeCases:
    """Edge cases: unicode values, large integers, None coalescing.
    Pins that the owner properties pass values through verbatim
    without coercion."""

    def test_unicode_value_round_trips(self):
        """A unicode string stored on ``calibration_status``
        (e.g. a localized status message with CJK characters) must
        survive the round-trip unchanged."""
        vad = _real_vad()
        unicode_status = "校准完成 ✓"
        vad.calibration_status = unicode_status
        assert vad.calibration_status == unicode_status

    def test_none_value_round_trips(self):
        """``None`` stored on ``calibration_rms_values`` must survive —
        this mirrors the ``_buffer_sr=None`` edge case in
        ``refresh_vad_caches`` (see below)."""
        vad = _real_vad()
        vad.calibration_rms_values = None
        assert vad.calibration_rms_values is None

    def test_large_int_round_trips(self):
        """Large integer values (e.g. frame counters at long recording
        durations) must survive the round-trip without truncation."""
        vad = _real_vad()
        large_count = 2**31 - 1  # INT32_MAX
        vad.consecutive_speech_frames = large_count
        assert vad.consecutive_speech_frames == large_count


# ---------------------------------------------------------------------------
# refresh_vad_caches — sample-rate resample-ratio computation
# ---------------------------------------------------------------------------


class TestRefreshVadCaches:
    """``refresh_vad_caches`` computes the per-chunk VAD caches. The
    interesting branch is the resample-ratio computation: when
    ``_buffer_sr`` (or ``_effective_sr`` fallback) is NOT in
    ``SILERO_VAD_SAMPLE_RATES`` ({8000, 16000}), the cache stores the
    (up, down) integer ratio so the per-chunk VAD path avoids
    recomputing ``math.gcd``. When the rate IS 8000 or 16000, no
    resample is needed and the cache is set to ``None``."""

    def test_buffer_sr_16000_skips_resample(self):
        """WHISPER_SAMPLE_RATE (16000) is in SILERO_VAD_SAMPLE_RATES —
        no resample needed, cache is None."""
        rec = _MockRecorder()
        rec._vad.vad_enabled = True
        rec._vad.use_silero_vad = True
        rec._vad.silero_available = True
        rec._audio_pipeline._buffer_sr = WHISPER_SAMPLE_RATE  # 16000
        rec._effective_sr = 48000

        refresh_vad_caches(rec)

        assert rec._cached_vad_enabled is True
        assert rec._cached_use_silero_vad is True
        assert rec._cached_silero_available is True
        assert rec._cached_vad_resample_up_down is None
        assert rec._cached_vad_resample_sr == WHISPER_SAMPLE_RATE

    def test_buffer_sr_8000_skips_resample(self):
        """8000 is in SILERO_VAD_SAMPLE_RATES — no resample needed."""
        rec = _MockRecorder()
        rec._vad.vad_enabled = False
        rec._vad.use_silero_vad = False
        rec._vad.silero_available = False
        rec._audio_pipeline._buffer_sr = 8000
        rec._effective_sr = None

        refresh_vad_caches(rec)

        assert rec._cached_vad_enabled is False
        assert rec._cached_use_silero_vad is False
        assert rec._cached_silero_available is False
        assert rec._cached_vad_resample_up_down is None
        assert rec._cached_vad_resample_sr == 8000

    def test_buffer_sr_48000_computes_resample_ratio(self):
        """48000 (a common native device rate) is NOT in
        SILERO_VAD_SAMPLE_RATES — the cache stores the (up, down)
        integer ratio derived from ``math.gcd(48000, 16000)``."""
        rec = _MockRecorder()
        rec._vad.vad_enabled = True
        rec._vad.use_silero_vad = True
        rec._vad.silero_available = True
        rec._audio_pipeline._buffer_sr = 48000
        rec._effective_sr = 48000

        refresh_vad_caches(rec)

        gcd = math.gcd(48000, WHISPER_SAMPLE_RATE)  # gcd(48000, 16000) = 16000
        expected_up = WHISPER_SAMPLE_RATE // gcd  # 1
        expected_down = 48000 // gcd  # 3
        assert rec._cached_vad_resample_up_down == (expected_up, expected_down)
        assert rec._cached_vad_resample_sr == 48000

    def test_buffer_sr_none_falls_back_to_effective_sr(self):
        """When ``_buffer_sr`` is None (before the first chunk arrives),
        the cache key falls back to ``_effective_sr``."""
        rec = _MockRecorder()
        rec._vad.vad_enabled = True
        rec._vad.use_silero_vad = False
        rec._vad.silero_available = False
        rec._audio_pipeline._buffer_sr = None
        rec._effective_sr = 16000

        refresh_vad_caches(rec)

        # _effective_sr=16000 is in SILERO_VAD_SAMPLE_RATES — no resample.
        assert rec._cached_vad_resample_up_down is None
        assert rec._cached_vad_resample_sr == 16000

    def test_both_sample_rates_none(self):
        """When both ``_buffer_sr`` and ``_effective_sr`` are None,
        ``vad_sr`` is None — no resample cache is set (the
        ``vad_sr is not None`` guard short-circuits)."""
        rec = _MockRecorder()
        rec._vad.vad_enabled = False
        rec._vad.use_silero_vad = False
        rec._vad.silero_available = False
        rec._audio_pipeline._buffer_sr = None
        rec._effective_sr = None

        refresh_vad_caches(rec)

        assert rec._cached_vad_resample_up_down is None
        assert rec._cached_vad_resample_sr is None
        # The other caches are still populated.
        assert rec._cached_vad_enabled is False
        assert rec._cached_use_silero_vad is False

    def test_buffer_sr_44100_computes_resample_ratio(self):
        """44100 (CD audio rate) is NOT in SILERO_VAD_SAMPLE_RATES —
        the cache stores the (up, down) ratio with gcd=100."""
        rec = _MockRecorder()
        rec._vad.vad_enabled = True
        rec._vad.use_silero_vad = True
        rec._vad.silero_available = True
        rec._audio_pipeline._buffer_sr = 44100
        rec._effective_sr = 44100

        refresh_vad_caches(rec)

        gcd = math.gcd(44100, WHISPER_SAMPLE_RATE)  # gcd(44100, 16000) = 100
        expected = (WHISPER_SAMPLE_RATE // gcd, 44100 // gcd)  # (160, 441)
        assert rec._cached_vad_resample_up_down == expected


# ---------------------------------------------------------------------------
# vad_auto_calibrate — gating on _vad_enabled
# ---------------------------------------------------------------------------


class TestVadAutoCalibrate:
    """``vad_auto_calibrate`` short-circuits when the cached
    ``_cached_vad_enabled`` scalar is False (the VAD-GATE: don't even
    call ``time.perf_counter()`` on every chunk in raw mode). When
    enabled, it delegates to ``self._vad.auto_calibrate(chunk_rms,
    elapsed, chunk_duration)`` where ``elapsed`` is computed from
    ``self._recording_start_time``."""

    def test_disabled_vad_skips_auto_calibrate(self):
        """When ``_cached_vad_enabled`` is False, ``vad_auto_calibrate``
        must NOT call ``self._vad.auto_calibrate`` (the short-circuit)."""
        rec = _MockRecorder()
        rec._cached_vad_enabled = False
        rec._recording_start_time = time.perf_counter()

        vad_auto_calibrate(rec, chunk_rms=0.05, chunk_duration=0.06)

        rec._vad.auto_calibrate.assert_not_called()

    def test_enabled_vad_calls_auto_calibrate_with_elapsed(self):
        """When ``_cached_vad_enabled`` is True, ``vad_auto_calibrate``
        delegates to ``self._vad.auto_calibrate(chunk_rms, elapsed,
        chunk_duration)``. ``elapsed`` is computed from
        ``self._recording_start_time``."""
        rec = _MockRecorder()
        rec._cached_vad_enabled = True
        # Set the recording start time 1.5s in the past.
        rec._recording_start_time = time.perf_counter() - 1.5

        vad_auto_calibrate(rec, chunk_rms=0.05, chunk_duration=0.06)

        rec._vad.auto_calibrate.assert_called_once()
        call_args = rec._vad.auto_calibrate.call_args
        # call_args[0] is the positional args tuple.
        assert call_args[0][0] == 0.05  # chunk_rms
        assert call_args[0][2] == 0.06  # chunk_duration
        # elapsed should be ~1.5 (allow some tolerance for test timing).
        elapsed = call_args[0][1]
        assert 1.4 <= elapsed <= 2.0, f"elapsed={elapsed} not near 1.5"

    def test_zero_chunk_rms_does_not_short_circuit(self):
        """An empty/zero chunk_rms (e.g. silent audio frame) must NOT
        short-circuit when VAD is enabled — it's passed through to
        ``self._vad.auto_calibrate`` so the calibration can update its
        noise-floor estimate."""
        rec = _MockRecorder()
        rec._cached_vad_enabled = True
        rec._recording_start_time = time.perf_counter()

        vad_auto_calibrate(rec, chunk_rms=0.0, chunk_duration=0.06)

        rec._vad.auto_calibrate.assert_called_once()
        assert rec._vad.auto_calibrate.call_args[0][0] == 0.0


# ---------------------------------------------------------------------------
# vad_update — state-machine delegation
# ---------------------------------------------------------------------------


class TestVadUpdate:
    """``vad_update`` delegates to ``self._vad.update_frame(chunk_rms_db,
    vad_prob)`` and returns the resulting ``VadState``. The VadProcessor
    owns the hysteresis transitions; this function is a thin pass-through."""

    def test_vad_update_delegates_and_returns_state(self):
        rec = _MockRecorder()
        expected_state = VadState.SPEECH
        rec._vad.update_frame.return_value = expected_state

        result = vad_update(rec, chunk_rms_db=-30.0, vad_prob=0.85)

        rec._vad.update_frame.assert_called_once_with(-30.0, 0.85)
        assert result is expected_state

    def test_vad_update_with_none_vad_prob(self):
        """When Silero VAD is disabled or no probability is available,
        ``vad_prob`` is None — the function still delegates (the
        VadProcessor falls back to RMS-based detection)."""
        rec = _MockRecorder()
        rec._vad.update_frame.return_value = VadState.SILENCE

        result = vad_update(rec, chunk_rms_db=-60.0, vad_prob=None)

        rec._vad.update_frame.assert_called_once_with(-60.0, None)
        assert result is VadState.SILENCE

    def test_vad_update_returns_unknown_state(self):
        """VAD-GATE: when VAD is disabled, the VadProcessor's
        ``update_frame`` returns ``VadState.UNKNOWN`` immediately. The
        caller's silence-timer logic sees UNKNOWN and treats it as
        "not silence" (no silence warnings, no VAD-based auto-stop)."""
        rec = _MockRecorder()
        rec._vad.update_frame.return_value = VadState.UNKNOWN

        result = vad_update(rec, chunk_rms_db=-50.0)

        assert result is VadState.UNKNOWN


# ---------------------------------------------------------------------------
# SILERO_VAD_SAMPLE_RATES sanity check (pins the constant the
# refresh_vad_caches branch depends on — a silent change to the set
# would break the resample-ratio logic).
# ---------------------------------------------------------------------------


def test_silero_vad_sample_rates_constant():
    """Pin the SILERO_VAD_SAMPLE_RATES set: {8000, 16000}. The
    refresh_vad_caches resample-ratio branch keys off this set, so a
    silent change (e.g. adding 32000) would alter the cache behavior
    for existing rates."""
    assert frozenset({8000, 16000}) == SILERO_VAD_SAMPLE_RATES
    assert WHISPER_SAMPLE_RATE == 16000
    # WHISPER_SAMPLE_RATE must be in SILERO_VAD_SAMPLE_RATES so the
    # default path (buffer_sr == 16000) skips resampling.
    assert WHISPER_SAMPLE_RATE in SILERO_VAD_SAMPLE_RATES
