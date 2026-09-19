"""Race-condition regression tests for ``QwenEngine.unload``."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server.qwen_engine import QwenEngine


def _make_engine() -> QwenEngine:
    """Build a QwenEngine without importing any heavy ML deps."""
    return QwenEngine(model_path="/fake/qwen/model", device="cpu", language="en")


class TestQwenUnloadWaitsForActiveInference:
    """``unload`` must block on ``_inference_cond`` while a transcription"""

    def test_unload_calls_wait_when_active_inference_positive(self):
        """flight), ``unload`` must call ``_inference_cond.wait()`` instead"""
        engine = _make_engine()
        engine._model = MagicMock()
        engine._active_inference = 1

        # ``wait()`` would block forever in a unit test (no real
        def _fake_wait(*args, **kwargs):
            engine._active_inference = 0
            return True

        with (
            patch.object(engine._inference_cond, "wait", side_effect=_fake_wait) as mock_wait,
            patch("voice_typer.server.transcription.release_gpu_memory"),
        ):
            engine.unload()

        assert mock_wait.called, (
            "OI-13: unload() must call _inference_cond.wait() when "
            "_active_inference > 0. Pre-fix, unload() nulled self._model "
            "without waiting, racing concurrent transcribe() and "
            "triggering use-after-free on the PyTorch module."
        )
        assert engine._model is None

    def test_unload_does_not_wait_when_no_active_inference(self):
        """When ``_active_inference == 0`` (idle), ``unload`` must NOT"""
        engine = _make_engine()
        engine._model = MagicMock()
        assert engine._active_inference == 0

        with (
            patch.object(engine._inference_cond, "wait") as mock_wait,
            patch("voice_typer.server.transcription.release_gpu_memory"),
        ):
            engine.unload()

        assert not mock_wait.called, (
            "OI-13: unload() must NOT call wait() on the idle path "
            "(_active_inference == 0). Spurious blocking would defeat "
            "the RACE-023 gc.collect()-outside-the-lock optimization."
        )
        assert engine._model is None

    def test_unload_wakes_when_inference_completes_on_other_thread(self):
        """
        End-to-end: a real ``transcribe`` call on another thread must
        This pins the notify_all() side of the contract, without it,
        """
        engine = _make_engine()
        mock_transcription = MagicMock()
        mock_transcription.text = "hello"
        mock_model = MagicMock()
        mock_model.transcribe.return_value = [mock_transcription]
        engine._model = mock_model

        unload_done = threading.Event()
        unload_order: list[str] = []

        def _run_unload():
            with patch("voice_typer.server.transcription.release_gpu_memory"):
                engine.unload()
                unload_order.append("unload_done")
                unload_done.set()

        # Hold the inference slot so unload() blocks on wait().
        with engine._inference_cond:
            engine._active_inference += 1

        t = threading.Thread(target=_run_unload)
        t.start()

        # Give unload a moment to enter wait().
        import time

        time.sleep(0.05)
        assert not unload_done.is_set(), "unload should be blocked on wait()"

        # Release the inference slot, transcribe's finally block does
        with engine._inference_cond:
            engine._active_inference -= 1
            engine._inference_cond.notify_all()
            unload_order.append("inference_done")

        assert unload_done.wait(timeout=2.0), (
            "OI-13: unload() must wake when _active_inference returns to 0 "
            "and notify_all() is called. A stuck wait() means the "
            "transcribe→unload coordination is broken."
        )
        assert unload_order == ["inference_done", "unload_done"], (
            f"OI-13: unload() must complete AFTER the inference slot is released. Got order: {unload_order}"
        )
        t.join(timeout=2.0)

        assert engine._model is None


class TestQwenTranscribeBalancesActiveInference:
    """decrement it on exit (including the exception path), so ``unload``"""

    def test_transcribe_increments_and_decrements_on_success(self):
        engine = _make_engine()
        mock_transcription = MagicMock()
        mock_transcription.text = "hello"
        engine._model = MagicMock()
        engine._model.transcribe.return_value = [mock_transcription]

        assert engine._active_inference == 0
        result = engine.transcribe(np.ones(16000, dtype=np.float32))
        assert result == "hello"
        assert engine._active_inference == 0, (
            "OI-13: transcribe() must decrement _active_inference back to 0 "
            "in its finally block on the happy path. A stuck positive "
            "counter would make unload() wait forever."
        )

    def test_transcribe_decrements_on_exception(self):
        """If ``model.transcribe`` raises, the ``finally`` block must"""
        engine = _make_engine()
        engine._model = MagicMock()
        engine._model.transcribe.side_effect = RuntimeError("CUDA OOM")

        with pytest.raises(RuntimeError, match="CUDA OOM"):
            engine.transcribe(np.ones(16000, dtype=np.float32))

        assert engine._active_inference == 0, (
            "OI-13: transcribe() must decrement _active_inference in its "
            "finally block EVEN IF inference raises. A stuck positive "
            "counter after a CUDA OOM would deadlock the next unload()."
        )

    def test_transcribe_increments_during_inference(self):
        """While ``model.transcribe`` is running, ``_active_inference``"""
        engine = _make_engine()
        mock_transcription = MagicMock()
        mock_transcription.text = "hello"
        engine._model = MagicMock()

        observed: list[int] = []

        def _spy_transcribe(*args, **kwargs):
            # Observed from the inference thread while the lock is
            observed.append(engine._active_inference)
            return [mock_transcription]

        engine._model.transcribe.side_effect = _spy_transcribe

        engine.transcribe(np.ones(16000, dtype=np.float32))

        assert observed == [1], (
            "OI-13: _active_inference must be 1 while model.transcribe() "
            "is running (incremented under _lock before the call, "
            f"decremented in finally). Got: {observed}"
        )


class TestQwenInferenceEventRemoved:
    """The dead ``_inference_event`` attribute (set/cleared but never"""

    def test_init_does_not_create_inference_event(self):
        """``QwenEngine.__init__`` must NOT create ``_inference_event``."""
        engine = _make_engine()
        assert not hasattr(engine, "_inference_event"), (
            "OI-13: the dead _inference_event attribute must be removed "
            "from QwenEngine.__init__. It was set/cleared but never read, "
            "and coexisted confusingly with the canonical _active_inference "
            "counter pattern."
        )

    def test_init_creates_active_inference_and_cond(self):
        """``__init__`` must create the canonical coordination attrs."""
        engine = _make_engine()
        assert hasattr(engine, "_active_inference"), (
            "OI-13: __init__ must create _active_inference (the counter transcribe increments / unload waits on)."
        )
        assert engine._active_inference == 0
        assert hasattr(engine, "_inference_cond"), (
            "OI-13: __init__ must create _inference_cond (the Condition transcribe notifies on / unload waits on)."
        )
        assert isinstance(engine._inference_cond, threading.Condition)
