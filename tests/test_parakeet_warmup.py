"""Warmup-concept regression guard for ``ParakeetEngine``.

Pre-ONNX-migration, ``ParakeetEngine`` had a ``_warm_up_model()`` method
that ran a 0.5s silence through ``model.generate()`` immediately after
``from_pretrained`` so the first CUDA ``generate()`` call (which JIT-
compiles + allocates GPU kernels for 2-5s) happened at LOAD time rather
than on the user's first dictation. The mock-torch / mock-transformers
tests in this file (now removed) pinned that contract.

The ONNX migration (PLAN_ONNX_INTEGRATION.md §3, Option B-1) removed
``_warm_up_model()`` entirely. The ONNX Runtime + ``onnx_asr.load_model``
backend:

1. Has no separate processor / model split — the onnx-asr adapter
   bundles the tokenizer + ONNX session
   the tokenizer + ONNX session, so the warmup-shaped
   ``processor([...])`` → ``model.generate()`` → ``processor.decode()``
   pipeline no longer exists.
2. Has no torch autograd graph to prime — ORT sessions are stateless
   inference engines; the first ``session.run()`` call (which happens
   lazily inside the first ``recognize()``) IS the kernel JIT. There is
   no benefit to a separate warmup pass: the first real dictation pays
   the JIT cost regardless.

This file therefore asserts the *absence* of the warmup contract:

1. ``ParakeetEngine`` has no ``_warm_up_model`` attribute (regression
   guard against accidental re-introduction).
2. ``load()`` does NOT call ``model.recognize()`` — the model is
   constructed by ``onnx_asr.load_model(...)`` and immediately returned; the
   first ``recognize()`` happens on the first ``transcribe()``.

If a future change re-introduces a warmup pass (e.g. an ORT-session-
preheat step that runs a dummy input through the encoder), update these
tests to pin the new contract.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.parakeet_engine import ParakeetEngine  # noqa: E402

# ─── Helpers ────────────────────────────────────────────────────────────


def _mock_onnx_asr_module() -> MagicMock:
    """Build a MagicMock that quacks like the bits of ``onnx_asr`` we use.

    ``load_model(...)`` returns a fresh MagicMock per call. Tests that
    want to assert on ``recognize`` call counts use the returned mock's
    ``load_model.return_value.recognize`` (or ``load_model.side_effect``).
    """
    mock = MagicMock(name="mock_onnx_asr")
    mock.__version__ = "0.12.0-test"
    mock.load_model.side_effect = lambda *args, **kwargs: MagicMock(name="mock_onnx_asr_model")
    return mock


def _mock_onnxruntime_module() -> MagicMock:
    """Build a MagicMock that quacks like the bits of ``onnxruntime`` we use."""
    mock = MagicMock(name="mock_onnxruntime")
    mock.__version__ = "1.20.0-test"
    mock.get_available_providers.return_value = ["CPUExecutionProvider"]
    return mock


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state():
    """Reset ``ParakeetEngine`` class-level state between tests.

    ``_ensure_imports`` caches ``_imports_loaded`` + the imported
    modules on the class. Without this reset, a test that runs
    ``_ensure_imports`` with mocked modules leaks the mocks into the
    next test.
    """
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


def _make_engine_with_mocks(device: str = "cpu"):
    """Build a ParakeetEngine and force mocked onnx_asr/onnxruntime imports.

    Returns ``(engine, mock_onnx_asr, mock_onnxruntime)``.
    """
    mock_onnx_asr = _mock_onnx_asr_module()
    mock_onnxruntime = _mock_onnxruntime_module()
    with patch.dict(
        sys.modules,
        {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
    ):
        engine = ParakeetEngine(device=device, language="en")
        ParakeetEngine._ensure_imports()
    return engine, mock_onnx_asr, mock_onnxruntime


# ─── Tests ──────────────────────────────────────────────────────────────


class TestParakeetOnnxNoWarmup:
    """The ONNX migration removed the warmup pass — assert its absence."""

    def test_engine_has_no_warm_up_model_attribute(self):
        """``ParakeetEngine`` must NOT have a ``_warm_up_model`` attribute.

        The pre-migration torch/transformers backend ran a 0.5s silence
        through ``model.generate()`` at load time to prime CUDA kernels.
        The ONNX backend has no such pass — the onnx-asr adapter is a
        stateless inference engine, and the first ``session.run()``
        (inside the first ``recognize()``) IS the kernel JIT. Re-introducing
        ``_warm_up_model`` would be a regression (dead code, no observable
        benefit on ORT).
        """
        engine = _make_engine_with_mocks(device="cpu")[0]
        assert not hasattr(engine, "_warm_up_model"), (
            "ParakeetEngine must NOT have a _warm_up_model method — the ONNX "
            "migration removed warmup (no torch generate() to prime, no "
            "separate processor + model split). Re-introducing it would be "
            "dead code on the ORT backend."
        )
        assert not hasattr(ParakeetEngine, "_warm_up_model"), (
            "ParakeetEngine class must NOT have a _warm_up_model method either."
        )

    def test_load_does_not_call_model_recognize(self):
        """``load()`` must NOT call ``model.recognize()`` — the ONNX
        backend has no warmup pass. The first ``recognize()`` happens
        on the first ``transcribe()`` (the user's first dictation)."""
        engine, mock_onnx_asr, _ = _make_engine_with_mocks(device="cpu")
        with patch.object(type(engine), "_is_cached", return_value=True):
            engine.load()

        # load_model(...) was called once during load (constructing the session).
        mock_onnx_asr.load_model.assert_called_once()
        # The constructed model's recognize() must NOT have been called.
        mock_model = engine._model
        assert mock_model is not None, "load() must construct self._model"
        mock_model.recognize.assert_not_called(), (
            "load() must NOT call model.recognize() — the ONNX backend has "
            "no warmup pass. The first recognize() happens on the first "
            "transcribe() (the user's first dictation)."
        )

    def test_engine_has_no_torch_class_attribute(self):
        """``ParakeetEngine`` must NOT have a ``_torch`` class attribute.

        The ONNX migration removed all torch references. The pre-migration
        ``_torch``, ``_AutoModelForTDT``, ``_AutoProcessor``, ``_hf_home_set``
        class attributes (cached by ``_ensure_imports``) are gone — the
        ONNX backend uses ``_onnx_asr`` and ``_ort`` instead. This guard
        prevents an accidental revert.
        """
        for attr in ("_torch", "_AutoModelForTDT", "_AutoProcessor", "_hf_home_set"):
            assert not hasattr(ParakeetEngine, attr), (
                f"ParakeetEngine must NOT have a {attr!r} class attribute — "
                f"the ONNX migration removed all torch/transformers references. "
                f"Re-introducing it would revert the ONNX migration."
            )
