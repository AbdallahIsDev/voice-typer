"""``AudioPipeline.run_vad_state_machine``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server.recording.audio_pipeline import AudioPipeline
from voice_typer.server.vad_processor import VadState

# 48000 -> 16000: gcd(48000, 16000) = 16000, so up=1, down=3.
_BUFFER_SR = 48000
_UP = 1
_DOWN = 3
_KNOWN_TAPS = (
    np.array([0.0, 0.1, 0.2, 0.3, 0.2, 0.1, 0.0], dtype=np.float64),
    2,
    1,
)
_INPUT_LEN = 4800
# Expected output length: input_len * up // down.
_EXPECTED_OUTPUT_LEN = _INPUT_LEN * _UP // _DOWN
# The production path slices ``raw[n_pre_remove : n_pre_remove + n_out]``
_MOCKED_RAW_LEN = _EXPECTED_OUTPUT_LEN + _KNOWN_TAPS[1]


def _make_recorder_stub() -> MagicMock:
    """``AudioPipeline.run_vad_state_machine`` reads."""
    recorder = MagicMock(name="RecorderStub")
    # Cached VAD properties, must all be True to enter the Silero
    recorder._cached_vad_enabled = True
    recorder._cached_use_silero_vad = True
    recorder._cached_silero_available = True
    # 48000 Hz buffer, NOT in SILERO_VAD_SAMPLE_RATES, so the resample
    recorder._audio_pipeline._buffer_sr = _BUFFER_SR
    recorder._effective_sr = _BUFFER_SR
    # Cache matches _buffer_sr so ``_refresh_vad_caches`` is skipped.
    recorder._cached_vad_resample_sr = _BUFFER_SR
    recorder._cached_vad_resample_up_down = (_UP, _DOWN)
    # State-machine downstream, return SPEECH to avoid silence-timer
    recorder._vad.update_frame.return_value = VadState.SPEECH
    # Silence-timer state, pre-initialised so the SPEECH branch's
    recorder._silence_start_time = None
    recorder._silence_timer = 0.0
    recorder._silence_warning_count = 0
    # Cached silence / max-duration thresholds, large so callbacks
    recorder._cached_silence_warning = 10_000.0
    recorder._cached_stop_on_silence = 10_000.0
    recorder._cached_max_recording_time = 10_000.0
    return recorder


def _call_run_vad(pipeline: AudioPipeline, filtered: np.ndarray) -> None:
    """Invoke ``run_vad_state_machine`` with the positional args it"""
    pipeline.run_vad_state_machine(
        filtered,
        chunk_rms=0.5,
        chunk_duration=0.1,
        perf_ts=12345.0,
        chunk_count=1,
        buffer_len=1,
        recording_start=0.0,
        silence_warning_cb=None,
        silence_auto_stop_cb=None,
        max_duration_cb=None,
    )


class TestVadResampleUsesCachedFirTaps:
    """SU-12: the VAD resample branch must consult"""

    def test_upfirdn_called_with_cached_taps_and_resample_poly_not_called(
        self,
    ) -> None:
        """When ``_buffer_sr=48000``, the cached-FIR path is taken:"""
        recorder = _make_recorder_stub()
        pipeline = AudioPipeline(recorder)

        filtered = np.arange(_INPUT_LEN, dtype=np.float32) / _INPUT_LEN
        # Mocked upfirdn output: an array of the RAW (untrimmed) output
        mocked_upfirdn_out = np.zeros(_MOCKED_RAW_LEN, dtype=np.float64)

        with (
            patch(
                "voice_typer.server.recording.resampling._get_resample_fir_taps",
                return_value=_KNOWN_TAPS,
            ) as mock_get_taps,
            patch("scipy.signal.upfirdn", return_value=mocked_upfirdn_out) as mock_upfirdn,
            patch("voice_typer.server.recording.resampling._get_resample_poly") as mock_get_poly,
            patch(
                "voice_typer.server.recording.audio_pipeline.compute_vad_prob",
                return_value=0.42,
            ) as mock_vad_prob,
        ):
            _call_run_vad(pipeline, filtered)

        # ``_get_resample_fir_taps`` was called with the cached (up, down).
        mock_get_taps.assert_called_once_with(_UP, _DOWN)
        # ``upfirdn`` was called positionally with the UNPACKED taps
        mock_upfirdn.assert_called_once()
        call_args, call_kwargs = mock_upfirdn.call_args
        assert call_args[0] is _KNOWN_TAPS[0]
        # Second positional arg is filtered.ravel(), same data.
        np.testing.assert_array_equal(call_args[1], filtered.ravel())
        assert call_kwargs == {"up": _UP, "down": _DOWN}
        # ``resample_poly`` was NOT consulted (no fallback).
        mock_get_poly.assert_not_called()
        # ``compute_vad_prob`` was called with the resampled audio.
        mock_vad_prob.assert_called_once()

    def test_output_is_float32_with_expected_length(self) -> None:
        """The resampled ``vad_audio`` passed to ``compute_vad_prob``"""
        recorder = _make_recorder_stub()
        pipeline = AudioPipeline(recorder)

        filtered = np.arange(_INPUT_LEN, dtype=np.float32) / _INPUT_LEN
        mocked_upfirdn_out = np.zeros(_MOCKED_RAW_LEN, dtype=np.float64)

        with (
            patch(
                "voice_typer.server.recording.resampling._get_resample_fir_taps",
                return_value=_KNOWN_TAPS,
            ),
            patch("scipy.signal.upfirdn", return_value=mocked_upfirdn_out),
            patch("voice_typer.server.recording.resampling._get_resample_poly"),
            patch(
                "voice_typer.server.recording.audio_pipeline.compute_vad_prob",
                return_value=0.42,
            ) as mock_vad_prob,
        ):
            _call_run_vad(pipeline, filtered)

        mock_vad_prob.assert_called_once()
        vad_audio_arg, vad_sr_arg = mock_vad_prob.call_args.args
        assert vad_audio_arg.dtype == np.float32, f"vad_audio must be float32, got {vad_audio_arg.dtype}"
        assert len(vad_audio_arg) == _EXPECTED_OUTPUT_LEN, (
            f"vad_audio length must be {_EXPECTED_OUTPUT_LEN}, got {len(vad_audio_arg)}"
        )
        # Sample rate passed to VAD is WHISPER_SAMPLE_RATE (16000).
        assert vad_sr_arg == 16000


class TestVadResampleFallbackUsesResamplePoly:
    """SU-12: when the cached-FIR path raises (e.g. ``upfirdn`` not"""

    def test_get_resample_fir_taps_raises_falls_back_to_resample_poly(
        self,
    ) -> None:
        """If ``_get_resample_fir_taps`` raises, the inner ``except``"""
        recorder = _make_recorder_stub()
        pipeline = AudioPipeline(recorder)

        filtered = np.arange(_INPUT_LEN, dtype=np.float32) / _INPUT_LEN

        # Fake resample_poly, returns an array of expected length.
        fake_resample_poly = MagicMock(return_value=np.zeros(_EXPECTED_OUTPUT_LEN, dtype=np.float64))

        with (
            patch(
                "voice_typer.server.recording.resampling._get_resample_fir_taps",
                side_effect=RuntimeError("simulated cache failure"),
            ) as mock_get_taps,
            patch("scipy.signal.upfirdn") as mock_upfirdn,
            patch(
                "voice_typer.server.recording.resampling._get_resample_poly",
                return_value=fake_resample_poly,
            ) as mock_get_poly,
            patch(
                "voice_typer.server.recording.audio_pipeline.compute_vad_prob",
                return_value=0.42,
            ) as mock_vad_prob,
        ):
            _call_run_vad(pipeline, filtered)

        # ``_get_resample_fir_taps`` was attempted (and raised).
        mock_get_taps.assert_called_once_with(_UP, _DOWN)
        # ``upfirdn`` was NOT called (the import-then-call path raised
        mock_upfirdn.assert_not_called()
        # ``_get_resample_poly`` was called to fetch the fallback
        mock_get_poly.assert_called_once()
        fake_resample_poly.assert_called_once()
        poly_args, poly_kwargs = fake_resample_poly.call_args
        np.testing.assert_array_equal(poly_args[0], filtered.ravel())
        assert poly_args[1] == _UP
        assert poly_args[2] == _DOWN
        mock_vad_prob.assert_called_once()
        vad_audio_arg, _vad_sr_arg = mock_vad_prob.call_args.args
        assert vad_audio_arg.dtype == np.float32
        assert len(vad_audio_arg) == _EXPECTED_OUTPUT_LEN

    def test_upfirdn_raises_falls_back_to_resample_poly(self) -> None:
        """If ``upfirdn`` itself raises (e.g. shape mismatch on an edge"""
        recorder = _make_recorder_stub()
        pipeline = AudioPipeline(recorder)

        filtered = np.arange(_INPUT_LEN, dtype=np.float32) / _INPUT_LEN

        fake_resample_poly = MagicMock(return_value=np.zeros(_EXPECTED_OUTPUT_LEN, dtype=np.float64))

        with (
            patch(
                "voice_typer.server.recording.resampling._get_resample_fir_taps",
                return_value=_KNOWN_TAPS,
            ),
            patch(
                "scipy.signal.upfirdn",
                side_effect=ValueError("simulated upfirdn failure"),
            ) as mock_upfirdn,
            patch(
                "voice_typer.server.recording.resampling._get_resample_poly",
                return_value=fake_resample_poly,
            ) as mock_get_poly,
            patch(
                "voice_typer.server.recording.audio_pipeline.compute_vad_prob",
                return_value=0.42,
            ) as mock_vad_prob,
        ):
            _call_run_vad(pipeline, filtered)

        # ``upfirdn`` was attempted (and raised).
        mock_upfirdn.assert_called_once()
        # ``_get_resample_poly`` was called for the fallback.
        mock_get_poly.assert_called_once()
        fake_resample_poly.assert_called_once()
        # ``compute_vad_prob`` still received a valid float32 array.
        mock_vad_prob.assert_called_once()
        vad_audio_arg, _vad_sr_arg = mock_vad_prob.call_args.args
        assert vad_audio_arg.dtype == np.float32
        assert len(vad_audio_arg) == _EXPECTED_OUTPUT_LEN


class TestVadResampleSkippedAt16kHz:
    """
    ``SILERO_VAD_SAMPLE_RATES`` (e.g. 16000), the resample branch is
    ``resample_poly`` is consulted. This pins the precondition under
    """

    def test_no_resample_when_buffer_sr_is_16000(self) -> None:
        recorder = _make_recorder_stub()
        # 16000 is in SILERO_VAD_SAMPLE_RATES -> _cached_vad_resample_up_down
        recorder._audio_pipeline._buffer_sr = 16000
        recorder._effective_sr = 16000
        recorder._cached_vad_resample_sr = 16000
        recorder._cached_vad_resample_up_down = None
        pipeline = AudioPipeline(recorder)

        filtered = np.arange(512, dtype=np.float32) / 512.0

        with (
            patch("voice_typer.server.recording.resampling._get_resample_fir_taps") as mock_get_taps,
            patch("scipy.signal.upfirdn") as mock_upfirdn,
            patch("voice_typer.server.recording.resampling._get_resample_poly") as mock_get_poly,
            patch(
                "voice_typer.server.recording.audio_pipeline.compute_vad_prob",
                return_value=0.5,
            ) as mock_vad_prob,
        ):
            _call_run_vad(pipeline, filtered)

        mock_get_taps.assert_not_called()
        mock_upfirdn.assert_not_called()
        mock_get_poly.assert_not_called()
        # ``compute_vad_prob`` still called with the un-resampled
        mock_vad_prob.assert_called_once()
        vad_audio_arg, _vad_sr_arg = mock_vad_prob.call_args.args
        assert len(vad_audio_arg) == 512


if __name__ == "__main__":
    pytest.main([__file__, "-q", "--timeout=30"])
