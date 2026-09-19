"""Tests for ``voice_typer.server.recording.vad_helpers``."""

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


class _MockRecorder:
    """Minimal host for the VAD method bodies."""

    def __init__(self, vad=None):
        self._vad = vad if vad is not None else MagicMock()
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
    """Build a real ``VadProcessor`` against a minimal config stand-in"""
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


class TestVadProcessorOwnerSurface:
    """``VadProcessor`` owns the VAD state under the public attribute"""

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
    """``VadProcessor.vad_enabled`` is the cached read-only property"""

    def test_vad_enabled_reflects_config(self):
        vad = _real_vad()
        # A config with no filters + "none" suppression method → VAD off.
        assert vad.vad_enabled is False
        vad._config.noise_filter_highpass = True
        vad.on_config_changed()
        assert vad.vad_enabled is True

    def test_vad_enabled_is_read_only(self):
        """``vad_enabled`` has no setter, assigning to it raises"""
        vad = _real_vad()
        try:
            vad.vad_enabled = True
            raise AssertionError("vad_enabled must be read-only")
        except AttributeError:
            pass


class TestVadOwnerEdgeCases:
    """
    Edge cases: unicode values, large integers, None coalescing.
    Pins that the owner properties pass values through verbatim
    """

    def test_unicode_value_round_trips(self):
        """A unicode string stored on ``calibration_status``"""
        vad = _real_vad()
        unicode_status = "校准完成 ✓"
        vad.calibration_status = unicode_status
        assert vad.calibration_status == unicode_status

    def test_none_value_round_trips(self):
        """``None`` stored on ``calibration_rms_values`` must survive —"""
        vad = _real_vad()
        vad.calibration_rms_values = None
        assert vad.calibration_rms_values is None

    def test_large_int_round_trips(self):
        """Large integer values (e.g. frame counters at long recording"""
        vad = _real_vad()
        large_count = 2**31 - 1  # INT32_MAX
        vad.consecutive_speech_frames = large_count
        assert vad.consecutive_speech_frames == large_count


class TestRefreshVadCaches:
    """interesting branch is the resample-ratio computation: when"""

    def test_buffer_sr_16000_skips_resample(self):
        """WHISPER_SAMPLE_RATE (16000) is in SILERO_VAD_SAMPLE_RATES —"""
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
        """8000 is in SILERO_VAD_SAMPLE_RATES, no resample needed."""
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
        """SILERO_VAD_SAMPLE_RATES, the cache stores the (up, down)"""
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
        """When ``_buffer_sr`` is None (before the first chunk arrives),"""
        rec = _MockRecorder()
        rec._vad.vad_enabled = True
        rec._vad.use_silero_vad = False
        rec._vad.silero_available = False
        rec._audio_pipeline._buffer_sr = None
        rec._effective_sr = 16000

        refresh_vad_caches(rec)

        # _effective_sr=16000 is in SILERO_VAD_SAMPLE_RATES, no resample.
        assert rec._cached_vad_resample_up_down is None
        assert rec._cached_vad_resample_sr == 16000

    def test_both_sample_rates_none(self):
        """When both ``_buffer_sr`` and ``_effective_sr`` are None,"""
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
        """44100 (CD audio rate) is NOT in SILERO_VAD_SAMPLE_RATES —"""
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


class TestVadAutoCalibrate:
    """``vad_auto_calibrate`` short-circuits when the cached"""

    def test_disabled_vad_skips_auto_calibrate(self):
        """When ``_cached_vad_enabled`` is False, ``vad_auto_calibrate``"""
        rec = _MockRecorder()
        rec._cached_vad_enabled = False
        rec._recording_start_time = time.perf_counter()

        vad_auto_calibrate(rec, chunk_rms=0.05, chunk_duration=0.06)

        rec._vad.auto_calibrate.assert_not_called()

    def test_enabled_vad_calls_auto_calibrate_with_elapsed(self):
        """When ``_cached_vad_enabled`` is True, ``vad_auto_calibrate``"""
        rec = _MockRecorder()
        rec._cached_vad_enabled = True
        # Set the recording start time 1.5s in the past.
        rec._recording_start_time = time.perf_counter() - 1.5

        vad_auto_calibrate(rec, chunk_rms=0.05, chunk_duration=0.06)

        rec._vad.auto_calibrate.assert_called_once()
        call_args = rec._vad.auto_calibrate.call_args
        assert call_args[0][0] == 0.05  # chunk_rms
        assert call_args[0][2] == 0.06  # chunk_duration
        elapsed = call_args[0][1]
        assert 1.4 <= elapsed <= 2.0, f"elapsed={elapsed} not near 1.5"

    def test_zero_chunk_rms_does_not_short_circuit(self):
        """An empty/zero chunk_rms (e.g. silent audio frame) must NOT"""
        rec = _MockRecorder()
        rec._cached_vad_enabled = True
        rec._recording_start_time = time.perf_counter()

        vad_auto_calibrate(rec, chunk_rms=0.0, chunk_duration=0.06)

        rec._vad.auto_calibrate.assert_called_once()
        assert rec._vad.auto_calibrate.call_args[0][0] == 0.0


class TestVadUpdate:
    """``vad_update`` delegates to ``self._vad.update_frame(chunk_rms_db,"""

    def test_vad_update_delegates_and_returns_state(self):
        rec = _MockRecorder()
        expected_state = VadState.SPEECH
        rec._vad.update_frame.return_value = expected_state

        result = vad_update(rec, chunk_rms_db=-30.0, vad_prob=0.85)

        rec._vad.update_frame.assert_called_once_with(-30.0, 0.85)
        assert result is expected_state

    def test_vad_update_with_none_vad_prob(self):
        """When Silero VAD is disabled or no probability is available,"""
        rec = _MockRecorder()
        rec._vad.update_frame.return_value = VadState.SILENCE

        result = vad_update(rec, chunk_rms_db=-60.0, vad_prob=None)

        rec._vad.update_frame.assert_called_once_with(-60.0, None)
        assert result is VadState.SILENCE

    def test_vad_update_returns_unknown_state(self):
        """VAD-GATE: when VAD is disabled, the VadProcessor's"""
        rec = _MockRecorder()
        rec._vad.update_frame.return_value = VadState.UNKNOWN

        result = vad_update(rec, chunk_rms_db=-50.0)

        assert result is VadState.UNKNOWN


def test_silero_vad_sample_rates_constant():
    """silent change (e.g. adding 32000) would alter the cache behavior"""
    assert frozenset({8000, 16000}) == SILERO_VAD_SAMPLE_RATES
    assert WHISPER_SAMPLE_RATE == 16000
    assert WHISPER_SAMPLE_RATE in SILERO_VAD_SAMPLE_RATES


def test_vad_update_docstring_matches_real_grey_zone_behavior():
    """``vad_update``'s docstring must describe the ACTUAL grey-zone"""
    import inspect

    from voice_typer.server.recording import vad_helpers

    doc = inspect.getdoc(vad_helpers.vad_update) or ""

    # The stale claim must be gone...
    assert "no counter resets" not in doc, (
        "the vad_update docstring still claims the grey zone has 'no "
        "counter resets', the real update_frame resets/seeds counters at "
        "the grey-zone hold limit (see TestGreyZoneDecay)"
    )
    # ...and the doc must describe the hold-limit behavior.
    assert "hold limit" in doc, (
        "the vad_update docstring must describe the grey-zone hold limit, "
        "the real update_frame force-transitions at the limit"
    )
