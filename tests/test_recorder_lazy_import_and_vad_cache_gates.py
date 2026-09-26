"""Focused regression tests for three Phase 4 cold-start / hot-path fixes."""

from __future__ import annotations

import collections
import inspect
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording.audio_pipeline import AudioPipeline
from voice_typer.server.recording.vad_helpers import vad_auto_calibrate


class TestRecorderLazyImport:
    """``Recorder`` is imported lazily inside"""

    def test_recorder_not_at_module_top(self) -> None:
        """``voice_typer.server.app``, it's imported inside"""
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "Recorder"), (
            "Recorder should NOT be a module-top attribute of "
            "voice_typer.server.app, it should be imported inside "
            "LausuApp.__init__ to defer the recording package "
            "(and its eager numpy import chain) to first construction."
        )

    def test_recorder_import_lives_inside_init(self) -> None:
        """
        The ``from voice_typer.server.recording import Recorder``
        ``self.recorder = Recorder(...)`` assignment must NOT appear
        """
        from voice_typer.server.app import LausuApp

        init_src = inspect.getsource(LausuApp._init_recording)
        # The lazy import statement must appear in the builder's body.
        assert "from voice_typer.server.recording import Recorder" in init_src, (
            "LausuApp._init_recording must contain the lazy import "
            "'from voice_typer.server.recording import Recorder' so the "
            "recording package is not imported at module top."
        )
        assert "self.recorder = Recorder(" not in init_src, (
            "the eager 'self.recorder = Recorder(...)' was removed "
            "from the recording-init builder, the recorder is built on "
            "the background recorder-init thread and assigned to "
            "_recorder_backing."
        )
        # And the import must appear BEFORE the construction assignment.
        import_idx = init_src.index("from voice_typer.server.recording import Recorder")
        construct_idx = init_src.index("self._recorder_backing = recorder")
        assert import_idx < construct_idx, (
            "The lazy 'from voice_typer.server.recording import Recorder' "
            "statement must appear BEFORE 'self._recorder_backing = "
            "recorder' inside LausuApp._init_recording."
        )

    def test_recorder_import_absent_from_module_top(self) -> None:
        """The module-top source of ``voice_typer.server.app`` must NOT"""
        from voice_typer.server import app as _app_mod

        assert "Recorder" not in _app_mod.__dict__, (
            "Recorder must not be bound in voice_typer.server.app's "
            "module __dict__, the lazy import inside __init__ binds it "
            "as a local, not as a module global."
        )


def _make_process_chunk_pipeline(
    *,
    cached_vad_enabled: bool = True,
) -> tuple[MagicMock, AudioPipeline]:
    """``process_audio_chunk`` tests."""
    recorder = MagicMock(name="RecorderStub")
    recorder._cached_vad_enabled = cached_vad_enabled
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
    pipeline = AudioPipeline(recorder)
    pipeline.detect_device_disconnect = MagicMock(return_value=False)
    pipeline.handle_xrun_status = MagicMock(return_value=False)
    pipeline.apply_filter_chain = MagicMock(return_value=np.array([0.5, -0.5, 0.5, -0.5], dtype=np.float32))
    pipeline.append_to_buffer_locked = MagicMock(return_value=(1, 1))
    pipeline.compute_rms_and_peak = MagicMock(return_value=(0.5, 0.9, 0.032))
    pipeline.detect_and_emit_clipping = MagicMock(return_value=None)
    pipeline.run_vad_state_machine = MagicMock(return_value=None)
    return recorder, pipeline


class TestRawRmsGatedOnCachedVadEnabled:
    """``recorder._cached_vad_enabled`` so it is skipped entirely in raw"""

    def test_raw_rms_skipped_when_vad_disabled(self) -> None:
        """When ``_cached_vad_enabled`` is False, the raw-RMS ``np.dot``"""
        recorder, pipeline = _make_process_chunk_pipeline(cached_vad_enabled=False)
        indata = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)

        pipeline.process_audio_chunk(indata, 4, None, 0, 12345.0)

        # The transient attribute must be the cheap default (0.0), NOT
        assert pipeline._pending_raw_chunk_rms == 0.0, (
            "When VAD is disabled (_cached_vad_enabled=False), the raw "
            "RMS computation must be SKIPPED, _pending_raw_chunk_rms "
            "should be the cheap default 0.0, not "
            f"{pipeline._pending_raw_chunk_rms}."
        )

    def test_raw_rms_computed_when_vad_enabled(self) -> None:
        """When ``_cached_vad_enabled`` is True, the raw-RMS ``np.dot``"""
        recorder, pipeline = _make_process_chunk_pipeline(cached_vad_enabled=True)
        indata = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)
        expected_raw_rms = float(np.sqrt(np.mean(indata**2)))

        pipeline.process_audio_chunk(indata, 4, None, 0, 12345.0)

        assert pipeline._pending_raw_chunk_rms == pytest.approx(expected_raw_rms), (
            "When VAD is enabled (_cached_vad_enabled=True), the raw RMS "
            "of indata must be computed and stored on _pending_raw_chunk_rms "
            f"(expected {expected_raw_rms}, got "
            f"{pipeline._pending_raw_chunk_rms})."
        )
        # And it must NOT be 0.0 (sanity check that the computation ran).
        assert pipeline._pending_raw_chunk_rms != 0.0, "Raw RMS must not be the cheap default 0.0 when VAD is enabled."

    def test_raw_rms_skipped_when_vad_disabled_and_indata_empty(self) -> None:
        """When VAD is disabled AND ``indata`` is empty, the gate"""
        recorder, pipeline = _make_process_chunk_pipeline(cached_vad_enabled=False)
        indata = np.zeros((0,), dtype=np.float32)

        pipeline.process_audio_chunk(indata, 0, None, 0, 12345.0)

        assert pipeline._pending_raw_chunk_rms == 0.0

    def test_raw_rms_gate_uses_cached_scalar_not_property(self) -> None:
        """The gate reads ``recorder._cached_vad_enabled`` (the cached"""
        recorder, pipeline = _make_process_chunk_pipeline(cached_vad_enabled=False)
        recorder._vad.vad_enabled = True  # mismatched, would force computation
        indata = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)

        pipeline.process_audio_chunk(indata, 4, None, 0, 12345.0)

        assert pipeline._pending_raw_chunk_rms == 0.0, (
            "The raw-RMS gate must read 'recorder._cached_vad_enabled' "
            "(the cached scalar = False), NOT 'recorder._vad.vad_enabled' "
            "(the dynamic property = True here). If the gate read the "
            "property, the raw RMS would be ~0.255, not 0.0."
        )


def _make_vad_auto_calibrate_recorder_stub(
    *,
    cached_vad_enabled: bool,
    vad_enabled: bool,
) -> MagicMock:
    """Build a MagicMock ``Recorder`` for ``vad_auto_calibrate`` tests."""
    recorder = MagicMock(name="RecorderStub")
    recorder._cached_vad_enabled = cached_vad_enabled
    recorder._vad.vad_enabled = vad_enabled  # mismatched by design
    recorder._recording_start_time = 100.0
    # ``_vad.auto_calibrate`` is the downstream call, mock counts it.
    recorder._vad.auto_calibrate.return_value = None
    return recorder


class TestVadAutoCalibrateReadsCachedScalar:
    """cached scalar set by ``refresh_vad_caches``), NOT the dynamic"""

    def test_short_circuits_when_cached_scalar_false_even_if_property_true(
        self,
    ) -> None:
        """When ``_cached_vad_enabled`` is False but ``_vad_enabled``"""
        recorder = _make_vad_auto_calibrate_recorder_stub(
            cached_vad_enabled=False,
            vad_enabled=True,
        )

        vad_auto_calibrate(recorder, chunk_rms=0.5, chunk_duration=0.032)

        recorder._vad.auto_calibrate.assert_not_called()

    def test_proceeds_when_cached_scalar_true_even_if_property_false(
        self,
    ) -> None:
        """When ``_cached_vad_enabled`` is True but ``_vad_enabled``"""
        recorder = _make_vad_auto_calibrate_recorder_stub(
            cached_vad_enabled=True,
            vad_enabled=False,
        )

        vad_auto_calibrate(recorder, chunk_rms=0.5, chunk_duration=0.032)

        recorder._vad.auto_calibrate.assert_called_once()

    def test_does_not_call_perf_counter_when_cached_scalar_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """whole point of the cached-scalar optimization (the property"""
        import voice_typer.server.recording.vad_helpers as vad_helpers_mod

        recorder = _make_vad_auto_calibrate_recorder_stub(
            cached_vad_enabled=False,
            vad_enabled=True,
        )

        perf_calls: list[float] = []

        def spy_perf_counter() -> float:
            perf_calls.append(0.0)
            return 0.0

        monkeypatch.setattr(vad_helpers_mod.time, "perf_counter", spy_perf_counter)

        vad_auto_calibrate(recorder, chunk_rms=0.5, chunk_duration=0.032)

        assert perf_calls == [], (
            "time.perf_counter must NOT be called when the cached "
            "scalar gate short-circuits, that's the whole point of "
            f"the cached-scalar optimization (got {len(perf_calls)} calls)."
        )

        # And downstream auto_calibrate was not called either.
        recorder._vad.auto_calibrate.assert_not_called()
