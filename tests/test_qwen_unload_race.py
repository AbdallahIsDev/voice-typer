"""Race-condition regression tests for ``QwenEngine.unload``.

Pre-fix (OI-13): ``QwenEngine.unload`` nullified ``self._model`` under
``self._lock`` WITHOUT waiting for an in-flight ``transcribe`` call.
``transcribe`` already released ``self._lock`` for the multi-second GPU
inference (RACE-032), so a concurrent ``unload`` (e.g. user-initiated
model swap mid-dictation) freed the PyTorch module while the inference
thread was still dereferencing it — a classic use-after-free that
manifested as intermittent CUDA illegal-memory-access crashes.

The fix ports the canonical pattern from ``ParakeetEngine``:
``transcribe`` increments an ``_active_inference`` counter under a
``Condition(self._lock)`` before releasing the lock for inference, and
decrements it in a ``finally`` block; ``unload`` waits on the Condition
for the counter to return to 0 before nulling ``self._model``. The dead
``_inference_event`` attribute (set/cleared but never read) is removed.

These tests pin the contract:

1. ``unload`` calls ``_inference_cond.wait()`` when
   ``_active_inference > 0`` (the race is closed).
2. ``unload`` does NOT call ``wait()`` when ``_active_inference == 0``
   (no spurious blocking on the idle path).
3. ``transcribe`` increments ``_active_inference`` on entry and
   decrements it on exit (the counter is balanced on the happy path).
4. ``transcribe`` decrements ``_active_inference`` in the ``finally``
   block EVEN IF inference raises (no stuck counter on failure).
5. The dead ``_inference_event`` attribute is gone from ``__init__``
   (set/cleared but never read — confirmed dead by the source-level
   ``_inference_event`` reference audit).
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server.qwen_engine import QwenEngine


def _make_engine() -> QwenEngine:
    """Build a QwenEngine WITHOUT importing torch / qwen_asr."""
    return QwenEngine(model_path="/fake/qwen/model", device="cpu", language="en")


# ── OI-13: unload() must wait for active inference ────────────────────


class TestQwenUnloadWaitsForActiveInference:
    """``unload`` must block on ``_inference_cond`` while a transcription
    is in flight, so the model is not freed mid-inference."""

    def test_unload_calls_wait_when_active_inference_positive(self):
        """When ``_active_inference > 0`` (a transcribe call is in
        flight), ``unload`` must call ``_inference_cond.wait()`` instead
        of immediately nulling ``self._model``.

        Pre-fix, ``unload`` would null ``self._model`` while the
        inference thread was still dereferencing it — use-after-free.
        """
        engine = _make_engine()
        engine._model = MagicMock()
        # Simulate an in-flight transcribe() that has incremented the
        # counter and released the lock for the GPU call.
        engine._active_inference = 1

        # ``wait()`` would block forever in a unit test (no real
        # inference thread will decrement + notify). Side-effect: simulate
        # the inference thread finishing by zeroing the counter, so the
        # while-loop exits after one ``wait()`` call.
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
        """When ``_active_inference == 0`` (idle), ``unload`` must NOT
        call ``wait()`` — the idle path must remain non-blocking."""
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
        """End-to-end: a real ``transcribe`` call on another thread must
        release the inference slot (decrement + notify), allowing
        ``unload`` to proceed.

        This pins the notify_all() side of the contract — without it,
        ``unload`` would wait forever even after inference finished.
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
        # (wait() releases the Condition's lock while parked, so we can
        # re-acquire it here.)
        import time

        time.sleep(0.05)
        assert not unload_done.is_set(), "unload should be blocked on wait()"

        # Release the inference slot — transcribe's finally block does
        # this in production.
        with engine._inference_cond:
            engine._active_inference -= 1
            engine._inference_cond.notify_all()
            unload_order.append("inference_done")

        # unload() should now complete.
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


# ── OI-13: transcribe() must balance the _active_inference counter ────


class TestQwenTranscribeBalancesActiveInference:
    """``transcribe`` must increment ``_active_inference`` on entry and
    decrement it on exit (including the exception path), so ``unload``
    doesn't wait forever on a stuck counter."""

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
        """If ``model.transcribe`` raises, the ``finally`` block must
        still decrement ``_active_inference`` so ``unload`` doesn't block
        forever on a stuck counter."""
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
        """While ``model.transcribe`` is running, ``_active_inference``
        must be 1 (so a concurrent ``unload`` would correctly wait)."""
        engine = _make_engine()
        mock_transcription = MagicMock()
        mock_transcription.text = "hello"
        engine._model = MagicMock()

        observed: list[int] = []

        def _spy_transcribe(*args, **kwargs):
            # Observed from the inference thread while the lock is
            # released (transcribe released _lock before calling us).
            observed.append(engine._active_inference)
            return [mock_transcription]

        engine._model.transcribe.side_effect = _spy_transcribe

        engine.transcribe(np.ones(16000, dtype=np.float32))

        assert observed == [1], (
            "OI-13: _active_inference must be 1 while model.transcribe() "
            "is running (incremented under _lock before the call, "
            f"decremented in finally). Got: {observed}"
        )


# ── OI-13: dead _inference_event must be gone ─────────────────────────


class TestQwenInferenceEventRemoved:
    """The dead ``_inference_event`` attribute (set/cleared but never
    read) must be removed from ``__init__``. Pre-fix it coexisted with
    the canonical ``_active_inference`` counter pattern as dead noise."""

    def test_init_does_not_create_inference_event(self):
        """``QwenEngine.__init__`` must NOT create ``_inference_event``.

        Pre-fix it was created (line 103) and set/cleared in
        ``transcribe`` (lines 394, 448) but NEVER read anywhere — pure
        dead noise. The canonical pattern is ``_active_inference`` +
        ``_inference_cond`` (mirroring ParakeetEngine).
        """
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
