"""SU-10: QwenEngine model warm-up tests.

The Qwen3-ASR backend previously had no warm-up step after
``from_pretrained()``.  The first real dictation therefore paid the
2-5 s CUDA JIT / memory-allocation cost, manifesting as a noticeable
lag on the very first transcription after model load.

These tests verify that ``QwenEngine.load()`` calls the new
``_warm_up_model()`` helper when CUDA is in use, skips it on CPU, and
that a warm-up failure is non-fatal (load() still returns True).

All model / torch calls are mocked — no real GPU or model weights
required.  Mirrors the mocking pattern in
``tests/test_qwen_engine_cuda.py``.
"""

import json as _json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE


def _make_engine(model_path: str = "/fake/qwen/model", **kwargs):
    from voice_typer.server.qwen_engine import QwenEngine

    return QwenEngine(model_path=model_path, **kwargs)


def _make_model_dir(tmp_path) -> str:
    """Create a minimal Qwen model dir with a valid ``config.json``.

    Mirrors ``tests/test_qwen_engine_cuda.py::_make_model_dir`` so
    ``load()``'s SEC-audit-007 directory validation + config.json
    read pass through to the ``qwen_asr`` import block.
    """
    model_dir = tmp_path / "qwen_model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(_json.dumps({"arch": "qwen3"}))
    return str(model_dir)


def _make_mock_torch(cuda_available: bool) -> MagicMock:
    """Build a fake ``torch`` module with a sentinel ``inference_mode`` ctx.

    ``torch.inference_mode`` is exposed as a MagicMock so it can be
    used as a context manager (``with torch.inference_mode(): ...``).
    A separate sentinel ``cuda.is_available`` controls the device
    resolution in ``QwenEngine._resolve_device``.
    """
    mock_torch = MagicMock(name="torch")
    mock_torch.cuda.is_available.return_value = cuda_available
    mock_torch.float16 = "FLOAT16_SENTINEL"
    # ``torch.inference_mode`` must behave as a context manager.
    mock_torch.inference_mode.return_value.__enter__ = MagicMock(return_value=None)
    mock_torch.inference_mode.return_value.__exit__ = MagicMock(return_value=False)
    return mock_torch


class TestWarmUpMethodExists:
    """Sanity check: the helper exists on the class."""

    def test_warm_up_method_exists(self):
        from voice_typer.server.qwen_engine import QwenEngine

        assert hasattr(QwenEngine, "_warm_up_model")


class TestWarmUpDirectCalls:
    """Call ``_warm_up_model`` directly with mocked state.

    These tests bypass ``load()`` so they isolate the warm-up
    behaviour from the qwen_asr import / from_pretrained flow.
    """

    def test_warm_up_no_model_is_noop(self):
        """Warm-up is a no-op when no model is loaded."""
        engine = _make_engine(device="cuda")
        engine._model = None
        # Must NOT raise.
        engine._warm_up_model()

    def test_warm_up_cpu_device_is_noop(self):
        """Warm-up is skipped on CPU — no transcribe() call."""
        engine = _make_engine(device="cpu")
        mock_model = MagicMock(name="qwen_model")
        engine._model = mock_model
        engine._warm_up_model()
        mock_model.transcribe.assert_not_called()

    def test_warm_up_cuda_calls_transcribe_with_half_second_silence(self):
        """On CUDA, warm-up must call transcribe with 0.5 s of silence.

        Asserts the audio argument is exactly
        ``np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)``
        — the contract from the Whisper warm-up pattern
        (transcription.py:649-680) adapted to Qwen's
        ``(audio, sample_rate)`` tuple API.
        """
        engine = _make_engine(device="cuda", language="en")
        mock_model = MagicMock(name="qwen_model")
        mock_result = MagicMock(name="asr_result")
        mock_result.text = ""
        mock_model.transcribe.return_value = [mock_result]
        engine._model = mock_model

        # ``_inference_mode_ctx`` lazily imports torch; inject a fake
        # so the context manager is a real no-op (avoids a hard torch
        # dependency in the test env).
        mock_torch = _make_mock_torch(cuda_available=True)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            engine._warm_up_model()

        mock_model.transcribe.assert_called_once()
        call_args, call_kwargs = mock_model.transcribe.call_args
        # First positional arg is the (audio, sample_rate) tuple.
        assert len(call_args) == 1
        audio_arg, sample_rate_arg = call_args[0]
        expected_audio = np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
        np.testing.assert_array_equal(audio_arg, expected_audio)
        assert audio_arg.dtype == np.float32
        assert sample_rate_arg == WHISPER_SAMPLE_RATE
        assert call_kwargs.get("language") == "en"

    def test_warm_up_failure_is_non_critical(self):
        """If ``model.transcribe`` raises, ``_warm_up_model`` swallows it.

        Warm-up MUST NOT raise — a failure here would propagate out
        of ``load()`` and abort model loading, breaking the
        non-fatal contract.
        """
        engine = _make_engine(device="cuda")
        mock_model = MagicMock(name="qwen_model")
        mock_model.transcribe.side_effect = RuntimeError("GPU kernel panic")
        engine._model = mock_model

        mock_torch = _make_mock_torch(cuda_available=True)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            # Must NOT raise.
            engine._warm_up_model()

        # transcribe was attempted (and raised, but the exception
        # was logged at debug level and swallowed).
        mock_model.transcribe.assert_called_once()


class TestLoadTriggersWarmUp:
    """``load()`` must wire the warm-up call after ``from_pretrained``.

    Mirrors the ``tests/test_qwen_engine_cuda.py::TestQwenLoadMovesModelToCuda``
    mocking pattern: a fake ``qwen_asr`` module + fake ``torch`` are
    injected via ``sys.modules`` patching so ``load()`` runs its full
    happy-path code without touching real model weights or GPUs.
    """

    def test_load_cuda_calls_warm_up(self, tmp_path):
        """When ``effective_device == "cuda"``, ``_warm_up_model`` is called."""
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="cuda")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
            patch.object(
                type(engine),
                "_warm_up_model",
                wraps=engine._warm_up_model,
            ) as mock_warmup,
        ):
            result = engine.load()

        assert result is True
        assert engine.device == "cuda"
        mock_warmup.assert_called_once_with()

    def test_load_auto_resolves_cuda_calls_warm_up(self, tmp_path):
        """``device="auto"`` resolving to CUDA also triggers warm-up."""
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="auto")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
            patch.object(
                type(engine),
                "_warm_up_model",
                wraps=engine._warm_up_model,
            ) as mock_warmup,
        ):
            result = engine.load()

        assert result is True
        assert engine.device == "cuda"
        mock_warmup.assert_called_once_with()

    def test_load_cpu_does_not_call_warm_up(self, tmp_path):
        """When ``effective_device == "cpu"``, ``_warm_up_model`` is NOT called."""
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="cpu")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        # Even if CUDA is available, explicit CPU must skip warm-up
        # (the gate is ``effective_device == "cuda"``, not the torch probe).
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
            patch.object(
                type(engine),
                "_warm_up_model",
            ) as mock_warmup,
        ):
            result = engine.load()

        assert result is True
        assert engine.device == "cpu"
        mock_warmup.assert_not_called()

    def test_load_auto_falls_back_to_cpu_skips_warm_up(self, tmp_path):
        """``device="auto"`` resolving to CPU must also skip warm-up."""
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="auto")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=False)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
            patch.object(
                type(engine),
                "_warm_up_model",
            ) as mock_warmup,
        ):
            result = engine.load()

        assert result is True
        assert engine.device == "cpu"
        mock_warmup.assert_not_called()

    def test_load_warmup_failure_does_not_abort_load(self, tmp_path):
        """Realistic warm-up failure: ``model.transcribe`` raises inside
        the helper, but the helper swallows it and ``load()`` returns True.

        This is the SU-10 non-fatal contract: a CUDA error during the
        silence-tensor warm-up inference is logged at debug level and
        the model is still considered loaded.  ``load()`` MUST return
        True so the user can still dictate — only the first
        transcription pays the JIT cost.
        """
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="cuda")

        mock_model = MagicMock(name="qwen_model")
        # Warm-up transcribe call raises — simulates a CUDA kernel
        # compile failure on the silence tensor (rare but possible
        # on driver-mismatch systems).
        mock_model.transcribe.side_effect = RuntimeError(
            "CUDA error: no kernel image is available for execution on the device"
        )
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            result = engine.load()

        assert result is True, "Warm-up failure (model.transcribe raised) must NOT abort load()"
        assert engine.device == "cuda"
        # Model is still considered loaded — warm-up is non-fatal.
        assert engine.is_loaded is True
        # The warm-up transcribe call was attempted exactly once.
        mock_model.transcribe.assert_called_once()


class TestWarmUpAudioContract:
    """Verify the exact warm-up audio tensor matches the contract."""

    def test_warm_up_audio_is_half_second_silence_at_whisper_sample_rate(self, tmp_path):
        """End-to-end: ``load()`` on CUDA must invoke ``model.transcribe``
        with ``np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)``
        as the audio portion of the ``(audio, sample_rate)`` tuple.

        This is the SU-10 contract: 0.5 s of silence at the Whisper
        sample rate (16 kHz) — identical to the Whisper warm-up
        pattern so both backends prime the same kernel paths.
        """
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="cuda")

        mock_model = MagicMock(name="qwen_model")
        mock_result = MagicMock(name="asr_result")
        mock_result.text = ""
        mock_model.transcribe.return_value = [mock_result]
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            result = engine.load()

        assert result is True
        mock_model.transcribe.assert_called_once()
        call_args, call_kwargs = mock_model.transcribe.call_args

        # Qwen3-ASR transcribe() takes (audio, sample_rate) as the
        # first positional arg (a tuple) plus a ``language`` kwarg.
        assert len(call_args) == 1, "transcribe() must receive exactly one positional arg"
        audio_tuple = call_args[0]
        assert isinstance(audio_tuple, tuple)
        assert len(audio_tuple) == 2
        audio_arg, sample_rate_arg = audio_tuple

        expected_audio = np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
        np.testing.assert_array_equal(audio_arg, expected_audio)
        assert audio_arg.dtype == np.float32
        assert audio_arg.shape == (int(WHISPER_SAMPLE_RATE * 0.5),)
        assert sample_rate_arg == WHISPER_SAMPLE_RATE
        assert call_kwargs.get("language") == engine.language


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
