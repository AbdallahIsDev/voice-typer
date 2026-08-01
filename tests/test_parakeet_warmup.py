"""Tests for ``ParakeetEngine._warm_up_model`` and its wiring into ``load()``.

The first CUDA ``model.generate()`` call after ``from_pretrained`` takes
2-5s longer than subsequent ones because the GPU kernels need to be
JIT-compiled and memory allocated. ``_warm_up_model`` runs a 0.5s
silence through the model at load time so the first real dictation is
fast.

Mirrors ``WhisperEngine._warm_up_model`` in
``voice_typer/server/transcription.py`` (lines ~649-680), adapted for
Parakeet's ``processor()`` + ``model.generate()`` API (the same call
shape as ``_transcribe_segment``).

Required coverage (per task spec):
  1. ``_warm_up_model()`` is called from ``load()`` when the effective
     device is ``cuda``.
  2. ``_warm_up_model()`` is NOT called when the effective device is
     ``cpu`` (CPU JIT cost is negligible).
  3. ``_warm_up_model()`` is NOT called when CUDA is requested but
     unavailable (effective_device falls back to ``cpu``).
  4. Warm-up failure (``model.generate()`` raises) does NOT cause
     ``load()`` to return False — warm-up is non-fatal.
  5. The warm-up audio is exactly
     ``np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)``.

The mocking pattern mirrors ``tests/test_parakeet_engine.py``: heavy
deps (``torch`` / ``transformers``) are mocked at the ``sys.modules``
level so the lazy ``_ensure_imports`` classmethod installs our mocks on
``ParakeetEngine._torch`` / ``._AutoModelForTDT`` / ``._AutoProcessor``.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE

# ─── Mock helpers ───────────────────────────────────────────────────────


def _mock_torch() -> MagicMock:
    """Build a MagicMock that quacks like the bits of torch we use.

    ``cuda.is_available()`` defaults to ``True`` so the CUDA path is
    exercised; tests that want the CPU-fallback path override the
    return value.  ``inference_mode()`` returns a context-manager
    MagicMock (``__enter__`` / ``__exit__`` are auto-supported by
    MagicMock).
    """
    mock = MagicMock(name="torch")
    mock.cuda.is_available.return_value = True
    mock.float16 = "fp16"
    mock.float32 = "fp32"
    return mock


def _mock_transformers() -> MagicMock:
    """Build a MagicMock exposing ``AutoModelForTDT`` + ``AutoProcessor``."""
    mock = MagicMock(name="transformers")
    mock.AutoModelForTDT = MagicMock()
    mock.AutoProcessor = MagicMock()
    return mock


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state():
    """Reset ``ParakeetEngine`` class-level state between tests.

    Mirrors the autouse fixture in ``tests/test_parakeet_engine.py``:
    ``_ensure_imports`` caches the imported modules on class attrs, so
    without this reset the mocks leak into the next test.
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


def _make_engine_with_mocks(device: str = "cuda", language: str = "en"):
    """Build a ParakeetEngine with mocked torch/transformers installed.

    Returns ``(engine, mock_torch, mock_transformers)`` so the test can
    configure ``from_pretrained`` / ``cuda.is_available`` etc.
    """
    mock_torch = _mock_torch()
    mock_transformers = _mock_transformers()
    with patch.dict(
        sys.modules,
        {"torch": mock_torch, "transformers": mock_transformers},
    ):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine(device=device, language=language)
        # Force the lazy import to fire now (inside the patch context).
        ParakeetEngine._ensure_imports()
    return engine, mock_torch, mock_transformers


def _wire_mock_model_and_processor(
    engine,
    *,
    generate_side_effect=None,
) -> tuple[MagicMock, MagicMock]:
    """Attach a mock ``_model`` + ``_processor`` to the engine.

    ``generate_side_effect``: if provided, set as ``model.generate``'s
    side_effect (e.g. to simulate a CUDA error during warm-up).

    Returns ``(mock_model, mock_processor)``.
    """
    mock_processor = MagicMock()
    # processor([audio], sampling_rate=..., return_tensors="pt") -> inputs
    mock_processor.return_value = MagicMock()
    mock_output = MagicMock()
    mock_output.sequences = [42]
    mock_processor.decode.return_value = ""  # warm-up output is discarded
    mock_model = MagicMock()
    if generate_side_effect is not None:
        mock_model.generate.side_effect = generate_side_effect
    else:
        mock_model.generate.return_value = mock_output
    mock_model.device = "cuda"
    mock_model.dtype = "fp16"
    engine._processor = mock_processor
    engine._model = mock_model
    return mock_model, mock_processor


# ─── Direct tests of _warm_up_model ─────────────────────────────────────


class TestParakeetWarmUpModelDirect:
    """``_warm_up_model`` directly invokes ``model.generate()`` with 0.5s
    silence when on CUDA, and no-ops on CPU / when the model is None."""

    def test_warmup_runs_generate_on_cuda(self):
        """On CUDA, warm-up calls ``model.generate()`` exactly once."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        mock_model, _ = _wire_mock_model_and_processor(engine)

        engine._warm_up_model()

        assert mock_model.generate.called, "Warm-up on CUDA must call model.generate() to prime kernels."
        assert mock_model.generate.call_count == 1, (
            f"Warm-up should call generate() exactly once, got {mock_model.generate.call_count}."
        )

    def test_warmup_skipped_on_cpu(self):
        """On CPU, warm-up is a no-op — JIT cost is negligible."""
        engine, _, _ = _make_engine_with_mocks(device="cpu")
        mock_model, mock_processor = _wire_mock_model_and_processor(engine)
        # Override device attrs to ensure no real GPU path is hit.
        mock_model.device = "cpu"
        mock_model.dtype = "fp32"

        engine._warm_up_model()

        mock_model.generate.assert_not_called()
        mock_processor.assert_not_called()

    def test_warmup_skipped_when_model_is_none(self):
        """Defensive no-op when ``_model`` is None (direct-call safety)."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        engine._model = None
        # Must not raise.
        engine._warm_up_model()

    def test_warmup_audio_is_half_second_silence(self):
        """The warm-up audio fed to ``processor()`` is exactly
        ``np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)``."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        _, mock_processor = _wire_mock_model_and_processor(engine)

        engine._warm_up_model()

        call_args = mock_processor.call_args
        assert call_args is not None, "Warm-up must call processor() with the silence audio."
        # processor([audio], sampling_rate=..., return_tensors="pt")
        # The first positional arg is a list containing the np array.
        first_arg = call_args.args[0]
        assert isinstance(first_arg, list), (
            f"Warm-up processor() must receive a list of audio arrays, got {type(first_arg)}."
        )
        assert len(first_arg) == 1, f"Warm-up processor() must receive exactly one audio array, got {len(first_arg)}."
        audio = first_arg[0]

        expected = np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
        assert isinstance(audio, np.ndarray), f"Warm-up audio must be a numpy.ndarray, got {type(audio)}."
        assert audio.dtype == np.float32, f"Warm-up audio dtype must be np.float32, got {audio.dtype}."
        assert audio.shape == expected.shape, (
            f"Warm-up audio shape {audio.shape} != expected {expected.shape} "
            f"(={int(WHISPER_SAMPLE_RATE * 0.5)} samples)."
        )
        np.testing.assert_array_equal(audio, expected)

    def test_warmup_audio_sample_rate_matches_whisper_constant(self):
        """Sanity: ``WHISPER_SAMPLE_RATE`` is 16000, so warm-up audio is
        8000 samples (0.5s).  Guards against a future constant rename
        silently breaking the warm-up duration."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        _, mock_processor = _wire_mock_model_and_processor(engine)

        engine._warm_up_model()

        audio = mock_processor.call_args.args[0][0]
        assert audio.shape == (8000,), f"0.5s @ 16kHz should be 8000 samples, got {audio.shape}."
        # sampling_rate kwarg passed to processor() must match too.
        assert mock_processor.call_args.kwargs.get("sampling_rate") == WHISPER_SAMPLE_RATE

    def test_warmup_uses_inference_mode_context(self):
        """Warm-up must wrap ``generate()`` in ``torch.inference_mode()`` —
        same contract as the production ``_transcribe_segment`` path
        (AB-11: skipping inference_mode roughly doubles activation-memory
        footprint on CUDA)."""
        engine, mock_torch, _ = _make_engine_with_mocks(device="cuda")
        _wire_mock_model_and_processor(engine)

        engine._warm_up_model()

        assert mock_torch.inference_mode.called, "Warm-up must wrap generate() in torch.inference_mode()."

    def test_warmup_calls_decode_to_force_full_pipeline(self):
        """Warm-up must invoke ``processor.decode()`` so the full
        processor → generate → decode pipeline is exercised (the
        decoded text itself is discarded)."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        _, mock_processor = _wire_mock_model_and_processor(engine)

        engine._warm_up_model()

        mock_processor.decode.assert_called_once()

    def test_warmup_failure_is_swallowed(self):
        """If ``model.generate()`` raises during warm-up, the exception
        is swallowed (logged at debug) and does NOT propagate to the
        caller."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        _wire_mock_model_and_processor(
            engine,
            generate_side_effect=RuntimeError("CUDA OOM during warmup"),
        )

        # Must NOT raise.
        engine._warm_up_model()

    def test_warmup_failure_from_processor_is_swallowed(self):
        """If ``processor()`` itself raises (e.g. bad audio shape), the
        exception is also swallowed — same non-fatal contract."""
        engine, _, _ = _make_engine_with_mocks(device="cuda")
        mock_processor = MagicMock()
        mock_processor.side_effect = RuntimeError("processor exploded")
        mock_model = MagicMock()
        mock_model.device = "cuda"
        mock_model.dtype = "fp16"
        engine._processor = mock_processor
        engine._model = mock_model

        # Must NOT raise.
        engine._warm_up_model()
        mock_model.generate.assert_not_called()


# ─── load() wiring tests ────────────────────────────────────────────────


class TestParakeetWarmUpWiredIntoLoad:
    """``load()`` must call ``_warm_up_model()`` on CUDA (after a
    successful ``from_pretrained``) and skip it on CPU / CPU-fallback.
    Warm-up failure must not cause ``load()`` to return False.
    """

    def test_load_calls_warmup_on_cuda(self):
        """Requirement #2: ``_warm_up_model()`` IS called when
        ``effective_device == "cuda"``."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cuda")
        mock_torch.cuda.is_available.return_value = True
        mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
        mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
            patch.object(type(engine), "_warm_up_model") as mock_warmup,
        ):
            result = engine.load()

        assert result is True
        mock_warmup.assert_called_once_with()

    def test_load_skips_warmup_on_cpu(self):
        """Requirement #3: ``_warm_up_model()`` is NOT called when
        ``effective_device == "cpu"`` (engine.device == "cpu")."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cpu")
        mock_torch.cuda.is_available.return_value = True  # available but engine asked for CPU
        mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
        mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
            patch.object(type(engine), "_warm_up_model") as mock_warmup,
        ):
            result = engine.load()

        assert result is True
        mock_warmup.assert_not_called()

    def test_load_skips_warmup_when_cuda_unavailable(self):
        """Requirement #3 (variant): when CUDA is requested but
        unavailable, ``effective_device`` falls back to ``cpu`` —
        warm-up must NOT run (it would crash without a GPU)."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cuda")
        mock_torch.cuda.is_available.return_value = False
        mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
        mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
            patch.object(type(engine), "_warm_up_model") as mock_warmup,
        ):
            result = engine.load()

        assert result is True
        mock_warmup.assert_not_called()

    def test_load_skips_warmup_when_force_cpu_due_to_low_disk(self):
        """Requirement #3 (variant): when ``_should_force_cpu`` returns
        True (low disk / pagefile), ``effective_device`` is downgraded
        to ``cpu`` — warm-up must NOT run."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cuda")
        mock_torch.cuda.is_available.return_value = True
        mock_transformers.AutoProcessor.from_pretrained.return_value = MagicMock()
        mock_transformers.AutoModelForTDT.from_pretrained.return_value = MagicMock()

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=True),
            patch.object(type(engine), "_warm_up_model") as mock_warmup,
        ):
            result = engine.load()

        assert result is True
        mock_warmup.assert_not_called()

    def test_load_returns_true_when_warmup_generate_raises(self):
        """Requirement #4: warm-up failure (``model.generate()`` raises)
        does NOT cause ``load()`` to return False.

        Exercises the production path: ``from_pretrained`` returns a
        mock model whose ``generate()`` raises; ``load()`` wires the
        real ``_warm_up_model`` (which catches the exception) and must
        still return True.
        """
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cuda")
        mock_torch.cuda.is_available.return_value = True

        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = ""
        mock_model = MagicMock()
        mock_model.generate.side_effect = RuntimeError("CUDA warmup OOM")
        mock_model.device = "cuda"
        mock_model.dtype = "fp16"
        mock_transformers.AutoProcessor.from_pretrained.return_value = mock_processor
        mock_transformers.AutoModelForTDT.from_pretrained.return_value = mock_model

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
        ):
            result = engine.load()

        assert result is True, (
            "load() must return True even when warm-up generate() raises — warm-up is non-fatal (Whisper parity)."
        )
        # And generate() must have actually been called (proving the
        # warm-up path was entered, not short-circuited).
        mock_model.generate.assert_called_once()

    def test_load_warmup_failure_does_not_clear_model(self):
        """Regression guard: when warm-up fails, the already-loaded
        ``_model`` / ``_processor`` must remain set so subsequent
        ``transcribe()`` calls still work (the warm-up is best-effort,
        not a load-stage gate)."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cuda")
        mock_torch.cuda.is_available.return_value = True

        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock()
        mock_processor.decode.return_value = ""
        mock_model = MagicMock()
        mock_model.generate.side_effect = RuntimeError("warmup CUDA error")
        mock_model.device = "cuda"
        mock_model.dtype = "fp16"
        mock_transformers.AutoProcessor.from_pretrained.return_value = mock_processor
        mock_transformers.AutoModelForTDT.from_pretrained.return_value = mock_model

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
        ):
            result = engine.load()

        assert result is True
        assert engine._model is mock_model, "Model must remain loaded after warm-up failure."
        assert engine._processor is mock_processor
        assert engine.is_loaded is True

    def test_load_warmup_called_after_from_pretrained_success(self):
        """Warm-up must be called AFTER ``from_pretrained`` succeeds —
        not before, and not in place of the success return.  Verified
        by attaching a side_effect to ``from_pretrained`` that records
        call order against ``_warm_up_model``."""
        engine, mock_torch, mock_transformers = _make_engine_with_mocks(device="cuda")
        mock_torch.cuda.is_available.return_value = True

        call_order: list[str] = []

        def _record_from_pretrained(*args, **kwargs):
            call_order.append("from_pretrained")
            return MagicMock()

        mock_transformers.AutoProcessor.from_pretrained.side_effect = _record_from_pretrained
        mock_transformers.AutoModelForTDT.from_pretrained.side_effect = _record_from_pretrained

        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
            patch.object(
                type(engine),
                "_warm_up_model",
                side_effect=lambda: call_order.append("warm_up_model"),
            ),
        ):
            result = engine.load()

        assert result is True
        # from_pretrained called twice (processor + model), then warm_up.
        assert call_order[-1] == "warm_up_model", f"Warm-up must be called AFTER from_pretrained. Order: {call_order}"
        assert call_order.count("from_pretrained") == 2
