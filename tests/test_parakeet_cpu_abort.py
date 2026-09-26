"""CPU-fallback abort regression tests for ``ParakeetEngine``."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server.parakeet_engine import (  # noqa: E402
    ParakeetEngine,
)


def _mock_onnx_asr_module(recognize_side_effect=None) -> MagicMock:
    """Build a MagicMock that quacks like ``onnx_asr``."""
    mock = MagicMock(name="mock_onnx_asr")
    mock.__version__ = "0.12.0-test"

    def _make_model(*args, **kwargs):
        m = MagicMock(name="mock_onnx_asr_model")
        if isinstance(recognize_side_effect, Exception) or callable(recognize_side_effect):
            m.recognize.side_effect = recognize_side_effect
        else:
            m.recognize.return_value = "hello world"
        return m

    mock.load_model.side_effect = _make_model
    return mock


def _mock_onnxruntime_module() -> MagicMock:
    """Build a MagicMock that quacks like ``onnxruntime``."""
    mock = MagicMock(name="mock_onnxruntime")
    mock.__version__ = "1.20.0-test"
    mock.get_available_providers.return_value = [
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]
    mock.RuntimeException = type("RuntimeException", (Exception,), {})
    return mock


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state(monkeypatch, tmp_path):
    """Reset ``ParakeetEngine`` class-level state between tests."""
    # load() verifies every snapshot under snapshot_search_dirs; point the
    # search at a tmp snapshot and treat it as verified, so tests never
    # touch the real HF cache (which may be absent/incomplete here).
    snap = tmp_path / "models--grikdotnet--parakeet-tdt-0.6b-fp16" / "snapshots" / "test123"
    snap.mkdir(parents=True)
    (snap / "model.onnx").write_bytes(b"fake-onnx")
    monkeypatch.setattr(
        "voice_typer.server.model_availability.snapshot_search_dirs",
        lambda config_dir, repo_id: [snap.parent.parent],
    )
    monkeypatch.setattr(
        "voice_typer.server.security.verify_model_integrity",
        lambda snapshot, repo_id: True,
    )
    saved = (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._onnx_asr,
        ParakeetEngine._ort,
    )
    ParakeetEngine._imports_loaded = False
    ParakeetEngine._onnx_asr = None
    ParakeetEngine._ort = None
    yield
    (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._onnx_asr,
        ParakeetEngine._ort,
    ) = saved


def _make_engine_with_cuda_loaded():
    """Build a ParakeetEngine with a mocked CUDA-loaded ONNX model."""
    mock_onnx_asr = _mock_onnx_asr_module(recognize_side_effect="hello world")
    mock_onnxruntime = _mock_onnxruntime_module()
    with patch.dict(
        sys.modules,
        {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
    ):
        engine = ParakeetEngine(device="cuda", language="en")
        ParakeetEngine._ensure_imports()
        with patch.object(type(engine), "_is_cached", return_value=True):
            engine.load()
    return engine, mock_onnx_asr, mock_onnxruntime


class TestParakeetCpuFallbackAbortGate:
    """
    Source-level guards + behavioral smoke tests for the inter-chunk
    The original OI-14 contract (ESC stops the chunk loop with bounded
    """

    def test_chunk_loop_has_abort_gate_in_source(self):
        """future refactor that accidentally removes the gate, without"""
        import inspect

        src = inspect.getsource(ParakeetEngine._transcribe_chunks)
        assert "_abort_event.is_set()" in src, (
            "_transcribe_chunks must check _abort_event.is_set() in the "
            "chunk loop. The gate appears to have been removed, the CPU "
            "fallback path would no longer respect ESC."
        )
        assert "break" in src, (
            "_transcribe_chunks must `break` out of the chunk loop when "
            "_abort_event is set. The break appears to have been removed."
        )

    def test_abort_gate_at_loop_top_in_source(self):
        """``_transcribe_segment`` call inside the chunk loop (not after)."""
        import inspect

        src = inspect.getsource(ParakeetEngine._transcribe_chunks)
        # The abort check must come before the segment call.
        abort_idx = src.find("_abort_event.is_set()")
        segment_idx = src.find("_transcribe_segment")
        assert abort_idx != -1, "abort gate (_abort_event.is_set()) missing from _transcribe_chunks source"
        assert segment_idx != -1, "_transcribe_segment call missing from _transcribe_chunks source"
        assert abort_idx < segment_idx, (
            "Abort check must come BEFORE _transcribe_segment call in the "
            "chunk loop. A bottom-of-loop check would decode one extra "
            "chunk after ESC, defeating the bounded-latency contract."
        )

    def test_abort_event_clears_on_clear_abort(self):
        """``clear_abort()`` must clear ``_abort_event`` so a stale"""
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._abort_event.set()
        engine.clear_abort()
        assert not engine._abort_event.is_set()

    def test_fallback_re_runs_audio_on_cpu_after_cuda_error(self):
        """Behavioral smoke test: after a CUDA error triggers"""
        # GPU model: raise a CUDA OOM on the first chunk.
        gpu_model = MagicMock(name="gpu_model")
        gpu_model.recognize.side_effect = RuntimeError("CUDA out of memory")
        # CPU-fallback model: succeed with a fixed text.
        cpu_model = MagicMock(name="cpu_model")
        cpu_model.recognize.return_value = "cpu result"

        engine, mock_onnx_asr, _ = _make_engine_with_cuda_loaded()
        engine._model = gpu_model
        mock_onnx_asr.load_model.side_effect = lambda *args, **kwargs: cpu_model

        # 1s of audio (single segment, matches the fallback path).
        audio = np.ones(16000, dtype=np.float32)
        with patch("voice_typer.server.event_bus.publish"):
            result = engine.transcribe_with_fallback(audio)

        # The CPU fallback must have run on CPU and returned the text.
        assert result == "cpu result", f"Expected 'cpu result' from the CPU fallback path, got: {result!r}"
        # GPU model's recognize() was called once (raised OOM).
        assert gpu_model.recognize.call_count == 1
        # CPU model's recognize() was called once (the re-transcribe).
        assert cpu_model.recognize.call_count == 1, (
            "CPU fallback must call the recreated model's recognize() once "
            f"for the re-transcribe. Got {cpu_model.recognize.call_count} calls."
        )
        # The fallback mutated device to "cpu" (session stays on CPU
        assert engine.device == "cpu"
