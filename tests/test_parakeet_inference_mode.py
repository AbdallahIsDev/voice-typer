"""AB-11: ``torch.inference_mode()`` is entered during Parakeet + Qwen
inference.

Pre-AB-11, ``ParakeetEngine._transcribe_segment``,
``_transcribe_batch``, ``_transcribe_segment_unlocked``, and
``QwenEngine.transcribe`` / ``_transcribe_chunked`` all called
``model.generate()`` / ``model.transcribe()`` WITHOUT an
``inference_mode()`` context. PyTorch therefore built and retained the
autograd graph for every call — roughly DOUBLING activation-memory
footprint on CUDA and adding ~10-30 % inference latency from
gradient-tracking overhead. For a 5-minute dictation split into 13
chunks, the latency penalty was several seconds.

The fix wraps each ``generate()`` / ``model.transcribe()`` call in
``with torch.inference_mode():``. ``torch.inference_mode()`` is
preferred over ``torch.no_grad()`` (lower overhead, recursive — the
context propagates to all sub-operations inside ``model.generate()``'s
internal ``forward`` calls).

These tests verify that the ``inference_mode()`` context manager is
actually entered around the ``generate()`` / ``transcribe()`` call.
The approach: install a recording mock ``torch`` whose
``inference_mode()`` returns a context manager that records
``__enter__`` / ``__exit__`` events. The test then drives a real
transcription and asserts that (a) ``inference_mode()`` was entered
BEFORE ``generate()`` was called and (b) exited AFTER ``generate()``
returned.
"""

from __future__ import annotations

import sys
from contextlib import AbstractContextManager
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ─── Recording torch mock ───────────────────────────────────────────────


class _RecordingInferenceModeCtx(AbstractContextManager):
    """A context manager that records ``__enter__`` / ``__exit__`` events.

    The mock ``torch.inference_mode()`` returns one of these. The test
    inspects the recorded events to verify the call ordering:
    ``__enter__`` must precede ``model.generate()``, and ``__exit__``
    must follow it.
    """

    def __init__(self, recorder: _InferenceModeRecorder) -> None:
        self._recorder = recorder

    def __enter__(self):
        self._recorder.events.append("inference_mode_enter")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._recorder.events.append("inference_mode_exit")
        # Don't suppress exceptions.
        return None


class _InferenceModeRecorder:
    """Records the call ordering of ``inference_mode()`` and ``generate()``.

    The test mocks ``torch`` with this object so that
    ``torch.inference_mode()`` returns a recording context manager.
    The test then drives a real ``engine.transcribe()`` call and
    inspects ``recorder.events`` to verify the inference_mode context
    was entered around the ``generate()`` call.
    """

    def __init__(self) -> None:
        self.events: list[str] = []
        # Mimic the bits of the torch API the parakeet engine touches
        # at class-load / _ensure_imports time.
        self.cuda = MagicMock()
        self.cuda.is_available.return_value = False
        self.float16 = "fp16"
        self.float32 = "fp32"

    def inference_mode(self) -> _RecordingInferenceModeCtx:
        return _RecordingInferenceModeCtx(self)


def _make_recording_torch_module() -> tuple[MagicMock, _InferenceModeRecorder]:
    """Build a fake ``torch`` module whose ``inference_mode()`` records events.

    Returns ``(mock_torch_module, recorder)``. The mock module quacks
    like torch for the parakeet engine's purposes (cuda, float16,
    float32, inference_mode). Other attribute accesses fall through to
    MagicMock auto-attrs.
    """
    recorder = _InferenceModeRecorder()
    mock_torch = MagicMock(name="torch")
    mock_torch.cuda = recorder.cuda
    mock_torch.float16 = recorder.float16
    mock_torch.float32 = recorder.float32
    mock_torch.inference_mode = recorder.inference_mode
    return mock_torch, recorder


# ─── ParakeetEngine test fixtures ───────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state():
    """Reset ``ParakeetEngine`` class-level state between tests.

    Mirrors the autouse fixture in ``tests/test_parakeet_engine.py`` —
    ``_ensure_imports`` caches ``_imports_loaded`` and the imported
    modules on the class; without this reset, mocks from a prior test
    leak into the next.
    """
    from voice_typer.server.parakeet_engine import ParakeetEngine

    saved = (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._torch,
        ParakeetEngine._AutoModelForTDT,
        ParakeetEngine._AutoProcessor,
        ParakeetEngine._hf_home_set,
    )
    ParakeetEngine._imports_loaded = False
    ParakeetEngine._torch = None
    ParakeetEngine._AutoModelForTDT = None
    ParakeetEngine._AutoProcessor = None
    ParakeetEngine._hf_home_set = False
    yield
    (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._torch,
        ParakeetEngine._AutoModelForTDT,
        ParakeetEngine._AutoProcessor,
        ParakeetEngine._hf_home_set,
    ) = saved


def _make_parakeet_engine_with_recording_torch(device: str = "cpu"):
    """Build a ParakeetEngine whose ``self._torch`` is a recording mock.

    Forces ``_ensure_imports()`` to install the recording torch via
    ``patch.dict(sys.modules, ...)``. Returns
    ``(engine, recorder)``.

    The recorder's ``events`` list lets the test assert that
    ``inference_mode()`` was entered around the ``generate()`` call.
    """
    from voice_typer.server.parakeet_engine import ParakeetEngine

    mock_torch, recorder = _make_recording_torch_module()
    mock_transformers = MagicMock()
    mock_transformers.AutoModelForTDT = MagicMock()
    mock_transformers.AutoProcessor = MagicMock()

    with patch.dict(
        sys.modules,
        {"torch": mock_torch, "transformers": mock_transformers},
    ):
        engine = ParakeetEngine(device=device, language="en")
        ParakeetEngine._ensure_imports()
    return engine, recorder


def _wire_parakeet_mocks(engine, decode_text: str = "hello world"):
    """Wire up mock processor + model on the engine.

    The mock model's ``generate()`` records a ``generate_call`` event
    in the recorder so the test can assert call ordering vs
    ``inference_mode_enter`` / ``inference_mode_exit``.

    Returns ``(mock_processor, mock_model)``.
    """
    mock_processor = MagicMock()
    mock_processor.return_value = MagicMock()
    mock_output = MagicMock()
    mock_output.sequences = [42]
    mock_processor.decode.return_value = decode_text
    mock_model = MagicMock()
    mock_model.generate.return_value = mock_output
    mock_model.device = "cpu"
    mock_model.dtype = "float32"
    engine._processor = mock_processor
    engine._model = mock_model
    return mock_processor, mock_model


# ─── AB-11: Parakeet inference_mode tests ───────────────────────────────


class TestParakeetInferenceModeEntered:
    """AB-11: each ``generate()`` call site in ``parakeet_engine.py``
    MUST be wrapped in ``with torch.inference_mode():``.
    """

    def test_transcribe_segment_enters_inference_mode(self):
        """``_transcribe_segment`` wraps ``generate()`` in
        ``inference_mode()`` — the context is entered BEFORE
        ``generate()`` is called and exited AFTER.
        """
        engine, recorder = _make_parakeet_engine_with_recording_torch(device="cpu")
        mock_processor, mock_model = _wire_parakeet_mocks(engine, decode_text="hello world")

        # Patch the mock model's generate side effect to record the
        # event AT CALL TIME — so we can assert the ordering.
        def _generate_recorder(*args, **kwargs):
            recorder.events.append("generate_call")
            return mock_model.generate.return_value

        mock_model.generate.side_effect = _generate_recorder

        audio = np.ones(16000, dtype=np.float32)  # 1s of audio
        result = engine.transcribe(audio)
        assert result == "hello world"

        # Verify the inference_mode context was entered around generate.
        assert "inference_mode_enter" in recorder.events, (
            f"AB-11: _transcribe_segment must call torch.inference_mode() around generate(). Events: {recorder.events}"
        )
        assert "inference_mode_exit" in recorder.events, (
            "AB-11: _transcribe_segment must exit the inference_mode() "
            f"context after generate(). Events: {recorder.events}"
        )
        # Verify the ordering: enter → generate → exit.
        try:
            enter_idx = recorder.events.index("inference_mode_enter")
            gen_idx = recorder.events.index("generate_call")
            exit_idx = recorder.events.index("inference_mode_exit")
        except ValueError as exc:
            pytest.fail(f"Missing expected event: {exc}. Events: {recorder.events}")
        assert enter_idx < gen_idx < exit_idx, (
            f"AB-11: inference_mode() must be entered BEFORE generate() and exited AFTER. Ordering: {recorder.events}"
        )

    def test_transcribe_segment_unlocked_enters_inference_mode(self):
        """``_transcribe_segment_unlocked`` (the CPU-fallback path)
        wraps ``generate()`` in ``inference_mode()``.
        """
        engine, recorder = _make_parakeet_engine_with_recording_torch(device="cpu")
        mock_processor, mock_model = _wire_parakeet_mocks(engine, decode_text="cpu fallback result")

        def _generate_recorder(*args, **kwargs):
            recorder.events.append("generate_call")
            return mock_model.generate.return_value

        mock_model.generate.side_effect = _generate_recorder

        audio = np.ones(16000, dtype=np.float32)
        result = engine._transcribe_segment_unlocked(audio)
        assert result == "cpu fallback result"

        assert "inference_mode_enter" in recorder.events
        assert "inference_mode_exit" in recorder.events
        try:
            enter_idx = recorder.events.index("inference_mode_enter")
            gen_idx = recorder.events.index("generate_call")
            exit_idx = recorder.events.index("inference_mode_exit")
        except ValueError as exc:
            pytest.fail(f"Missing expected event: {exc}. Events: {recorder.events}")
        assert enter_idx < gen_idx < exit_idx, (
            f"AB-11: _transcribe_segment_unlocked must wrap generate() in inference_mode(). Ordering: {recorder.events}"
        )

    def test_transcribe_batch_enters_inference_mode(self):
        """``_transcribe_batch`` (the batched-chunk path) wraps
        ``generate()`` in ``inference_mode()``.
        """
        engine, recorder = _make_parakeet_engine_with_recording_torch(device="cpu")
        mock_processor, mock_model = _wire_parakeet_mocks(engine, decode_text="batch result")

        def _generate_recorder(*args, **kwargs):
            recorder.events.append("generate_call")
            return mock_model.generate.return_value

        mock_model.generate.side_effect = _generate_recorder

        # Two chunks in the batch.
        chunks = [
            np.ones(16000, dtype=np.float32),
            np.ones(16000, dtype=np.float32),
        ]
        results = engine._transcribe_batch(chunks)
        assert isinstance(results, list)
        assert len(results) == 2

        # generate() is called ONCE per batch (batched together).
        assert "inference_mode_enter" in recorder.events
        assert "inference_mode_exit" in recorder.events
        # Exactly one enter / one exit / one generate_call (batched).
        assert recorder.events.count("inference_mode_enter") == 1, (
            "AB-11: _transcribe_batch must enter inference_mode() exactly "
            f"once per generate() call. Events: {recorder.events}"
        )
        assert recorder.events.count("inference_mode_exit") == 1, (
            "AB-11: _transcribe_batch must exit inference_mode() exactly "
            f"once per generate() call. Events: {recorder.events}"
        )
        try:
            enter_idx = recorder.events.index("inference_mode_enter")
            gen_idx = recorder.events.index("generate_call")
            exit_idx = recorder.events.index("inference_mode_exit")
        except ValueError as exc:
            pytest.fail(f"Missing expected event: {exc}. Events: {recorder.events}")
        assert enter_idx < gen_idx < exit_idx

    def test_long_audio_chunks_each_enter_inference_mode(self):
        """AB-11: for a long audio that splits into multiple chunks,
        ``inference_mode()`` is entered once PER chunk (each
        ``_transcribe_segment`` call wraps its own ``generate()``).
        """
        engine, recorder = _make_parakeet_engine_with_recording_torch(device="cpu")
        mock_processor, mock_model = _wire_parakeet_mocks(engine, decode_text="chunk")

        def _generate_recorder(*args, **kwargs):
            recorder.events.append("generate_call")
            return mock_model.generate.return_value

        mock_model.generate.side_effect = _generate_recorder

        # 30s of audio → 2 chunks.
        audio = np.ones(int(30 * 16000), dtype=np.float32)
        engine.transcribe(audio)

        # Two chunks → two generate() calls → two inference_mode enters.
        enter_count = recorder.events.count("inference_mode_enter")
        exit_count = recorder.events.count("inference_mode_exit")
        gen_count = recorder.events.count("generate_call")
        assert enter_count == gen_count, (
            "AB-11: each generate() call must be wrapped in its own "
            f"inference_mode() context. enters={enter_count}, "
            f"generate_calls={gen_count}. Events: {recorder.events}"
        )
        assert exit_count == gen_count, (
            "AB-11: each inference_mode() context must be exited after "
            f"its generate() call. exits={exit_count}, generate_calls="
            f"{gen_count}. Events: {recorder.events}"
        )


# ─── AB-11: Qwen inference_mode tests ───────────────────────────────────


class TestQwenInferenceModeEntered:
    """AB-11: each ``model.transcribe()`` call site in ``qwen_engine.py``
    MUST be wrapped in ``with torch.inference_mode():``.
    """

    def _make_qwen_engine_with_recording_torch(self, monkeypatch):
        """Build a QwenEngine + install a recording torch mock.

        ``QwenEngine._inference_mode_ctx`` does a fresh
        ``import torch`` on every call, so we mock ``torch`` in
        ``sys.modules`` for the duration of the transcribe call.
        """
        mock_torch, recorder = _make_recording_torch_module()
        # Make the mock look "installed" to ``import torch``.
        monkeypatch.setitem(sys.modules, "torch", mock_torch)

        from voice_typer.server.qwen_engine import QwenEngine

        engine = QwenEngine(model_path="/fake/qwen/model", device="cpu", language="en")
        # Wire up a mock model whose transcribe() records events.
        mock_model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "hello from qwen"

        def _transcribe_recorder(*args, **kwargs):
            recorder.events.append("model_transcribe_call")
            return [mock_transcription]

        mock_model.transcribe.side_effect = _transcribe_recorder
        engine._model = mock_model
        return engine, recorder, mock_model

    def test_qwen_transcribe_enters_inference_mode(self, monkeypatch):
        """``QwenEngine.transcribe`` (non-chunked path) wraps
        ``model.transcribe()`` in ``inference_mode()``.
        """
        engine, recorder, _ = self._make_qwen_engine_with_recording_torch(monkeypatch)

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "hello from qwen"

        assert "inference_mode_enter" in recorder.events, (
            "AB-11: QwenEngine.transcribe must wrap model.transcribe() in "
            f"torch.inference_mode(). Events: {recorder.events}"
        )
        assert "inference_mode_exit" in recorder.events
        try:
            enter_idx = recorder.events.index("inference_mode_enter")
            t_idx = recorder.events.index("model_transcribe_call")
            exit_idx = recorder.events.index("inference_mode_exit")
        except ValueError as exc:
            pytest.fail(f"Missing expected event: {exc}. Events: {recorder.events}")
        assert enter_idx < t_idx < exit_idx, (
            "AB-11: inference_mode() must be entered BEFORE "
            f"model.transcribe() and exited AFTER. Events: {recorder.events}"
        )

    def test_qwen_transcribe_chunked_enters_inference_mode(self, monkeypatch):
        """``QwenEngine._transcribe_chunked`` wraps each per-chunk
        ``model.transcribe()`` in ``inference_mode()``.
        """
        engine, recorder, mock_model = self._make_qwen_engine_with_recording_torch(monkeypatch)

        # Long audio → multiple chunks. Each chunk's transcribe call
        # must be wrapped in inference_mode.
        # _QWEN_CHUNK_SECONDS is 30s; use 65s → 3 chunks.
        n = int(65 * 16000)
        t = np.linspace(0, 65, n, endpoint=False, dtype=np.float32)
        audio = (0.1 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)

        # Each chunk returns a distinct non-overlapping text so dedup
        # doesn't strip them.
        texts = ["alpha beta", "gamma delta", "epsilon zeta"]
        mock_transcriptions = []
        for text in texts:
            mt = MagicMock()
            mt.text = text
            mock_transcriptions.append(mt)

        # Use a single callable as side_effect that records the event
        # and returns the next mock transcription. ``side_effect`` as a
        # list would RETURN the items (not call them), which breaks
        # for callable items.
        call_state = {"n": 0}

        def _transcribe_recorder(*args, **kwargs):
            idx = call_state["n"]
            call_state["n"] += 1
            recorder.events.append("model_transcribe_call")
            return [mock_transcriptions[idx]]

        mock_model.transcribe.side_effect = _transcribe_recorder

        result = engine.transcribe(audio)
        # All three chunks' text should appear in the result.
        assert "alpha beta" in result
        assert "gamma delta" in result
        assert "epsilon zeta" in result

        enter_count = recorder.events.count("inference_mode_enter")
        exit_count = recorder.events.count("inference_mode_exit")
        t_count = recorder.events.count("model_transcribe_call")
        assert t_count == 3, f"Expected 3 chunks → 3 model.transcribe() calls, got {t_count}."
        assert enter_count == t_count, (
            "AB-11: each model.transcribe() in _transcribe_chunked must "
            f"be wrapped in its own inference_mode(). enters={enter_count}, "
            f"transcribe_calls={t_count}."
        )
        assert exit_count == t_count


# ─── AB-11: graceful fallback when torch is not installed ──────────────


class TestInferenceModeFallback:
    """AB-11: when ``torch`` is not installed (test stubs that bypass
    ``_ensure_imports``), the ``_inference_mode_ctx`` helper returns a
    ``contextlib.nullcontext`` so the call site is transparent — the
    mocked model never actually uses autograd, so skipping
    ``inference_mode()`` is safe.
    """

    def test_parakeet_inference_mode_ctx_returns_nullcontext_when_torch_missing(self, monkeypatch):
        """If ``self._torch`` is None AND ``import torch`` fails,
        ``_inference_mode_ctx`` returns a ``contextlib.nullcontext``.
        """
        import contextlib

        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine(device="cpu")
        # Force torch import to fail.
        monkeypatch.setitem(sys.modules, "torch", None)
        ctx = engine._inference_mode_ctx()
        assert isinstance(ctx, contextlib.nullcontext), (
            "AB-11: when torch is not importable, _inference_mode_ctx "
            f"must return a contextlib.nullcontext (got {type(ctx)})."
        )

    def test_qwen_inference_mode_ctx_returns_nullcontext_when_torch_missing(self, monkeypatch):
        """If ``import torch`` fails, ``QwenEngine._inference_mode_ctx``
        returns a ``contextlib.nullcontext``.
        """
        import contextlib

        from voice_typer.server.qwen_engine import QwenEngine

        monkeypatch.setitem(sys.modules, "torch", None)
        ctx = QwenEngine._inference_mode_ctx()
        assert isinstance(ctx, contextlib.nullcontext), (
            "AB-11: when torch is not importable, "
            "QwenEngine._inference_mode_ctx must return a "
            f"contextlib.nullcontext (got {type(ctx)})."
        )

    def test_parakeet_transcribe_still_works_when_torch_not_importable(self, monkeypatch):
        """AB-11 regression guard: the inference_mode wrap must NOT
        break the existing test stubs that bypass ``_ensure_imports``
        and set ``engine._model`` / ``engine._processor`` directly
        (e.g. ``tests/test_parakeet_engine.py::TestParakeetEngineTranscribe``).

        Concretely: ``self._torch`` is None, ``import torch`` fails,
        so ``_inference_mode_ctx`` returns ``nullcontext``. The mock
        model's ``generate()`` is still called inside the (null)
        context.
        """
        # Force torch import to fail.
        monkeypatch.setitem(sys.modules, "torch", None)
        # Also clear cached _torch on ParakeetEngine.
        from voice_typer.server.parakeet_engine import ParakeetEngine

        ParakeetEngine._torch = None
        ParakeetEngine._imports_loaded = False

        engine = ParakeetEngine(device="cpu")
        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = "stub result"
        mock_model = MagicMock()
        mock_model.generate.return_value = MagicMock(sequences=[42])
        mock_model.device = "cpu"
        mock_model.dtype = "float32"
        engine._processor = mock_processor
        engine._model = mock_model

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "stub result"
        mock_model.generate.assert_called_once()
