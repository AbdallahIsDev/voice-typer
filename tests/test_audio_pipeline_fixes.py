"""Focused tests for the audio-pipeline fixes:"""

from __future__ import annotations

import collections
import inspect
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording import audio_pipeline as audio_pipeline_mod
from voice_typer.server.recording.audio_pipeline import AudioPipeline
from voice_typer.server.vad_processor import VadState


class TestScipyImportHoisted:
    """
    ``from voice_typer.server.recording.resampling import
    ``run_vad_state_machine`` must NOT contain any ``from ... import``
    """

    def test_run_vad_state_machine_has_no_per_call_scipy_import(
        self,
    ) -> None:
        src = inspect.getsource(AudioPipeline.run_vad_state_machine)
        assert "from scipy.signal import upfirdn" not in src, (
            "run_vad_state_machine must not contain a per-call 'from scipy.signal import upfirdn', hoist to module top."
        )

    def test_run_vad_state_machine_has_no_per_call_resampling_import(
        self,
    ) -> None:
        src = inspect.getsource(AudioPipeline.run_vad_state_machine)
        assert "from voice_typer.server.recording.resampling import" not in src, (
            "run_vad_state_machine must not contain a per-call "
            "'from voice_typer.server.recording.resampling import ...' "
            "— hoist to module top."
        )

    def test_module_top_aliases_exist(self) -> None:
        """The module-top aliases ``_sp_signal`` and ``_resampling_mod``"""
        assert hasattr(audio_pipeline_mod, "_resampling_mod"), (
            "audio_pipeline module must bind '_resampling_mod' at module top."
        )
        import voice_typer.server.recording.resampling as expected_resampling

        assert audio_pipeline_mod._resampling_mod is expected_resampling, (
            "_resampling_mod must be the resampling submodule (so test "
            "patches of '...resampling._get_resample_fir_taps' take effect)."
        )
        # name must exist.
        assert hasattr(audio_pipeline_mod, "_sp_signal"), (
            "audio_pipeline module must bind '_sp_signal' at module top (may be None if scipy is unavailable)."
        )

    def test_call_site_uses_module_aliases(self) -> None:
        """The VAD resample call site uses ``_sp_signal.upfirdn(...)``"""
        src = inspect.getsource(AudioPipeline.run_vad_state_machine)
        assert "_sp_signal.upfirdn" in src, (
            "run_vad_state_machine must call '_sp_signal.upfirdn(...)' "
            "(module attribute access) so patches of "
            "'scipy.signal.upfirdn' take effect."
        )
        assert "_resampling_mod._get_resample_fir_taps" in src, (
            "run_vad_state_machine must call "
            "'_resampling_mod._get_resample_fir_taps(...)' (module "
            "attribute access) so patches of "
            "'...resampling._get_resample_fir_taps' take effect."
        )


def _make_vad_recorder_stub() -> MagicMock:
    """Build a MagicMock ``Recorder`` with the cached VAD attributes"""
    recorder = MagicMock(name="RecorderStub")
    # Disable Silero VAD branch, skip resample + compute_vad_prob.
    recorder._cached_vad_enabled = False
    recorder._cached_use_silero_vad = False
    recorder._cached_silero_available = False
    # State-machine downstream, return SPEECH to avoid silence-timer
    recorder._silence_start_time = None
    recorder._silence_timer = 0.0
    recorder._silence_warning_count = 0
    # Cached silence / max-duration thresholds, large so callbacks
    recorder._cached_silence_warning = 10_000.0
    recorder._cached_stop_on_silence = 10_000.0
    recorder._cached_max_recording_time = 10_000.0
    # Buffer / chunk-count state read by the telemetry log guard.
    recorder._audio_pipeline._chunk_count = 0
    return recorder


def _make_process_chunk_pipeline_stub() -> AudioPipeline:
    """``process_audio_chunk`` tests."""
    recorder = MagicMock(name="RecorderStub")
    pipeline = AudioPipeline(recorder)
    pipeline.detect_device_disconnect = MagicMock(return_value=False)
    pipeline.handle_xrun_status = MagicMock(return_value=False)
    pipeline.apply_filter_chain = MagicMock(return_value=np.array([0.5, -0.5, 0.5, -0.5], dtype=np.float32))
    pipeline.append_to_buffer_locked = MagicMock(return_value=(1, 1))
    pipeline.compute_rms_and_peak = MagicMock(return_value=(0.5, 0.9, 0.032))
    pipeline.detect_and_emit_clipping = MagicMock()
    pipeline.run_vad_state_machine = MagicMock()
    recorder._audio_pipeline._lock = threading.Lock()
    recorder._recent_rms_values = collections.deque(maxlen=10)
    recorder._last_rms = None
    recorder._rms_callback_error_count = 0
    recorder.on_rms_level = None
    recorder.on_silence_warning = None
    recorder.on_silence_auto_stop = None
    recorder.on_max_duration_auto_stop = None
    recorder._recording_start_time = 100.0
    recorder._effective_sr = 16000
    return pipeline


class TestVadAutoCalibrateReceivesRawRms:
    """``vad_auto_calibrate`` (the module-level function"""

    def test_auto_calibrate_receives_raw_rms_not_filtered(
        self,
        monkeypatch,
    ) -> None:
        """``process_audio_chunk``), ``vad_auto_calibrate`` is called"""
        import voice_typer.server.recording.audio_pipeline as ap_mod

        recorder = _make_vad_recorder_stub()
        pipeline = AudioPipeline(recorder)
        vad_update_mock = MagicMock(return_value=VadState.SPEECH)
        calibrate_mock = MagicMock()
        monkeypatch.setattr(ap_mod, "vad_update", vad_update_mock)
        monkeypatch.setattr(ap_mod, "vad_auto_calibrate", calibrate_mock)

        raw_rms = 0.123
        filtered_rms = 0.456  # deliberately different from raw_rms
        # Simulate process_audio_chunk having set the transient attr.
        pipeline._pending_raw_chunk_rms = raw_rms

        pipeline.run_vad_state_machine(
            np.array([0.1, 0.2, 0.3], dtype=np.float32),
            chunk_rms=filtered_rms,
            chunk_duration=0.032,
            perf_ts=12345.0,
            chunk_count=1,
            buffer_len=1,
            recording_start=0.0,
            silence_warning_cb=None,
            silence_auto_stop_cb=None,
            max_duration_cb=None,
        )

        calibrate_mock.assert_called_once_with(recorder, raw_rms, 0.032)

    def test_auto_calibrate_falls_back_to_chunk_rms_when_no_raw(
        self,
        monkeypatch,
    ) -> None:
        """Direct callers of ``run_vad_state_machine`` that did not"""
        import voice_typer.server.recording.audio_pipeline as ap_mod

        recorder = _make_vad_recorder_stub()
        pipeline = AudioPipeline(recorder)
        # The stub disables the VAD branch (``_cached_vad_enabled=False``),
        calibrate_mock = MagicMock()
        monkeypatch.setattr(ap_mod, "vad_auto_calibrate", calibrate_mock)
        # Do NOT set _pending_raw_chunk_rms, simulate a direct caller.

        filtered_rms = 0.456
        pipeline.run_vad_state_machine(
            np.array([0.1, 0.2, 0.3], dtype=np.float32),
            chunk_rms=filtered_rms,
            chunk_duration=0.032,
            perf_ts=12345.0,
            chunk_count=1,
            buffer_len=1,
            recording_start=0.0,
            silence_warning_cb=None,
            silence_auto_stop_cb=None,
            max_duration_cb=None,
        )

        calibrate_mock.assert_called_once_with(recorder, filtered_rms, 0.032)

    def test_process_audio_chunk_sets_pending_raw_rms_from_indata(
        self,
    ) -> None:
        """``process_audio_chunk`` computes the raw (pre-filter) RMS"""
        pipeline = _make_process_chunk_pipeline_stub()

        indata = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)
        expected_raw_rms = float(np.sqrt(np.mean(indata**2)))

        pipeline.process_audio_chunk(indata, 4, None, 0, 12345.0)

        # The transient attribute was set to the raw RMS of indata.
        assert hasattr(pipeline, "_pending_raw_chunk_rms"), (
            "process_audio_chunk must set '_pending_raw_chunk_rms' before calling _apply_filter_chain."
        )
        assert pipeline._pending_raw_chunk_rms == pytest.approx(expected_raw_rms), (
            f"_pending_raw_chunk_rms must be the raw RMS of indata "
            f"({expected_raw_rms}), got "
            f"{pipeline._pending_raw_chunk_rms}."
        )
        # And it must NOT be the filtered RMS (0.5) returned by
        assert pipeline._pending_raw_chunk_rms != pytest.approx(0.5), (
            "_pending_raw_chunk_rms must be the RAW (pre-filter) RMS, not the post-filter chunk_rms (0.5)."
        )

    def test_process_audio_chunk_sets_raw_rms_before_filter_chain(
        self,
    ) -> None:
        """The raw RMS is computed from ``indata`` BEFORE"""
        pipeline = _make_process_chunk_pipeline_stub()

        # Capture whether _pending_raw_chunk_rms is set at the time
        raw_rms_at_filter_time: list[float | None] = []

        def capture_filter_chain(indata: np.ndarray) -> np.ndarray:
            # Record the value of _pending_raw_chunk_rms on the pipeline
            raw_rms_at_filter_time.append(getattr(pipeline, "_pending_raw_chunk_rms", None))
            return pipeline.apply_filter_chain.return_value

        pipeline.apply_filter_chain.side_effect = capture_filter_chain

        indata = np.array([0.3, -0.3, 0.3, -0.3], dtype=np.float32)
        expected_raw_rms = float(np.sqrt(np.mean(indata**2)))

        pipeline.process_audio_chunk(indata, 4, None, 0, 12345.0)

        pipeline.apply_filter_chain.assert_called_once_with(indata)
        # At the time _apply_filter_chain was called, _pending_raw_chunk_rms
        assert len(raw_rms_at_filter_time) == 1
        assert raw_rms_at_filter_time[0] == pytest.approx(expected_raw_rms), (
            "_pending_raw_chunk_rms must be set BEFORE _apply_filter_chain "
            f"is called (expected {expected_raw_rms}, got "
            f"{raw_rms_at_filter_time[0]})."
        )


class TestAudioQualityControllerLazyNumpy:
    """module can be imported without eager-loading numpy."""

    def test_np_not_in_module_dict_at_import_time(self) -> None:
        """After importing ``audio_quality_controller``, ``np`` must"""
        import voice_typer.server.audio_quality_controller as aqc

        assert "np" not in aqc.__dict__, (
            "'np' must not be in audio_quality_controller.__dict__ at "
            "runtime, move 'import numpy as np' under TYPE_CHECKING."
        )
        assert "numpy" not in aqc.__dict__, "'numpy' must not be in audio_quality_controller.__dict__ at runtime."

    def test_numpy_import_under_type_checking(self) -> None:
        """The module source must contain ``if TYPE_CHECKING:`` with"""
        import voice_typer.server.audio_quality_controller as aqc

        src = inspect.getsource(aqc)
        # The top-level (un-indented) 'import numpy as np' must NOT
        lines = src.splitlines()
        for line in lines:
            stripped = line.lstrip()
            if stripped == "import numpy as np":
                # This line must be indented (inside the TYPE_CHECKING block).
                assert line != stripped, (
                    "'import numpy as np' must be indented under 'if TYPE_CHECKING:', not at module top level."
                )

    def test_module_imports_without_numpy_being_used_at_runtime(
        self,
    ) -> None:
        """The module can be imported and the class symbol accessed"""
        import voice_typer.server.audio_quality_controller as aqc

        # The class is accessible.
        assert hasattr(aqc, "AudioQualityController")
        # The method exists and is callable.
        assert hasattr(aqc.AudioQualityController, "_finalize_audio_quality_report")
        assert "np" not in aqc.__dict__


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])
