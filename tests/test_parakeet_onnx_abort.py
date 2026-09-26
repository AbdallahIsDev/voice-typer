"""Abort test for the ONNX Parakeet engine."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# NOTE: no module-level ``pytest.importorskip("onnx_asr")``, these
from voice_typer.server.parakeet_engine import ParakeetEngine  # noqa: E402


def _mock_onnx_asr_module(recognize_side_effect=None) -> MagicMock:
    """Build a MagicMock that quacks like ``onnx_asr``."""
    mock = MagicMock(name="mock_onnx_asr")
    mock.__version__ = "0.12.0-test"

    def _make_model(*args, **kwargs):
        m = MagicMock(name="mock_onnx_asr_model")
        if recognize_side_effect is not None:
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
    mock.get_available_providers.return_value = ["CPUExecutionProvider"]
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


def _make_engine_with_mocks(
    device: str = "cpu",
    recognize_side_effect=None,
):
    """Build a ParakeetEngine with mocked onnx_asr + onnxruntime."""
    mock_onnx_asr = _mock_onnx_asr_module(recognize_side_effect=recognize_side_effect)
    mock_onnxruntime = _mock_onnxruntime_module()
    with patch.dict(
        sys.modules,
        {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
    ):
        engine = ParakeetEngine(device=device, language="en")
        ParakeetEngine._ensure_imports()
        with patch.object(type(engine), "_is_cached", return_value=True):
            engine.load()
    return engine, mock_onnx_asr, mock_onnxruntime


class TestParakeetOnnxAbortWiring:
    """``request_abort()`` sets the internal event that the chunk loop"""

    def test_request_abort_sets_internal_event(self):
        """``request_abort()`` sets ``_abort_event`` so the chunk loop"""
        engine, _, _ = _make_engine_with_mocks()
        assert not engine._abort_event.is_set()
        engine.request_abort()
        assert engine._abort_event.is_set()

    def test_clear_abort_clears_internal_event(self):
        """``clear_abort()`` clears ``_abort_event`` so a stale abort"""
        engine, _, _ = _make_engine_with_mocks()
        engine.request_abort()
        assert engine._abort_event.is_set()
        engine.clear_abort()
        assert not engine._abort_event.is_set()

    def test_request_abort_is_safe_without_inflight_recognize(self):
        """When no ``recognize()`` is in flight, ``request_abort()``"""
        engine, _, _ = _make_engine_with_mocks()
        # Must not raise.
        engine.request_abort()
        assert engine._abort_event.is_set()

    def test_request_abort_is_idempotent(self):
        """Calling ``request_abort()`` twice must not raise, the event"""
        engine, _, _ = _make_engine_with_mocks()
        engine.request_abort()
        engine.request_abort()  # must not raise
        assert engine._abort_event.is_set()


class TestParakeetOnnxAbortBetweenChunks:
    """The working abort path: ``_abort_event`` checked between chunks"""

    def test_abort_set_before_loop_skips_all_chunks(self):
        """When ``_abort_event`` is set BEFORE the loop starts, NO chunks"""
        engine, _, _ = _make_engine_with_mocks()
        engine._abort_event.set()

        # 60s of audio → 3 chunks (25s + 25s + 10s).
        audio = np.ones(int(60 * 16000), dtype=np.float32)
        result = engine.transcribe(audio)

        assert result == "", (
            "Expected empty result when abort is set before the loop, no "
            f"chunks should have been decoded. Got: {result!r}"
        )
        (
            engine._model.recognize.assert_not_called(),
            ("model.recognize() must NOT be called when _abort_event is set before the chunk loop starts."),
        )

    def test_abort_between_chunks_stops_loop_early(self):
        """dictation pipeline's cancel path), chunk 2 must NOT be decoded."""
        call_state = {"n": 0}

        def _recognize_then_abort(audio, **kwargs):
            call_state["n"] += 1
            # After the first chunk, set the abort event (mirrors ESC
            if call_state["n"] >= 1:
                _recognize_then_abort.engine._abort_event.set()
            return f"chunk {call_state['n']}"

        engine, _, _ = _make_engine_with_mocks(
            recognize_side_effect=_recognize_then_abort,
        )
        _recognize_then_abort.engine = engine  # type: ignore[attr-defined]
        # Reset the abort event before transcribe (the helper above
        engine._abort_event.clear()
        # 60s of audio → 3 chunks (25s + 25s + 10s).
        audio = np.ones(int(60 * 16000), dtype=np.float32)
        engine.transcribe(audio)

        # Only the first chunk should have been transcribed (abort
        assert call_state["n"] == 1, (
            f"Expected 1 recognize() call (abort after chunk 1 stops the loop), got {call_state['n']}."
        )

    def test_abort_check_is_at_loop_top(self):
        """The abort check must be at the TOP of the loop (before"""
        engine, _, _ = _make_engine_with_mocks()
        # Set abort BEFORE the loop. If the check is at the top, zero
        engine._abort_event.set()
        audio = np.ones(int(60 * 16000), dtype=np.float32)  # 3 chunks
        engine.transcribe(audio)

        assert engine._model.recognize.call_count == 0, (
            "Abort check must be at the TOP of the chunk loop (before "
            "recognize()). A bottom check would decode one extra chunk "
            "after ESC, defeating the bounded-latency contract."
        )

    def test_no_abort_decodes_all_chunks(self):
        """Happy path: when ``_abort_event`` is NOT set, all chunks"""
        engine, _, _ = _make_engine_with_mocks()
        audio = np.ones(int(60 * 16000), dtype=np.float32)  # 3 chunks
        result = engine.transcribe(audio)

        # 3 chunks → 3 recognize() calls.
        assert engine._model.recognize.call_count == 3, (
            "On the happy path (no abort), all chunks must be decoded. "
            f"Got {engine._model.recognize.call_count} recognize() calls."
        )
        # Result is non-empty (the merge of identical texts).
        assert result == "hello world", f"Expected merged result 'hello world', got: {result!r}"

    def test_abort_gate_present_in_source(self):
        """future refactor that accidentally removes the gate."""
        import inspect

        src = inspect.getsource(ParakeetEngine._transcribe_chunks)
        assert "_abort_event.is_set()" in src, (
            "_transcribe_chunks must check _abort_event.is_set() in the "
            "chunk loop. The gate appears to have been removed."
        )
        assert "break" in src, (
            "_transcribe_chunks must `break` out of the chunk loop when "
            "_abort_event is set. The break appears to have been removed."
        )


class TestParakeetOnnxAbortNoRunOptionsPlumbing:
    """Guards against accidental re-introduction of the dead"""

    def test_engine_has_no_run_options_attribute(self):
        """``ParakeetEngine`` instances must NOT carry a ``_run_options``"""
        engine = ParakeetEngine(device="cpu", language="en")
        assert not hasattr(engine, "_run_options"), (
            "ParakeetEngine must NOT have a _run_options attribute, the "
            "RunOptions stash was removed as dead code (onnx-asr 0.12.0 "
            "does not forward run_options to session.run)."
        )

    def test_engine_has_no_make_run_options_method(self):
        """``ParakeetEngine`` must NOT have a ``_make_run_options``"""
        assert not hasattr(ParakeetEngine, "_make_run_options"), (
            "ParakeetEngine must NOT have a _make_run_options method, the "
            "RunOptions stash was removed as dead code (onnx-asr 0.12.0 "
            "does not forward run_options to session.run)."
        )

    def test_request_abort_does_not_touch_run_options(self):
        """``request_abort()`` must NOT call ``RunOptions.set_terminate``"""
        import inspect

        src = inspect.getsource(ParakeetEngine.request_abort)
        assert "set_terminate" not in src, (
            "request_abort() must NOT call RunOptions.set_terminate, "
            "onnx-asr 0.12.0 does not forward run_options to session.run, "
            "so the call cannot reach the in-flight decode."
        )
        assert "run_options" not in src, (
            "request_abort() must NOT reference run_options at all, the stash was removed as dead code."
        )
