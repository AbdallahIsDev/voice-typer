"""Abort test for the ONNX Parakeet engine.

Verifies ``RunOptions.set_terminate(True)`` is called when
``request_abort()`` is invoked during a long-running transcription.
This is the ORT equivalent of the torch/transformers
``StoppingCriteria`` shim — the dictation pipeline's cancel path (ESC
/ watchdog) calls ``request_abort()`` to stop the in-flight
``model.recognize()`` call with bounded latency.

PLAN_ONNX_INTEGRATION.md §3.6:
    > ``tests/test_parakeet_onnx_abort.py`` — verify ``RunOptions`` can
    > abort a long-running transcription (ORT supports this via
    > ``RunOptions``).

The tests mock ``onnxruntime.RunOptions`` so they run on CI without
the real package installed. The mock pattern mirrors
``tests/test_parakeet_onnx_load.py``.
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# NOTE: no module-level ``pytest.importorskip("onnx_asr")`` — these
# tests mock onnx_asr.Model + onnxruntime.RunOptions so they run
# without the real packages.
from voice_typer.server.parakeet_engine import ParakeetEngine  # noqa: E402

# ─── Helpers ────────────────────────────────────────────────────────────


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

    mock.Model.side_effect = _make_model
    return mock


class _FakeRunOptions:
    """Minimal stand-in for ``onnxruntime.RunOptions``.

    Records ``set_terminate`` calls so tests can assert the abort path
    wired through correctly. The real ORT ``RunOptions`` is a C++
    binding — we can't use it without onnxruntime installed.
    """

    def __init__(self) -> None:
        self.terminate_calls: list[bool] = []

    def set_terminate(self, value: bool) -> None:
        self.terminate_calls.append(value)


def _mock_onnxruntime_module(fake_run_options: _FakeRunOptions | None = None) -> MagicMock:
    """Build a MagicMock that quacks like ``onnxruntime``.

    ``fake_run_options`` is returned by ``RunOptions()`` — if None, a
    fresh ``_FakeRunOptions`` is created per call (which breaks
    abort-path testing, so callers that want to assert on
    ``set_terminate`` should pass a shared instance).
    """
    mock = MagicMock(name="mock_onnxruntime")
    mock.__version__ = "1.20.0-test"
    mock.get_available_providers.return_value = ["CPUExecutionProvider"]
    if fake_run_options is not None:
        mock.RunOptions.return_value = fake_run_options
    else:
        mock.RunOptions.side_effect = _FakeRunOptions
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


def _make_engine_with_mocks(
    device: str = "cpu",
    recognize_side_effect=None,
    fake_run_options: _FakeRunOptions | None = None,
):
    """Build a ParakeetEngine with mocked onnx_asr + onnxruntime.

    Returns ``(engine, mock_onnx_asr, mock_onnxruntime, fake_run_options)``.
    """
    mock_onnx_asr = _mock_onnx_asr_module(recognize_side_effect=recognize_side_effect)
    if fake_run_options is None:
        fake_run_options = _FakeRunOptions()
    mock_onnxruntime = _mock_onnxruntime_module(fake_run_options=fake_run_options)
    with patch.dict(
        sys.modules,
        {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
    ):
        engine = ParakeetEngine(device=device, language="en")
        ParakeetEngine._ensure_imports()
        with patch.object(type(engine), "_is_cached", return_value=True):
            engine.load()
    return engine, mock_onnx_asr, mock_onnxruntime, fake_run_options


# ─── Tests ──────────────────────────────────────────────────────────────


class TestParakeetOnnxAbortWiring:
    """``request_abort()`` wires the dictation pipeline's cancel path
    into the ORT ``RunOptions.set_terminate()`` API."""

    def test_request_abort_sets_internal_event(self):
        """``request_abort()`` sets ``_abort_event`` so the chunk loop
        breaks between chunks (even if no RunOptions is stashed yet)."""
        engine, _, _, _ = _make_engine_with_mocks()
        assert not engine._abort_event.is_set()
        engine.request_abort()
        assert engine._abort_event.is_set()

    def test_clear_abort_clears_internal_event(self):
        """``clear_abort()`` clears ``_abort_event`` so a stale abort
        from the previous cycle doesn't suppress the next transcription."""
        engine, _, _, _ = _make_engine_with_mocks()
        engine.request_abort()
        assert engine._abort_event.is_set()
        engine.clear_abort()
        assert not engine._abort_event.is_set()

    def test_request_abort_calls_run_options_set_terminate(self):
        """When a RunOptions is stashed (i.e. a recognize() call is
        in-flight), ``request_abort()`` must call
        ``RunOptions.set_terminate(True)`` so ORT stops the run."""
        fake_ro = _FakeRunOptions()
        engine, _, _, _ = _make_engine_with_mocks(fake_run_options=fake_ro)
        # Stash a RunOptions on the engine (mirrors what
        # _transcribe_segment does before calling model.recognize()).
        engine._run_options = fake_ro
        engine.request_abort()
        assert True in fake_ro.terminate_calls, (
            "request_abort() must call RunOptions.set_terminate(True) when "
            "a RunOptions is stashed — ORT stops the in-flight recognize() "
            "call with bounded latency."
        )

    def test_request_abort_with_no_run_options_stashed_is_noop(self):
        """When no RunOptions is stashed (no in-flight recognize()),
        ``request_abort()`` must NOT raise — it just sets the internal
        event so the next chunk loop iteration breaks."""
        engine, _, _, _ = _make_engine_with_mocks()
        assert engine._run_options is None
        # Must not raise.
        engine.request_abort()
        assert engine._abort_event.is_set()

    def test_clear_abort_drops_stashed_run_options(self):
        """``clear_abort()`` drops the stashed RunOptions so the next
        ``_transcribe_segment`` call creates a fresh one. ORT's
        ``set_terminate(True)`` is one-way — a terminated RunOptions
        cannot be reused."""
        fake_ro = _FakeRunOptions()
        engine, _, _, _ = _make_engine_with_mocks(fake_run_options=fake_ro)
        engine._run_options = fake_ro
        engine.clear_abort()
        assert engine._run_options is None, (
            "clear_abort() must drop the stashed RunOptions — ORT's "
            "set_terminate(True) is one-way; the next recognize() call "
            "needs a fresh RunOptions."
        )

    def test_transcribe_segment_stashes_and_clears_run_options(self):
        """``_transcribe_segment`` must stash the RunOptions on
        ``self._run_options`` BEFORE calling ``model.recognize()``
        (so ``request_abort()`` can reach it) and clear it AFTER
        (so a stale terminated RunOptions isn't reused)."""
        engine, _, _, _ = _make_engine_with_mocks()
        assert engine._run_options is None
        # Transcribe a short audio (≤ 25s → single segment, no chunking).
        audio = np.ones(16000, dtype=np.float32)
        engine.transcribe(audio)
        # After transcribe completes, _run_options must be cleared.
        assert engine._run_options is None, (
            "_transcribe_segment must clear _run_options after recognize() "
            "returns — a stale RunOptions would break the next abort."
        )

    def test_abort_between_chunks_stops_loop_early(self):
        """When ``_abort_event`` is set, the chunk loop in
        ``_transcribe_chunks`` breaks BEFORE processing the next chunk.
        A 13-chunk dictation stops after the current chunk rather than
        decoding all remaining ones."""
        # Build a model whose recognize() sets the abort event after
        # the FIRST call (so the loop sees abort=True before chunk 2).
        call_state = {"n": 0}

        def _recognize_then_abort(audio, **kwargs):
            call_state["n"] += 1
            # After the first chunk, set the abort event.
            if call_state["n"] >= 1:
                # Reach into the engine via closure — set on the
                # outer-scope engine reference (set after construction).
                _recognize_then_abort.engine._abort_event.set()
            return f"chunk {call_state['n']}"

        engine, mock_onnx_asr, _, _ = _make_engine_with_mocks(
            recognize_side_effect=_recognize_then_abort,
        )
        _recognize_then_abort.engine = engine  # type: ignore[attr-defined]
        # Reset the abort event before transcribe (the helper above
        # sets it after the first chunk).
        engine._abort_event.clear()
        # 60s of audio → 3 chunks (25s + 25s + 10s).
        audio = np.ones(int(60 * 16000), dtype=np.float32)
        engine.transcribe(audio)
        # Only the first chunk should have been transcribed (abort
        # fired after chunk 1, loop breaks before chunk 2).
        assert call_state["n"] == 1, (
            f"Expected 1 recognize() call (abort after chunk 1 stops the loop), got {call_state['n']}."
        )


class TestParakeetOnnxAbortRunOptionsCreation:
    """``_make_run_options()`` creates a fresh ORT RunOptions per call."""

    def test_make_run_options_returns_run_options_instance(self):
        """``_make_run_options()`` returns the result of
        ``onnxruntime.RunOptions()``."""
        fake_ro = _FakeRunOptions()
        engine, _, mock_ort, _ = _make_engine_with_mocks(fake_run_options=fake_ro)
        opts = engine._make_run_options()
        assert opts is fake_ro, (
            "_make_run_options() must return the onnxruntime.RunOptions() instance so request_abort() can reach it."
        )
        mock_ort.RunOptions.assert_called_once()

    def test_make_run_options_stashes_on_self(self):
        """The created RunOptions is stashed on ``self._run_options``
        so ``request_abort()`` can reach it during the in-flight call."""
        fake_ro = _FakeRunOptions()
        engine, _, _, _ = _make_engine_with_mocks(fake_run_options=fake_ro)
        assert engine._run_options is None
        engine._make_run_options()
        assert engine._run_options is fake_ro

    def test_make_run_options_returns_none_when_ort_unavailable(self):
        """Defensive: if ``onnxruntime`` is not available (shouldn't
        happen post-_ensure_imports, but tests bypass it),
        ``_make_run_options()`` returns None rather than raising."""
        engine = ParakeetEngine(device="cpu", language="en")
        # _ort is None (no _ensure_imports called).
        assert engine._make_run_options() is None


class TestParakeetOnnxAbortEndToEnd:
    """End-to-end: a long-running recognize() that respects abort."""

    def test_abort_during_long_running_recognize_calls_set_terminate(self):
        """Simulate a long-running ``recognize()`` (e.g. 30s audio)
        and verify ``request_abort()`` calls
        ``RunOptions.set_terminate(True)`` on the stashed options
        mid-decode."""

        # Build a model whose recognize() blocks until the abort event
        # is set, then returns. This mirrors the real ORT behavior: a
        # terminated RunOptions causes recognize() to raise (or return
        # partial output) and unblock the caller.
        def _long_running_recognize(audio, **kwargs):
            # Spin until the abort event is set (or timeout).
            for _ in range(100):
                if _long_running_recognize.engine._abort_event.is_set():
                    return "aborted"
                time.sleep(0.01)
            return "timeout"

        fake_ro = _FakeRunOptions()
        engine, _, _, _ = _make_engine_with_mocks(
            recognize_side_effect=_long_running_recognize,
            fake_run_options=fake_ro,
        )
        _long_running_recognize.engine = engine  # type: ignore[attr-defined]

        # Transcribe in a background thread (so we can call request_abort
        # from the main thread while recognize() is spinning).
        audio = np.ones(16000, dtype=np.float32)  # short audio → single segment
        result_holder: dict[str, str] = {}

        def _transcribe_thread():
            result_holder["text"] = engine.transcribe(audio)

        t = threading.Thread(target=_transcribe_thread, daemon=True)
        t.start()
        # Give the transcribe thread a moment to enter recognize() and
        # stash the RunOptions.
        for _ in range(50):
            if engine._run_options is not None:
                break
            time.sleep(0.01)
        # Now call request_abort from the main thread.
        engine.request_abort()
        t.join(timeout=5.0)
        assert not t.is_alive(), "transcribe thread did not unblock after request_abort()"
        assert result_holder.get("text") == "aborted", (
            f"Expected 'aborted' (recognize() returned after abort), got {result_holder.get('text')!r}"
        )
        # The RunOptions.set_terminate(True) must have been called.
        assert True in fake_ro.terminate_calls, (
            "request_abort() must call RunOptions.set_terminate(True) on "
            "the stashed options so ORT stops the in-flight recognize() "
            "call with bounded latency."
        )
