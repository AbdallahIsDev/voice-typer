"""Tests for the Silero VAD wrapper (ONNX Runtime backend).

Companion §2.4 — the JIT-era tests mocked ``torch.from_numpy`` /
``torch.zeros`` / ``torch.cat`` / ``torch.no_grad``. The ORT rewrite
mocks ``onnxruntime.InferenceSession`` with a fake that returns fixed
``(output, stateN)`` tuples and records every call so the hidden-state
threading (companion §2.2) is verifiable end-to-end.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# ─── Fake onnxruntime.InferenceSession ────────────────────────────────


class _FakeNode:
    """Minimal stand-in for ``onnxruntime.NodeArg`` — only ``name`` is read."""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeOrtSession:
    """Fake ``onnxruntime.InferenceSession`` for VAD tests.

    Returns fixed ``(output, stateN)`` tuples and records every
    ``run()`` call so tests can assert:

    1. The audio input was padded/truncated/sliced to 512-sample windows.
    2. The LSTM hidden state (shape ``(2, 1, 128)`` float32) is threaded
       forward — each call receives the previous call's ``stateN`` return
       value as its ``state`` input.
    3. The sample-rate feed entry is passed when the session declares an
       ``sr`` input.

    The fake is intentionally minimal — it does NOT validate shapes or
    dtypes (real ORT does). The test fixtures pin the shapes via the
    ``compute_vad_prob`` contract: 1-D float32 audio in, float prob out.
    """

    def __init__(
        self,
        prob_sequence: list[float] | None = None,
        state_delta: float = 1.0,
    ) -> None:
        # ``prob_sequence`` lets a test script a series of probabilities
        # across multiple sub-chunk calls. The last value is reused if
        # the session is called more times than the sequence has entries
        # (so a 5120-sample chunk with 10 sub-chunks doesn't need 10
        # entries when the test only cares about call_count).
        self._prob_seq = list(prob_sequence) if prob_sequence else [0.5]
        self._prob_idx = 0
        # ``state_delta`` makes state threading observable: each call
        # returns ``state + delta`` so a test can verify the next call's
        # input state equals the previous call's output state.
        self._state_delta = float(state_delta)
        self.calls: list[dict[str, object]] = []

    def get_inputs(self) -> list[_FakeNode]:
        return [
            _FakeNode("input"),
            _FakeNode("state"),
            _FakeNode("sr"),
        ]

    def get_outputs(self) -> list[_FakeNode]:
        return [
            _FakeNode("output"),
            _FakeNode("stateN"),
        ]

    def run(self, output_names, feed):
        # Capture the call for assertions. Copy the input + state arrays
        # so a later mutation by the caller doesn't retroactively edit
        # the recorded history (numpy slices share memory).
        input_arr = np.array(feed["input"], copy=True)
        state_arr = np.array(feed["state"], copy=True)
        sr_value = int(feed["sr"]) if "sr" in feed else None
        self.calls.append(
            {
                "input": input_arr,
                "state": state_arr,
                "sr": sr_value,
            }
        )
        # Pull the next probability from the script. Reuse the last
        # value if the script is exhausted.
        if self._prob_idx < len(self._prob_seq):
            prob = float(self._prob_seq[self._prob_idx])
        else:
            prob = float(self._prob_seq[-1])
        self._prob_idx += 1
        # Silero v4 ONNX ``output`` shape is ``(1, 1)`` for a single
        # batched window. Use that exact shape so the production code's
        # ``np.asarray(out[0]).reshape(-1)[0]`` indexing works.
        out_prob = np.array([[prob]], dtype=np.float32)
        # Return the input state + delta so the next call's input state
        # is observably different from a fresh zero buffer.
        new_state = state_arr + self._state_delta
        return [out_prob, new_state]


def _install_fake_ort(monkeypatch, session: FakeOrtSession) -> MagicMock:
    """Install a fake ``onnxruntime`` module whose ``InferenceSession``
    returns ``session``. Mirrors the JIT-era ``_setup_torch_mock`` helper.

    Returns the underlying MagicMock so a test can additionally assert
    on ``mock.InferenceSession.call_args`` if needed.
    """
    mock_ort = MagicMock(name="fake_onnxruntime")
    mock_ort.InferenceSession = MagicMock(return_value=session)
    monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)
    return mock_ort


# ─── VAD-001: non-512-sample chunk handling ───────────────────────────


class TestSileroVadHandlesNon512SampleChunks:
    """VAD-001: Silero VAD must handle non-512-sample chunks.

    Previously, PortAudio delivered chunks of arbitrary size (e.g. 1136
    on WASAPI) and Silero VAD raised ValueError because it strictly
    expects 512 samples at 16kHz. The fix pads/truncates the chunk
    before inference.
    """

    def test_compute_vad_prob_handles_non_512_chunk(self, monkeypatch):
        """compute_vad_prob must not crash on a 1136-sample chunk."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.75])
        _install_fake_ort(monkeypatch, session)
        # Point _VAD_MODEL_PATH at an existing file so _load_model
        # proceeds past the missing-file short-circuit.
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # 1136-sample chunk (typical WASAPI block)
        audio = np.ones(1136, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        # Use approx because the fake ORT session returns float32;
        # the production code casts via ``float(...)`` which preserves
        # the float32 representation but loses exactness for values
        # like 0.3 / 0.85 / 0.9 / 0.4.
        assert prob == pytest.approx(0.75)
        # 1136 // 512 = 2 sub-chunks; trailing 112 samples dropped.
        assert len(session.calls) == 2
        for call in session.calls:
            assert call["input"].shape == (1, 512), (
                f"Each sub-chunk must be reshaped to (1, 512); got {call['input'].shape}"
            )
        vad.reset()

    def test_compute_vad_prob_handles_small_chunk(self, monkeypatch):
        """compute_vad_prob must pad a 100-sample chunk to 512."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.3])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(100, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == pytest.approx(0.3)
        assert len(session.calls) == 1
        assert session.calls[0]["input"].shape == (1, 512)
        vad.reset()

    def test_compute_vad_prob_handles_exact_512_chunk(self, monkeypatch):
        """compute_vad_prob must work with exactly 512 samples."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.9])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(512, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == pytest.approx(0.9)
        assert len(session.calls) == 1
        assert session.calls[0]["input"].shape == (1, 512)
        vad.reset()


# ─── AUDIO-10: long-chunk slicing ─────────────────────────────────────


class TestSileroVadSlicesLongChunksIntoSubchunks:
    """AUDIO-10: long audio chunks must be sliced into 512-sample
    sub-chunks, run through the model on each, and the MAX probability
    returned. Previously the chunk was truncated to the first 512
    samples, discarding up to 55% of the audio (624 of 1136 samples on
    a typical WASAPI block)."""

    def test_long_chunk_processes_all_full_subchunks(self, monkeypatch):
        """AUDIO-10: a 1136-sample chunk produces 2 model calls
        (sub-chunks [0:512] and [512:1024]), each exactly 512 samples."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5, 0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(1136, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert len(session.calls) == 2, f"Expected 2 sub-chunk calls for 1136 samples, got {len(session.calls)}"
        for call in session.calls:
            assert call["input"].shape == (1, 512)
        assert prob == pytest.approx(0.5)
        vad.reset()

    def test_long_chunk_takes_max_probability(self, monkeypatch):
        """AUDIO-10: when sub-chunks return different probabilities,
        the MAX is returned (speech is an "any sub-chunk contains it"
        decision — max is more sensitive than mean for short bursts)."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.2, 0.85])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # 1024 samples → exactly 2 sub-chunks of 512.
        audio = np.ones(1024, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        # Max of [0.2, 0.85] = 0.85 — speech in the second sub-chunk
        # is detected. Under OLD truncation, prob would be 0.2 (missed).
        assert prob == pytest.approx(0.85), f"Expected max prob 0.85 (speech in 2nd sub-chunk), got {prob}"
        vad.reset()

    def test_very_long_chunk_processes_all_subchunks(self, monkeypatch):
        """AUDIO-10: a 5120-sample chunk (10× the Silero block size)
        produces exactly 10 model calls — verifies the slicing loop."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.6] * 10)
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(5120, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert len(session.calls) == 10, f"Expected 10 sub-chunk calls for 5120 samples, got {len(session.calls)}"
        for call in session.calls:
            assert call["input"].shape == (1, 512)
        assert prob == pytest.approx(0.6)
        vad.reset()

    def test_long_chunk_with_odd_remainder_drops_remainder(self, monkeypatch):
        """AUDIO-10: a 1500-sample chunk yields 2 sub-chunks of 512
        (1024 samples) + 476-sample remainder dropped (not padded)."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.4, 0.4])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        audio = np.ones(1500, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert len(session.calls) == 2, f"Expected 2 sub-chunk calls for 1500 samples, got {len(session.calls)}"
        for call in session.calls:
            assert call["input"].shape == (1, 512)
        assert prob == pytest.approx(0.4)
        vad.reset()


# ─── Companion §2.2: hidden-state threading ───────────────────────────


class TestHiddenStateThreading:
    """Companion §2.2 — the LSTM hidden state (shape ``(2, 1, 128)``
    float32) MUST be threaded through every ``compute_vad_prob`` call.
    The JIT module held it internally; ORT's stateless InferenceSession
    forces the caller to manage it. If the state is not threaded, VAD
    probabilities are garbage after the first 512-sample window.
    """

    def test_state_buffer_has_correct_shape(self, monkeypatch):
        """``_state`` must be ``(2, 1, 128)`` float32 after load."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        vad._load_model()
        assert vad._state is not None
        assert vad._state.shape == (2, 1, 128), (
            f"Silero v4 LSTM state shape must be (2, 1, 128); got {vad._state.shape}"
        )
        assert vad._state.dtype == np.float32
        vad.reset()

    def test_state_threads_forward_across_calls(self, monkeypatch):
        """Each ``session.run`` call must receive the previous call's
        returned ``stateN`` as its ``state`` input. The fake session
        returns ``state + 1.0`` so the threading is observable: the
        second call's input state should be ``1.0``, the third ``2.0``,
        etc. (starting from a zero buffer)."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5, 0.5, 0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # 1536 samples → 3 sub-chunks of 512.
        audio = np.ones(1536, dtype=np.float32) * 0.1
        vad.compute_vad_prob(audio, sample_rate=16000)

        assert len(session.calls) == 3
        # First call: state = zeros (initial load).
        assert np.array_equal(session.calls[0]["state"], np.zeros((2, 1, 128), dtype=np.float32))
        # Second call: state = first call's return = 0.0 + 1.0 = 1.0
        assert np.allclose(session.calls[1]["state"], 1.0)
        # Third call: state = second call's return = 1.0 + 1.0 = 2.0
        assert np.allclose(session.calls[2]["state"], 2.0)
        vad.reset()

    def test_reset_states_zeros_buffer_when_loaded(self, monkeypatch):
        """``reset_states()`` must re-zero ``_state`` when the session
        is loaded — the load-bearing reset for session boundaries."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # Load + run inference to populate _state with non-zero values.
        vad._load_model()
        audio = np.ones(512, dtype=np.float32) * 0.1
        vad.compute_vad_prob(audio, sample_rate=16000)
        assert not np.array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32)), (
            "Test setup: _state should be non-zero after an inference call"
        )

        vad.reset_states()
        assert np.array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32)), (
            "reset_states() must zero the LSTM hidden buffer"
        )
        vad.reset()

    def test_unload_clears_session_and_state(self, monkeypatch):
        """``unload()`` must drop the ORT session AND reset the hidden
        state — companion §2.3.5. A subsequent ``preload()`` / first
        chunk load must start from a clean state."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        vad._load_model()
        assert vad._model is not None
        assert vad._state is not None

        vad.unload()
        assert vad._model is None
        # _state is None when no session is loaded (matches the
        # ``reset_states()`` no-session branch).
        assert vad._state is None
        vad.reset()

    def test_reset_states_noop_when_unloaded(self, monkeypatch):
        """``reset_states()`` must NOT trigger a model load when called
        on an unloaded session — the JIT-era contract."""
        import voice_typer.server.vad as vad

        vad.reset()
        # No fake ORT installed → if reset_states triggered a load,
        # _load_model would try ``import onnxruntime`` (real, missing
        # in this env) and return (None, None). The state stays None.
        vad.reset_states()
        assert vad._model is None
        assert vad._state is None
        vad.reset()

    def test_state_zeroed_on_first_load(self, monkeypatch):
        """The first ``_load_model()`` call must initialize ``_state``
        to zeros — companion §2.2 says this is the first-load path."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        # Pre-condition: _state is None before the first load.
        assert vad._state is None

        vad._load_model()
        assert vad._state is not None
        assert vad._state.shape == (2, 1, 128)
        assert np.array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32)), (
            "First load must initialize _state to zeros"
        )
        vad.reset()


# ─── Test fixtures: WaveformBubble (kept from the prior file) ─────────


@pytest.fixture
def bubble():
    """WaveformBubble fixture — local copy of the one in
    ``tests/test_waveform_bubble.py`` (moved alongside
    ``TestWaveformVADGate`` in WR-12)."""
    from voice_typer.server.waveform import WaveformBubble

    return WaveformBubble()


# ─── T021: Silero VAD integration tests ───────────────────────────────


class TestVADModule:
    """Test the VAD wrapper module (voice_typer.server.vad)."""

    def test_is_available_returns_bool(self):
        """is_available() should return True or False, not raise."""
        from voice_typer.server.vad import is_available

        result = is_available()
        assert isinstance(result, bool)

    def test_compute_vad_prob_without_ort(self, monkeypatch):
        """When onnxruntime is not available, compute_vad_prob returns None.

        Companion §2.3.4 — ``is_available()`` now probes onnxruntime
        instead of torch. The ``_load_model`` failure path returns
        ``(None, None)`` and ``compute_vad_prob`` falls through to None
        so the RMS fallback fires.
        """
        from voice_typer.server import vad

        # Force onnxruntime to be unimportable.
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        vad.reset()
        result = vad.compute_vad_prob(np.zeros(16000, dtype=np.float32))
        assert result is None
        vad.reset()

    def test_is_speech_fallback_rms(self, monkeypatch):
        """Without VAD, is_speech falls back to RMS energy check.

        VAD-001: Previously this test relied on vad.reset() + a 16000-sample
        chunk erroring through Silero VAD (which requires exactly 512 samples)
        to exercise the RMS fallback. We now properly mock _load_model to
        return ``(None, None)`` so the RMS fallback is deterministically
        exercised regardless of whether onnxruntime/Silero is installed.
        """
        from voice_typer.server import vad

        vad.reset()
        # Force VAD to be unavailable so RMS fallback is exercised
        monkeypatch.setattr(vad, "_load_model", lambda: (None, None))
        # Silence
        assert vad.is_speech(np.zeros(16000, dtype=np.float32)) is False
        # Loud audio
        assert vad.is_speech(np.full(16000, 0.1, dtype=np.float32)) is True
        vad.reset()

    def test_is_speech_empty_audio(self):
        """Empty audio chunk should return False."""
        from voice_typer.server.vad import is_speech

        assert is_speech(np.array([], dtype=np.float32)) is False

    def test_reset_clears_model(self):
        """reset() should clear the cached model + state."""
        from voice_typer.server import vad

        vad.reset()
        assert vad._model is None
        assert vad._state is None

    def test_ort_missing_warning_rate_limited(self, caplog, monkeypatch):
        """When onnxruntime is unavailable, repeated ``_load_model``
        calls (every 16 Hz audio chunk) must NOT re-log the identical
        WARNING — only the 1st occurrence logs at WARNING; repeats
        drop to DEBUG (log_rate_limited, first-only).

        Regression: ``_load_model`` does not cache a failure (model
        stays None), so a permanently ORT-less environment would
        otherwise emit ~960 identical WARNINGs/minute.
        """
        import logging

        from voice_typer.server import vad

        vad.reset()
        monkeypatch.setitem(sys.modules, "onnxruntime", None)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.vad"):
            for _ in range(5):
                assert vad._load_model() == (None, None)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, (
            f"onnxruntime-missing WARNING must be rate-limited to 1 occurrence across 5 calls; got {len(warnings)}"
        )
        vad.reset()

    def test_bundled_model_missing_error_rate_limited(self, caplog, monkeypatch):
        """When the bundled ``silero_vad.onnx`` is missing, repeated
        ``_load_model`` calls must log the ERROR once, not per call."""
        import logging
        from pathlib import Path

        from voice_typer.server import vad

        vad.reset()
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path("/nonexistent/silero_vad.onnx"))
        # ORT is importable (mock) so we exercise the missing-file branch,
        # not the ImportError short-circuit.
        _install_fake_ort(monkeypatch, FakeOrtSession(prob_sequence=[0.5]))

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.vad"):
            for _ in range(5):
                assert vad._load_model() == (None, None)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1, (
            f"missing-model ERROR must be rate-limited to 1 occurrence across 5 calls; got {len(errors)}"
        )
        vad.reset()

    def test_local_load_failure_error_rate_limited(self, caplog, monkeypatch):
        """When ``InferenceSession(...)`` raises (corrupt/undownloadable
        model), repeated ``_load_model`` calls must NOT spam the ERROR
        — the 1st + every Nth occurrence logs at ERROR, repeats at DEBUG.

        Regression: the failure is not cached, so a permanently corrupt
        model would otherwise log ~960 ERRORs/minute on the 16 Hz audio
        path.
        """
        import logging

        from voice_typer.server import vad

        vad.reset()
        mock_ort = MagicMock(name="fake_onnxruntime")
        mock_ort.InferenceSession = MagicMock(side_effect=RuntimeError("corrupt onnx model"))
        monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.vad"):
            for _ in range(5):
                assert vad._load_model() == (None, None)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1, (
            f"load-failure ERROR must be rate-limited (1st + every Nth only); got {len(errors)} across 5 calls"
        )
        assert vad._model is None, "failure must not cache a model"
        vad.reset()

    def test_providers_pinned_to_cpu(self, monkeypatch):
        """Companion §2.3.3 — the ORT session MUST be created with
        ``providers=["CPUExecutionProvider"]`` only. VAD is CPU-only by
        design; routing to GPU adds upload latency per 512-sample
        window and breaks the latency budget. Source-level guard so a
        future reader doesn't 'fix' this by adding CUDAExecutionProvider."""
        import inspect

        from voice_typer.server import vad

        src = inspect.getsource(vad._load_model)
        assert 'providers=["CPUExecutionProvider"]' in src, (
            "VAD must pin providers=['CPUExecutionProvider'] — see "
            "companion §2.3.3 for the rationale (CPU-only by design, "
            "GPU upload latency dwarfs the ~0.5ms inference for a "
            "512-sample window)."
        )

    def test_no_torch_import_in_vad_source(self):
        """Companion §1 — ``vad.py`` must NOT import torch anywhere.
        Source-level guard so a future refactor doesn't silently
        re-add the torch dependency."""
        import inspect

        from voice_typer.server import vad

        src = inspect.getsource(vad)
        # ``torch`` may appear in comments / docstrings as a historical
        # reference (the JIT-era code is gone); what's banned is an
        # actual ``import torch`` / ``from torch`` statement. Match the
        # import-statement pattern, not bare substrings.
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith("import torch"), (
                "vad.py must not 'import torch' — torch is removed as a "
                "project dependency under the ONNX migration (companion §1)."
            )
            assert not stripped.startswith("from torch"), (
                "vad.py must not 'from torch import ...' — torch is removed "
                "as a project dependency under the ONNX migration (companion §1)."
            )


class TestWaveformVADGate:
    """Test that WaveformBubble.update_level uses the RMS-only path.

    BUBBLE-FIX-4.1: the previous VAD gate (T021) was removed because it
    called ``compute_vad_prob`` with the device's native sample-rate
    audio (often 44.1/48 kHz) but the VAD model assumes 16 kHz,
    systematically biasing probabilities low and collapsing the bars.
    The visualizer now relies on the renderer's attack/release smoothing
    to handle ambient noise.  These tests verify the RMS-only behavior
    is preserved whether or not an ``audio_chunk`` is supplied.
    """

    def test_update_level_without_audio_chunk(self, bubble):
        """When no audio_chunk is passed, update_level works as before (RMS-only)."""
        bubble.update_level(0.1, 0.2)
        assert abs(bubble.rms_level - 0.045) < 0.01  # smoothed
        assert bubble.is_speaking is True  # 0.045 > 0.01 threshold

    def test_update_level_with_silent_audio_chunk(self, bubble, monkeypatch):
        """With a silent audio chunk but non-zero RMS, the RMS-only path
        fires (VAD gate removed in BUBBLE-FIX-4.1).  is_speaking tracks
        the smoothed RMS level, not VAD output."""
        # VAD is no longer consulted; the audio_chunk argument is accepted
        # for backward-compat with callers but ignored.
        bubble.update_level(0.15, 0.3, audio_chunk=np.zeros(16000, dtype=np.float32))
        # RMS-only path: smoothed level is 0.5 * 0 (initial) + 0.5 * 0.15 = 0.075
        assert bubble.rms_level > 0
        assert bubble.is_speaking is True  # 0.075 > 0.005 threshold

    def test_update_level_with_speech_audio_chunk(self, bubble, monkeypatch):
        """With speech audio chunk, the RMS-only path updates normally
        (VAD gate removed in BUBBLE-FIX-4.1)."""
        bubble.update_level(0.15, 0.3, audio_chunk=np.full(16000, 0.1, dtype=np.float32))
        assert bubble.rms_level > 0
        assert bubble.is_speaking is True

    def test_update_level_with_zero_rms_decays(self, bubble):
        """With zero RMS (true silence), the level decays and is_speaking
        eventually becomes False."""
        # Prime the level
        bubble.update_level(0.2, 0.4)
        assert bubble.is_speaking is True
        # Feed silence
        for _ in range(20):
            bubble.update_level(0.0, 0.0)
        assert bubble.rms_level < 0.005
        assert bubble.is_speaking is False


class TestProductionWiring:
    """T021: verify the audio_chunk path is wired end-to-end.

    The VAD gate existed in waveform.py but was inert in production
    because the recorder RMS callback didn't pass audio_chunk to
    WaveformBubble.update_level. These tests verify the wiring is
    now in place.

    REFACTOR: the old ``VoiceTyperApp._on_recorder_rms`` was extracted
    into ``RecordingController.on_recorder_rms`` (see
    ``recording_controller.py``). The recorder callback is wired via
    ``app.recorder.on_rms_level = self.on_recorder_rms`` in
    ``RecordingController.wire()``.
    """

    def test_app_on_recorder_rms_accepts_audio_chunk(self):
        """RecordingController.on_recorder_rms must accept audio_chunk
        and forward it to WaveformBubble.update_level for VAD gating."""
        import inspect
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController

        sig = inspect.signature(RecordingController.on_recorder_rms)
        assert "audio_chunk" in sig.parameters, (
            "on_recorder_rms must accept audio_chunk kwarg to forward "
            "audio to WaveformBubble.update_level for VAD gating"
        )

        # Production wiring assertion: on_recorder_rms must route the
        # rms/peak/audio_chunk through to the bubble's update_level.
        controller = MagicMock(spec=RecordingController)
        controller._app = MagicMock()
        chunk = np.full(512, 0.1, dtype=np.float32)
        RecordingController.on_recorder_rms(controller, 0.05, 0.12, audio_chunk=chunk)
        controller._app._waveform_bubble.update_level.assert_called_once_with(
            0.05,
            0.12,
            audio_chunk=chunk,
        )

    def test_recorder_callback_passes_three_args(self):
        """Recorder.on_rms_level callback receives 3 args: rms, peak, audio_chunk.

        Reads the source of the recording module to confirm the callback
        is invoked with 3 positional arguments (not 2). The callback
        is a nested function inside Recorder.start(), so we read the
        whole module source as a static check.
        """
        import inspect

        from voice_typer.server import recording

        src = inspect.getsource(recording)
        assert "rms_callback(chunk_rms, chunk_peak, filtered)" in src, (
            "Recorder's audio callback must pass the filtered audio chunk "
            "as the 3rd argument to rms_callback so VAD can run on it"
        )

    def test_update_level_signature_accepts_audio_chunk(self):
        """WaveformBubble.update_level must accept audio_chunk kwarg."""
        import inspect

        from voice_typer.server.waveform import WaveformBubble

        sig = inspect.signature(WaveformBubble.update_level)
        assert "audio_chunk" in sig.parameters, (
            "WaveformBubble.update_level must accept audio_chunk kwarg to run VAD on the incoming audio"
        )


class TestVadLocalOnlyNoNetwork:
    """C-DATA-1 regression: the VAD module must NEVER make a network call.

    The JIT-era ``_load_model`` previously had a ``torch.hub.load``
    fallback that fired when the bundled ``silero_vad.jit`` was missing
    — a hard HTTPS call to github.com that violated the offline
    guarantee. The fallback was removed long ago and the ORT rewrite
    carries the same contract: missing ``silero_vad.onnx`` → ERROR +
    ``(None, None)``, NEVER a network fetch.
    """

    _VAD_SRC_PATH = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "vad.py"

    def test_load_model_returns_quickly_without_network(self, monkeypatch):
        """``_load_model`` must not block on a network timeout.

        Even when the bundled model file is missing, ``_load_model``
        returns immediately (no hub fetch, no ThreadPoolExecutor
        deadline) — it logs an ERROR and returns ``(None, None)``.
        """
        import time

        from voice_typer.server import vad

        vad.reset()
        # Point the path at a nonexistent file so the local-load
        # branch is skipped — exercising the missing-model path that
        # previously fell through to the network call.
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path("/nonexistent/silero_vad.onnx"))
        # Install a fake ORT so we exercise the missing-file branch
        # (not the ORT-missing short-circuit).
        _install_fake_ort(monkeypatch, FakeOrtSession(prob_sequence=[0.5]))

        start = time.monotonic()
        result = vad._load_model()
        elapsed = time.monotonic() - start

        assert result == (None, None)
        # 3s is far below the old 5s hub-timeout deadline — proves no
        # network call was attempted.
        assert elapsed < 3.0, (
            f"_load_model took {elapsed:.2f}s — looks like a network call was attempted (C-DATA-1 violation)"
        )
        vad.reset()

    def test_no_torch_hub_load_in_vad_source(self):
        """``vad.py`` must not import or call ``torch.hub.load``.

        Source-level grep assertion so the network fallback cannot be
        silently reintroduced by a future refactor. (Also catches
        ``requests.get`` / ``urllib`` etc. — defensive pin against any
        network egress helper, not just torch.hub.)
        """
        src = self._VAD_SRC_PATH.read_text(encoding="utf-8")
        assert "torch.hub.load" not in src, (
            "C-DATA-1 violation: voice_typer/server/vad.py references "
            "'torch.hub.load' — a hard network call to github.com that "
            "breaks the offline guarantee."
        )
        assert "hub_load" not in src, (
            "C-DATA-1 violation: voice_typer/server/vad.py still has a "
            "'hub_load' reference — the network-fallback helper / "
            "negative-cache flag should have been removed entirely."
        )
        assert "_HUB_LOAD_TIMEOUT_S" not in src, (
            "Dead-code leftover: _HUB_LOAD_TIMEOUT_S was the deadline "
            "for the (now-removed) torch.hub.load fallback and should "
            "have been removed with it."
        )

    def test_load_model_returns_none_none_when_model_missing(self, monkeypatch):
        """When the bundled model is missing, ``_load_model`` returns
        ``(None, None)`` without raising and without a network call."""
        from voice_typer.server import vad

        vad.reset()
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path("/nonexistent/silero_vad.onnx"))
        # ORT is importable (mock) so we exercise the missing-file path
        # rather than the ORT-missing short-circuit.
        _install_fake_ort(monkeypatch, FakeOrtSession(prob_sequence=[0.5]))

        # Must not raise.
        result = vad._load_model()
        assert result == (None, None)
        # And the cached _model must remain None so subsequent
        # compute_vad_prob calls degrade to the RMS fallback.
        assert vad._model is None
        vad.reset()


class TestPreloadWarmup:
    """``preload()`` must load the session, run a warmup inference, and
    reset the LSTM state so the first real audio chunk starts clean.
    """

    def test_preload_runs_warmup_and_resets_state(self, monkeypatch):
        """preload() should call the session once for warmup (with a
        512-sample zero tensor), then reset_states() so the production
        path starts from a zero state."""
        import voice_typer.server.vad as vad

        session = FakeOrtSession(prob_sequence=[0.5])
        _install_fake_ort(monkeypatch, session)
        monkeypatch.setattr(vad, "_VAD_MODEL_PATH", Path(__file__))
        vad.reset()

        assert vad.preload() is True
        # Exactly one warmup call.
        assert len(session.calls) == 1
        assert session.calls[0]["input"].shape == (1, 512)
        # State was reset after warmup — _state is zeros, NOT the
        # post-warmup ``state + 1.0`` value the fake would otherwise
        # have left behind.
        assert np.array_equal(vad._state, np.zeros((2, 1, 128), dtype=np.float32)), (
            "preload() must reset_states() after warmup so the first real audio chunk starts from a clean LSTM state"
        )
        vad.reset()

    def test_preload_returns_false_when_ort_missing(self, monkeypatch):
        """preload() must return False (not raise) when onnxruntime is
        unavailable — the RMS fallback path fires downstream."""
        from voice_typer.server import vad

        vad.reset()
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        assert vad.preload() is False
        assert vad._model is None
        vad.reset()
