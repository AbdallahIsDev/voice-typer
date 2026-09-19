"""Tests for the Silero VAD wrapper (ONNX Runtime backend)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest


class _FakeNode:
    """Minimal stand-in for ``onnxruntime.NodeArg``, only ``name`` is read."""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeOrtSession:
    """Fake ``onnxruntime.InferenceSession`` for VAD tests."""

    def __init__(
        self,
        prob_sequence: list[float] | None = None,
        state_delta: float = 1.0,
    ) -> None:
        # ``prob_sequence`` lets a test script a series of probabilities
        self._prob_seq = list(prob_sequence) if prob_sequence else [0.5]
        self._prob_idx = 0
        # ``state_delta`` makes state threading observable: each call
        self._state_delta = float(state_delta)
        self.calls: list[dict[str, object]] = []

    def get_inputs(self) -> list[_FakeNode]:
        return [
            _FakeNode("input"),
            _FakeNode("state"),
            _FakeNode("sr"),
        ]

    def get_outputs(self) -> list[_FakeNode]:
        return [
            _FakeNode("output"),
            _FakeNode("stateN"),
        ]

    def run(self, output_names, feed):
        # Capture the call for assertions. Copy the input + state arrays
        input_arr = np.array(feed["input"], copy=True)
        state_arr = np.array(feed["state"], copy=True)
        sr_value = int(feed["sr"]) if "sr" in feed else None
        self.calls.append(
            {
                "input": input_arr,
                "state": state_arr,
                "sr": sr_value,
            }
        )
        # Pull the next probability from the script. Reuse the last
        if self._prob_idx < len(self._prob_seq):
            prob = float(self._prob_seq[self._prob_idx])
        else:
            prob = float(self._prob_seq[-1])
        self._prob_idx += 1
        # Silero v4 ONNX ``output`` shape is ``(1, 1)`` for a single
        out_prob = np.array([[prob]], dtype=np.float32)
        # Return the input state + delta so the next call's input state
        new_state = state_arr + self._state_delta
        return [out_prob, new_state]


def _install_fake_ort(monkeypatch, session: FakeOrtSession) -> MagicMock:
    """Install a fake ``onnxruntime`` module whose ``InferenceSession``"""
    mock_ort = MagicMock(name="fake_onnxruntime")
    mock_ort.InferenceSession = MagicMock(return_value=session)
    monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)
    return mock_ort


class TestSileroVadHandlesNon512SampleChunks:
    """VAD-001: Silero VAD must handle non-512-sample chunks."""

    def test_compute_vad_prob_handles_non_512_chunk(self, monkeypatch):
        """compute_vad_prob must not crash on a 1136-sample chunk."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.75])
        _install_fake_ort(monkeypatch, session)
        # Point _VAD_MODEL_PATH at an existing file so _load_model
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # 1136-sample chunk (typical WASAPI block)
        audio = np.ones(1136, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        # Use approx because the fake ORT session returns float32;
        assert prob == pytest.approx(0.75)
        # 1136 // 512 = 2 sub-chunks; trailing 112 samples dropped.
        assert len(session.calls) == 2
        for call in session.calls:
            assert call["input"].shape == (1, 512), (
                f"Each sub-chunk must be reshaped to (1, 512); got {call['input'].shape}"
            )
        vad.reset()

    def test_compute_vad_prob_handles_small_chunk(self, monkeypatch):
        """compute_vad_prob must pad a 100-sample chunk to 512."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.3])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(100, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == pytest.approx(0.3)
        assert len(session.calls) == 1
        assert session.calls[0]["input"].shape == (1, 512)
        vad.reset()

    def test_compute_vad_prob_handles_exact_512_chunk(self, monkeypatch):
        """compute_vad_prob must work with exactly 512 samples."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.9])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(512, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == pytest.approx(0.9)
        assert len(session.calls) == 1
        assert session.calls[0]["input"].shape == (1, 512)
        vad.reset()


class TestSileroVadSlicesLongChunksIntoSubchunks:
    """AUDIO-10: long audio chunks must be sliced into 512-sample"""

    def test_long_chunk_processes_all_full_subchunks(self, monkeypatch):
        """AUDIO-10: a 1136-sample chunk produces 2 model calls"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5, 0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(1136, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert len(session.calls) == 2, f"Expected 2 sub-chunk calls for 1136 samples, got {len(session.calls)}"
        for call in session.calls:
            assert call["input"].shape == (1, 512)
        assert prob == pytest.approx(0.5)
        vad.reset()

    def test_long_chunk_takes_max_probability(self, monkeypatch):
        """AUDIO-10: when sub-chunks return different probabilities,"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.2, 0.85])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # 1024 samples → exactly 2 sub-chunks of 512.
        audio = np.ones(1024, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        # Max of [0.2, 0.85] = 0.85, speech in the second sub-chunk
        assert prob == pytest.approx(0.85), f"Expected max prob 0.85 (speech in 2nd sub-chunk), got {prob}"
        vad.reset()

    def test_very_long_chunk_processes_all_subchunks(self, monkeypatch):
        """AUDIO-10: a 5120-sample chunk (10× the Silero block size)"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.6] * 10)
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(5120, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert len(session.calls) == 10, f"Expected 10 sub-chunk calls for 5120 samples, got {len(session.calls)}"
        for call in session.calls:
            assert call["input"].shape == (1, 512)
        assert prob == pytest.approx(0.6)
        vad.reset()

    def test_long_chunk_with_odd_remainder_drops_remainder(self, monkeypatch):
        """AUDIO-10: a 1500-sample chunk yields 2 sub-chunks of 512"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.4, 0.4])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(1500, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert len(session.calls) == 2, f"Expected 2 sub-chunk calls for 1500 samples, got {len(session.calls)}"
        for call in session.calls:
            assert call["input"].shape == (1, 512)
        assert prob == pytest.approx(0.4)
        vad.reset()


class TestHiddenStateThreading:
    """Companion §2.2, the LSTM hidden state (shape ``(2, 1, 128)``"""

    def test_state_buffer_has_correct_shape(self, monkeypatch):
        """``_state`` must be ``(2, 1, 128)`` float32 after load."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        vad._load_model()
        assert vad._state is not None
        assert vad._state.shape == (2, 1, 128), (
            f"Silero v4 LSTM state shape must be (2, 1, 128); got {vad._state.shape}"
        )
        assert vad._state.dtype == np.float32
        vad.reset()

    def test_state_threads_forward_across_calls(self, monkeypatch):
        """Each ``session.run`` call must receive the previous call's"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5, 0.5, 0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # 1536 samples → 3 sub-chunks of 512.
        audio = np.ones(1536, dtype=np.float32) * 0.1
        vad.compute_vad_prob(audio, sample_rate=16000)

        assert len(session.calls) == 3
        # First call: state = zeros (initial load).
        assert np.array_equal(session.calls[0]["state"], np.zeros((2, 1, 128), dtype=np.float32))
        # Second call: state = first call's return = 0.0 + 1.0 = 1.0
        assert np.allclose(session.calls[1]["state"], 1.0)
        # Third call: state = second call's return = 1.0 + 1.0 = 2.0
        assert np.allclose(session.calls[2]["state"], 2.0)
        vad.reset()

    def test_reset_states_zeros_buffer_when_loaded(self, monkeypatch):
        """``reset_states()`` must re-zero ``_state`` when the session"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # Load + run inference to populate _state with non-zero values.
        vad._load_model()
        audio = np.ones(512, dtype=np.float32) * 0.1
        vad.compute_vad_prob(audio, sample_rate=16000)
        assert not np.array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32)), (
            "Test setup: _state should be non-zero after an inference call"
        )

        vad.reset_states()
        assert np.array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32)), (
            "reset_states() must zero the LSTM hidden buffer"
        )
        vad.reset()

    def test_unload_clears_session_and_state(self, monkeypatch):
        """``unload()`` must drop the ORT session AND reset the hidden"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        vad._load_model()
        assert vad._model is not None
        assert vad._state is not None

        vad.unload()
        assert vad._model is None
        # ``reset_states()`` no-session branch).
        assert vad._state is None
        vad.reset()

    def test_reset_states_noop_when_unloaded(self, monkeypatch):
        """``reset_states()`` must NOT trigger a model load when called"""
        import voice_typer.server.vad as vad

        vad.reset()
        # No fake ORT installed → if reset_states triggered a load,
        vad.reset_states()
        assert vad._model is None
        assert vad._state is None
        vad.reset()

    def test_state_zeroed_on_first_load(self, monkeypatch):
        """The first ``_load_model()`` call must initialize ``_state``"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # Pre-condition: _state is None before the first load.
        assert vad._state is None

        vad._load_model()
        assert vad._state is not None
        assert vad._state.shape == (2, 1, 128)
        assert np.array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32)), (
            "First load must initialize _state to zeros"
        )
        vad.reset()


@pytest.fixture
def bubble():
    """``tests/test_waveform_bubble.py`` (moved alongside"""
    from voice_typer.server.waveform import WaveformBubble

    return WaveformBubble()


class TestVADModule:
    """Test the VAD wrapper module (voice_typer.server.vad)."""

    def test_is_available_returns_bool(self):
        """is_available() should return True or False, not raise."""
        from voice_typer.server.vad import is_available

        result = is_available()
        assert isinstance(result, bool)

    def test_compute_vad_prob_without_ort(self, monkeypatch):
        """When onnxruntime is not available, compute_vad_prob returns None."""
        from voice_typer.server import vad

        # Force onnxruntime to be unimportable.
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        vad.reset()
        result = vad.compute_vad_prob(np.zeros(16000, dtype=np.float32))
        assert result is None
        vad.reset()

    def test_is_speech_fallback_rms(self, monkeypatch):
        """Without VAD, is_speech falls back to RMS energy check."""
        from voice_typer.server import vad

        vad.reset()
        # Force VAD to be unavailable so RMS fallback is exercised
        monkeypatch.setattr(vad, "_load_model", lambda: (None, None))
        # Silence
        assert vad.is_speech(np.zeros(16000, dtype=np.float32)) is False
        # Loud audio
        assert vad.is_speech(np.full(16000, 0.1, dtype=np.float32)) is True
        vad.reset()

    def test_is_speech_empty_audio(self):
        """Empty audio chunk should return False."""
        from voice_typer.server.vad import is_speech

        assert is_speech(np.array([], dtype=np.float32)) is False

    def test_reset_clears_model(self):
        """reset() should clear the cached model + state."""
        from voice_typer.server import vad

        vad.reset()
        assert vad._model is None
        assert vad._state is None

    def test_ort_missing_warning_rate_limited(self, caplog, monkeypatch):
        """When onnxruntime is unavailable, repeated ``_load_model``"""
        import logging

        from voice_typer.server import vad

        vad.reset()
        monkeypatch.setitem(sys.modules, "onnxruntime", None)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.vad"):
            for _ in range(5):
                assert vad._load_model() == (None, None)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, (
            f"onnxruntime-missing WARNING must be rate-limited to 1 occurrence across 5 calls; got {len(warnings)}"
        )
        vad.reset()

    def test_bundled_model_missing_error_rate_limited(self, caplog, monkeypatch):
        """When the bundled ``silero_vad.onnx`` is missing, repeated"""
        import logging
        from pathlib import Path

        from voice_typer.server import vad

        vad.reset()
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path("/nonexistent/silero_vad.onnx"))
        # ORT is importable (mock) so we exercise the missing-file branch,
        _install_fake_ort(monkeypatch, FakeOrtSession(prob_sequence=[0.5]))

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.vad"):
            for _ in range(5):
                assert vad._load_model() == (None, None)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1, (
            f"missing-model ERROR must be rate-limited to 1 occurrence across 5 calls; got {len(errors)}"
        )
        vad.reset()

    def test_local_load_failure_error_rate_limited(self, caplog, monkeypatch):
        """When ``InferenceSession(...)`` raises (corrupt/undownloadable"""
        import logging

        from voice_typer.server import vad

        vad.reset()
        mock_ort = MagicMock(name="fake_onnxruntime")
        mock_ort.InferenceSession = MagicMock(side_effect=RuntimeError("corrupt onnx model"))
        monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.vad"):
            for _ in range(5):
                assert vad._load_model() == (None, None)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1, (
            f"load-failure ERROR must be rate-limited (1st + every Nth only); got {len(errors)} across 5 calls"
        )
        assert vad._model is None, "failure must not cache a model"
        vad.reset()

    def test_providers_pinned_to_cpu(self, monkeypatch):
        """design; routing to GPU adds upload latency per 512-sample"""
        import inspect

        from voice_typer.server import vad

        src = inspect.getsource(vad._load_model)
        assert 'providers=["CPUExecutionProvider"]' in src, (
            "VAD must pin providers=['CPUExecutionProvider']: see "
            "companion §2.3.3 for the rationale (CPU-only by design, "
            "GPU upload latency dwarfs the ~0.5ms inference for a "
            "512-sample window)."
        )

    def test_no_torch_import_in_vad_source(self):
        """Companion §1: ``vad.py`` must NOT import torch anywhere."""
        import inspect

        from voice_typer.server import vad

        src = inspect.getsource(vad)
        # ``torch`` may appear in comments / docstrings as a historical
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith("import torch"), (
                "vad.py must not 'import torch', torch is removed as a "
                "project dependency under the ONNX migration (companion §1)."
            )
            assert not stripped.startswith("from torch"), (
                "vad.py must not 'from torch import ...', torch is removed "
                "as a project dependency under the ONNX migration (companion §1)."
            )


class TestWaveformVADGate:
    """Test that WaveformBubble.update_level uses the RMS-only path."""

    def test_update_level_without_audio_chunk(self, bubble):
        """When no audio_chunk is passed, update_level works as before (RMS-only)."""
        bubble.update_level(0.1, 0.2)
        assert abs(bubble.rms_level - 0.045) < 0.01  # smoothed
        assert bubble.is_speaking is True  # 0.045 > 0.01 threshold

    def test_update_level_with_silent_audio_chunk(self, bubble, monkeypatch):
        """With a silent-looking input but non-zero RMS, the RMS-only path"""
        bubble.update_level(0.15, 0.3)
        # RMS-only path: smoothed level is 0.5 * 0 (initial) + 0.5 * 0.15 = 0.075
        assert bubble.rms_level > 0
        assert bubble.is_speaking is True  # 0.075 > 0.005 threshold

    def test_update_level_with_speech_audio_chunk(self, bubble, monkeypatch):
        """With speech-level RMS input, the RMS-only path updates normally"""
        bubble.update_level(0.15, 0.3)
        assert bubble.rms_level > 0
        assert bubble.is_speaking is True

    def test_update_level_with_zero_rms_decays(self, bubble):
        """With zero RMS (true silence), the level decays and is_speaking"""
        # Prime the level
        bubble.update_level(0.2, 0.4)
        assert bubble.is_speaking is True
        # Feed silence
        for _ in range(20):
            bubble.update_level(0.0, 0.0)
        assert bubble.rms_level < 0.005
        assert bubble.is_speaking is False


class TestProductionWiring:
    """Pin the recorder→controller→bubble RMS-callback wiring as it"""

    def test_app_on_recorder_rms_is_two_arg_and_forwards(self):
        """RecordingController.on_recorder_rms must be a 2-arg"""
        import inspect
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController

        sig = inspect.signature(RecordingController.on_recorder_rms)
        assert "audio_chunk" not in sig.parameters, (
            "on_recorder_rms must NOT carry the dead audio_chunk kwarg, "
            "no production caller passes it and update_level is RMS-only"
        )
        assert list(sig.parameters) == ["self", "rms", "peak"], (
            "on_recorder_rms must match the recorder's 2-arg on_rms_level callback contract (rms, peak)"
        )

        controller = MagicMock(spec=RecordingController)
        controller._app = MagicMock()
        RecordingController.on_recorder_rms(controller, 0.05, 0.12)
        controller._app._waveform_bubble.update_level.assert_called_once_with(0.05, 0.12)

    def test_recorder_rms_callback_invoked_with_exactly_two_args(self):
        """EXACTLY two positional arguments ``(chunk_rms, chunk_peak)``."""
        import collections
        import threading

        from voice_typer.server.recording.audio_pipeline import AudioPipeline

        recorder = MagicMock(name="RecorderStub")
        pipeline = AudioPipeline(recorder)
        # Stub the heavy helpers (no real audio I/O, no VAD model, no
        pipeline.detect_device_disconnect = MagicMock(return_value=False)
        pipeline.handle_xrun_status = MagicMock(return_value=False)
        pipeline.apply_filter_chain = MagicMock(return_value=np.array([0.1, -0.2, 0.3], dtype=np.float32))
        pipeline.append_to_buffer_locked = MagicMock(return_value=(1, 1))
        pipeline.compute_rms_and_peak = MagicMock(return_value=(0.05, 0.12, 0.032))
        pipeline.run_vad_state_machine = MagicMock()
        pipeline.detect_and_emit_clipping = MagicMock()
        # append contract).
        pipeline._lock = threading.Lock()
        recorder._recent_rms_values = collections.deque(maxlen=10)
        recorder._last_rms = None
        recorder._rms_callback_error_count = 0
        # VAD caches off: skips the np.dot branch in raw mode.
        recorder._cached_vad_enabled = False
        recorder.on_rms_level = MagicMock(name="on_rms_level")
        recorder.on_silence_warning = None
        recorder.on_silence_auto_stop = None
        recorder.on_max_duration_auto_stop = None
        recorder._recording_start_time = 100.0

        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)
        pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        recorder.on_rms_level.assert_called_once()
        args, kwargs = recorder.on_rms_level.call_args
        assert kwargs == {}, "on_rms_level must be invoked with positional args only"
        assert len(args) == 2, (
            "on_rms_level must receive EXACTLY 2 arguments (chunk_rms, chunk_peak), "
            "the 3-arg form was removed in BUBBLE-FIX-4.1 (forwarding the filtered "
            "chunk re-opens the native-rate→16 kHz Silero bias)"
        )
        assert args[0] == 0.05  # chunk_rms from compute_rms_and_peak
        assert args[1] == 0.12  # chunk_peak from compute_rms_and_peak

    def test_update_level_signature_has_no_audio_chunk(self):
        """WaveformBubble.update_level must be 2-arg (rms, peak)."""
        import inspect

        from voice_typer.server.waveform import WaveformBubble

        sig = inspect.signature(WaveformBubble.update_level)
        assert "audio_chunk" not in sig.parameters, (
            "WaveformBubble.update_level must NOT carry the dead audio_chunk "
            "parameter, the VAD gate is removed and the visualizer is RMS-only"
        )


class TestVadLocalOnlyNoNetwork:
    """C-DATA-1 regression: the VAD module must NEVER make a network call."""

    _VAD_SRC_PATH = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "vad.py"

    def test_load_model_returns_quickly_without_network(self, monkeypatch):
        """``_load_model`` must not block on a network timeout."""
        import time

        from voice_typer.server import vad

        vad.reset()
        # Point the path at a nonexistent file so the local-load
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path("/nonexistent/silero_vad.onnx"))
        # Install a fake ORT so we exercise the missing-file branch
        _install_fake_ort(monkeypatch, FakeOrtSession(prob_sequence=[0.5]))

        start = time.monotonic()
        result = vad._load_model()
        elapsed = time.monotonic() - start

        assert result == (None, None)
        assert elapsed < 3.0, (
            f"_load_model took {elapsed:.2f}s, looks like a network call was attempted (C-DATA-1 violation)"
        )
        vad.reset()

    def test_no_torch_hub_load_in_vad_source(self):
        """
        ``vad.py`` must not import or call ``torch.hub.load``.
        ``requests.get`` / ``urllib`` etc., defensive pin against any
        """
        src = self._VAD_SRC_PATH.read_text(encoding="utf-8")
        assert "torch.hub.load" not in src, (
            "C-DATA-1 violation: voice_typer/server/vad.py references "
            "'torch.hub.load', a hard network call to github.com that "
            "breaks the offline guarantee."
        )
        assert "hub_load" not in src, (
            "C-DATA-1 violation: voice_typer/server/vad.py still has a "
            "'hub_load' reference, the network-fallback helper / "
            "negative-cache flag should have been removed entirely."
        )
        assert "_HUB_LOAD_TIMEOUT_S" not in src, (
            "Dead-code leftover: _HUB_LOAD_TIMEOUT_S was the deadline "
            "for the (now-removed) torch.hub.load fallback and should "
            "have been removed with it."
        )

    def test_load_model_returns_none_none_when_model_missing(self, monkeypatch):
        """When the bundled model is missing, ``_load_model`` returns"""
        from voice_typer.server import vad

        vad.reset()
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path("/nonexistent/silero_vad.onnx"))
        # ORT is importable (mock) so we exercise the missing-file path
        _install_fake_ort(monkeypatch, FakeOrtSession(prob_sequence=[0.5]))

        # Must not raise.
        result = vad._load_model()
        assert result == (None, None)
        # And the cached _model must remain None so subsequent
        assert vad._model is None
        vad.reset()


class TestPreloadWarmup:
    """reset the LSTM state so the first real audio chunk starts clean."""

    def test_preload_runs_warmup_and_resets_state(self, monkeypatch):
        """512-sample zero tensor), then reset_states() so the production"""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        assert vad.preload() is True
        # Exactly one warmup call.
        assert len(session.calls) == 1
        assert session.calls[0]["input"].shape == (1, 512)
        assert np.array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32)), (
            "preload() must reset_states() after warmup so the first real audio chunk starts from a clean LSTM state"
        )
        vad.reset()

    def test_preload_returns_false_when_ort_missing(self, monkeypatch):
        """preload() must return False (not raise) when onnxruntime is"""
        from voice_typer.server import vad

        vad.reset()
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        assert vad.preload() is False
        assert vad._model is None
        vad.reset()
