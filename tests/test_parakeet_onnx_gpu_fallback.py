"""GPU→CPU fallback test for the ONNX Parakeet engine.

Mocks a CUDA error during ``model.recognize()`` and verifies:

1. The engine UNLOADS the GPU session and RECREATES it with
   ``CPUExecutionProvider`` only (PLAN_ONNX_INTEGRATION.md §3.4 —
   session recreation, NOT torch's ``.to("cpu")``).
2. The ``parakeet_cpu_fallback`` event is published to ``event_bus``.
3. The ``notification`` event is published (user-facing toast).
4. The notification is ONE-TIME per loaded session — a second
   fallback in the same session does NOT re-notify.
5. ``self.device`` is mutated to ``"cpu"`` (the session is now CPU-
   bound; the next ``transcribe_with_fallback`` call uses the CPU path
   directly without re-attempting CUDA).

The tests mock ``onnx_asr.load_model`` and ``onnxruntime`` so they run on
CI without the real packages installed. The mock pattern mirrors
``tests/test_parakeet_onnx_load.py``.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# NOTE: no module-level ``pytest.importorskip("onnx_asr")`` — these
# tests mock onnx_asr.load_model so they run without the real package.
# (Belt-and-suspenders: if a downstream CI env has onnx_asr installed
# and wants to validate against the real package, the mocks here still
# drive the engine's fallback logic correctly via sys.modules patching.)
from voice_typer.server.parakeet_engine import (  # noqa: E402
    ParakeetEngine,
    TranscriptionBackendError,
)

# ─── Helpers ────────────────────────────────────────────────────────────


def _mock_onnx_asr_module(recognize_side_effect=None) -> MagicMock:
    """Build a MagicMock that quacks like ``onnx_asr``.

    ``recognize_side_effect`` controls the ``Model.recognize()`` return
    value or side effect:

    - ``None`` → ``recognize()`` returns ``"hello world"`` by default.
    - ``Exception`` → ``recognize()`` raises the exception (used to
      simulate a CUDA OOM).
    - ``callable`` → ``recognize()`` calls the callable.
    - ``str`` → ``recognize()`` returns the string.

    Every ``Model(...)`` call returns a FRESH MagicMock — so the GPU
    session and the CPU-fallback recreated session are distinct mock
    instances, and tests can assert per-instance call counts. Tests
    that want the GPU model to raise and the CPU model to succeed
    should reconfigure ``engine._model.recognize`` AFTER ``load()``
    (which uses the first mock) and rely on ``_load_impl`` creating a
    fresh mock with the default ``"hello world"`` return.
    """
    mock = MagicMock(name="mock_onnx_asr")
    mock.__version__ = "0.12.0-test"

    def _make_model(*args, **kwargs):
        m = MagicMock(name="mock_onnx_asr_model")
        if isinstance(recognize_side_effect, Exception) or callable(recognize_side_effect):
            m.recognize.side_effect = recognize_side_effect
        elif isinstance(recognize_side_effect, str):
            m.recognize.return_value = recognize_side_effect
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
    mock.RunOptions = MagicMock(name="mock_RunOptions")
    mock.RuntimeException = type("RuntimeException", (Exception,), {})
    return mock


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state():
    """Reset ``ParakeetEngine`` class-level state between tests."""
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


def _make_engine_with_cuda_loaded(recognize_return_value: str = "hello world"):
    """Build a ParakeetEngine with a mocked CUDA-loaded ONNX model.

    Returns ``(engine, mock_onnx_asr, mock_onnxruntime)`` so the test
    can assert on ``Model.call_args`` (providers list etc.) and
    reconfigure ``recognize.side_effect`` between the GPU and CPU
    fallback calls.

    ``recognize_return_value`` is the default return value of
    ``model.recognize()`` for BOTH the GPU model (created during
    ``load()``) and any subsequent models (created by ``_load_impl``
    during the CPU fallback). Tests that want the GPU model to raise
    should reconfigure ``engine._model.recognize.side_effect`` AFTER
    ``load()`` returns — the CPU-fallback model (created by
    ``_load_impl``) will still use the default return value.
    """
    mock_onnx_asr = _mock_onnx_asr_module(recognize_side_effect=recognize_return_value)
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


# ─── Tests ──────────────────────────────────────────────────────────────


class TestParakeetOnnxCpuFallback:
    """GPU→CPU fallback (session recreation) — PLAN_ONNX_INTEGRATION.md §3.4."""

    def test_cuda_error_triggers_session_recreation_on_cpu(self):
        """A CUDA error during ``recognize()`` → unload + reload with
        ``CPUExecutionProvider`` only, then re-transcribe on CPU."""
        cuda_oom = RuntimeError("CUDA out of memory")
        # Build engine with default recognize return value "hello world".
        # The CPU-fallback recreated model will use this return value.
        engine, mock_onnx_asr, _ = _make_engine_with_cuda_loaded(
            recognize_return_value="hello world",
        )
        # Make the GPU model (already constructed during load()) raise.
        engine._model.recognize.side_effect = cuda_oom

        published_events: list[dict] = []
        with patch(
            "voice_typer.server.event_bus.publish",
            side_effect=lambda e: published_events.append(e),
        ):
            result = engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        # The CPU-fallback recreated model returns "hello world" by default.
        assert result == "hello world"
        # Model must have been constructed TWICE: once for the GPU
        # session (during load()), once for the CPU fallback (during
        # _load_impl).
        assert mock_onnx_asr.load_model.call_count == 2, (
            f"Expected 2 Model(...) calls (GPU load + CPU fallback recreate), got {mock_onnx_asr.load_model.call_count}"
        )
        # Second call must use CPU providers only.
        second_call_kwargs = mock_onnx_asr.load_model.call_args_list[1].kwargs
        assert second_call_kwargs["providers"] == ["CPUExecutionProvider"], (
            f"CPU fallback must recreate session with providers=['CPUExecutionProvider'] "
            f"only (NOT torch's .to('cpu') — see PLAN_ONNX_INTEGRATION.md §3.4). "
            f"Got: {second_call_kwargs['providers']}"
        )

    def test_cpu_fallback_emits_parakeet_cpu_fallback_event(self):
        """A successful CUDA→CPU fallback publishes the
        ``parakeet_cpu_fallback`` status event (consumed by tray.py)."""
        cuda_oom = RuntimeError("CUDA out of memory")
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = cuda_oom

        published_events: list[dict] = []
        with patch(
            "voice_typer.server.event_bus.publish",
            side_effect=lambda e: published_events.append(e),
        ):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        status_events = [e for e in published_events if e.get("type") == "parakeet_cpu_fallback"]
        assert status_events, (
            "CUDA→CPU fallback must emit a 'parakeet_cpu_fallback' status event "
            "so tray.py can show '(CPU fallback)' suffix."
        )
        assert status_events[0]["data"]["device"] == "cpu"
        assert "CUDA out of memory" in status_events[0]["data"]["reason"]

    def test_cpu_fallback_emits_notification_event(self):
        """A successful fallback also publishes a ``notification`` event
        (user-facing toast: 'GPU transcription failed — switched to CPU')."""
        cuda_oom = RuntimeError("CUDA out of memory")
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = cuda_oom

        published_events: list[dict] = []
        with patch(
            "voice_typer.server.event_bus.publish",
            side_effect=lambda e: published_events.append(e),
        ):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        notif_events = [e for e in published_events if e.get("type") == "notification"]
        assert notif_events, (
            "CUDA→CPU fallback must emit a 'notification' event so the user knows why dictation got slower."
        )
        assert "GPU transcription failed" in notif_events[0]["data"]["message"]

    def test_cpu_fallback_notification_is_one_time(self):
        """The notification fires ONCE per loaded session — a second
        fallback in the same session does NOT re-notify."""
        cuda_oom = RuntimeError("CUDA cublas error")
        engine, mock_onnx_asr, _ = _make_engine_with_cuda_loaded()
        # The first transcribe triggers the fallback. The CPU model
        # returns "hello world" by default. The SECOND transcribe runs
        # on CPU (device was mutated to "cpu" by the fallback) — no
        # fallback fires, no notification.
        engine._model.recognize.side_effect = cuda_oom

        published_events: list[dict] = []
        with patch(
            "voice_typer.server.event_bus.publish",
            side_effect=lambda e: published_events.append(e),
        ):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))
            # Second call: device is now "cpu", so no CUDA error
            # possible → no fallback, no notification.
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        notif_events = [e for e in published_events if e.get("type") == "notification"]
        assert len(notif_events) == 1, (
            f"notification must be ONE-TIME per session — got {len(notif_events)} notifications for 2 calls."
        )

    def test_non_cuda_error_does_not_trigger_cpu_fallback(self):
        """A non-CUDA error (e.g. ValueError) does NOT trigger the
        CPU fallback — it surfaces as ``TranscriptionBackendError``."""
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = ValueError("invalid audio shape")

        with (
            patch("voice_typer.server.event_bus.publish") as mock_publish,
            pytest.raises(TranscriptionBackendError, match="Parakeet transcription failed"),
        ):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))
        # No CPU-fallback event must have been published.
        for call in mock_publish.call_args_list:
            event = call.args[0]
            assert event.get("type") != "parakeet_cpu_fallback", (
                "Non-CUDA error must NOT trigger the parakeet_cpu_fallback event."
            )

    def test_cpu_fallback_load_failure_raises_transcription_backend_error(self):
        """If the CPU session recreation fails (e.g. onnx_asr.load_model
        raises), ``TranscriptionBackendError`` is raised (NOT swallowed)."""
        cuda_oom = RuntimeError("CUDA out of memory")
        engine, mock_onnx_asr, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = cuda_oom
        # Make the second Model(...) call (the CPU recreate) raise.
        mock_onnx_asr.load_model.side_effect = RuntimeError("CPU load failed")

        with patch("voice_typer.server.event_bus.publish"), pytest.raises(TranscriptionBackendError):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

    def test_cpu_fallback_mutates_device_to_cpu(self):
        """After a successful CPU fallback, ``self.device`` is ``"cpu"``
        so the next ``transcribe_with_fallback`` call uses the CPU path
        directly (no re-attempt at CUDA — the ORT session was recreated
        on CPU and stays there until the next ``load()``)."""
        cuda_oom = RuntimeError("CUDA out of memory")
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = cuda_oom

        with patch("voice_typer.server.event_bus.publish"):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        assert engine.device == "cpu", (
            "After CPU fallback, self.device must be 'cpu' so the next "
            "transcribe_with_fallback call doesn't re-attempt CUDA."
        )

    def test_transcribe_with_fallback_raises_when_not_loaded(self):
        """``transcribe_with_fallback`` raises if the model isn't loaded."""
        engine = ParakeetEngine(device="cuda", language="en")
        with pytest.raises(TranscriptionBackendError, match="not loaded"):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

    def test_transcribe_with_fallback_returns_empty_for_empty_audio(self):
        """Empty audio short-circuits to ``""`` without touching the model."""
        engine, _, _ = _make_engine_with_cuda_loaded()
        result = engine.transcribe_with_fallback(np.array([], dtype=np.float32))
        assert result == ""
        # Model.recognize must NOT have been called.
        engine._model.recognize.assert_not_called()


class TestParakeetOnnxCudaErrorClassifier:
    """The fallback path uses ``asr_utils.is_cuda_error`` (or the local
    fallback) to decide whether to recreate the session. Verify the
    engine's classifier integration."""

    def test_cuda_keyword_in_message_triggers_fallback(self):
        """A RuntimeError whose message contains 'cuda' triggers the
        CPU fallback (layer 3 of the 5-layer classifier)."""
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = RuntimeError("CUDA error: something failed")

        with patch("voice_typer.server.event_bus.publish"):
            result = engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        # If the classifier correctly identified it as CUDA, the
        # fallback ran and returned the CPU result ("hello world" by
        # default from the recreated model).
        assert result == "hello world"

    def test_cublas_keyword_in_message_triggers_fallback(self):
        """``cublas`` is one of the 3 CUDA keywords (layer 3)."""
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = RuntimeError("cublas GEMM launch failed")

        with patch("voice_typer.server.event_bus.publish"):
            result = engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        assert result == "hello world"

    def test_cudnn_keyword_in_message_triggers_fallback(self):
        """``cudnn`` is one of the 3 CUDA keywords (layer 3)."""
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = RuntimeError("cudnn convolution failed")

        with patch("voice_typer.server.event_bus.publish"):
            result = engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        assert result == "hello world"

    def test_dll_load_failure_triggers_fallback(self):
        """DLL-load failure keywords (layer 4) trigger the fallback —
        critical for Windows where onnxruntime-gpu is installed but
        the system CUDA Toolkit DLLs are missing."""
        engine, _, _ = _make_engine_with_cuda_loaded()
        engine._model.recognize.side_effect = RuntimeError("load library failed: cudart64_12.dll not found")

        with patch("voice_typer.server.event_bus.publish"):
            result = engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))

        assert result == "hello world"
