import sys
from unittest.mock import MagicMock

import numpy as np
import pytest


class _MockTensor:
    """Minimal torch.Tensor mock for VAD-001 tests."""

    def __init__(self, data):
        self.data = np.asarray(data, dtype=np.float32)
        self._shape = [len(self.data)]

    @property
    def shape(self):
        return self._shape

    def dim(self):
        return 1

    def squeeze(self):
        return self

    def float(self):
        return self

    def item(self):
        return float(self.data[0]) if len(self.data) > 0 else 0.0

    def __getitem__(self, key):
        return _MockTensor(self.data[key])


class _MockNoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _setup_torch_mock(monkeypatch):
    """Install a minimal torch mock in sys.modules."""
    mock_torch = MagicMock()
    mock_torch.from_numpy = lambda x: _MockTensor(x)
    mock_torch.zeros = lambda n: _MockTensor(np.zeros(n, dtype=np.float32))
    mock_torch.cat = lambda tensors: _MockTensor(np.concatenate([t.data for t in tensors]))
    mock_torch.no_grad = _MockNoGrad
    monkeypatch.setitem(sys.modules, "torch", mock_torch)


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

        class MockModel:
            def __call__(self, tensor, sr):
                # Verify the tensor was padded/truncated to 512
                assert tensor.shape[0] == 512, f"Expected 512 samples, got {tensor.shape[0]}"

                class MockResult:
                    def item(self):
                        return 0.75

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        # 1136-sample chunk (typical WASAPI block)
        audio = np.ones(1136, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == 0.75

    def test_compute_vad_prob_handles_small_chunk(self, monkeypatch):
        """compute_vad_prob must pad a 100-sample chunk to 512."""
        import voice_typer.server.vad as vad

        class MockModel:
            def __call__(self, tensor, sr):
                assert tensor.shape[0] == 512

                class MockResult:
                    def item(self):
                        return 0.3

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        audio = np.ones(100, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == 0.3

    def test_compute_vad_prob_handles_exact_512_chunk(self, monkeypatch):
        """compute_vad_prob must work with exactly 512 samples."""
        import voice_typer.server.vad as vad

        class MockModel:
            def __call__(self, tensor, sr):
                assert tensor.shape[0] == 512

                class MockResult:
                    def item(self):
                        return 0.9

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        audio = np.ones(512, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == 0.9


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

        state = {"count": 0, "sizes": []}

        class MockModel:
            def __call__(self, tensor, sr):
                state["count"] += 1
                state["sizes"].append(tensor.shape[0])

                class MockResult:
                    def item(self):
                        return 0.5

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        audio = np.ones(1136, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert state["count"] == 2, f"Expected 2 sub-chunk calls for 1136 samples, got {state['count']}"
        assert state["sizes"] == [512, 512], f"Sub-chunk sizes must be [512, 512], got {state['sizes']}"
        assert prob == 0.5

    def test_long_chunk_takes_max_probability(self, monkeypatch):
        """AUDIO-10: when sub-chunks return different probabilities,
        the MAX is returned (speech is an "any sub-chunk contains it"
        decision — max is more sensitive than mean for short bursts)."""
        import voice_typer.server.vad as vad

        state = {"count": 0}

        class MockModel:
            def __call__(self, tensor, sr):
                state["count"] += 1

                class MockResult:
                    def item(self):
                        # First sub-chunk low, second high — verifies max.
                        return 0.2 if state["count"] == 1 else 0.85

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        # 1024 samples → exactly 2 sub-chunks of 512.
        audio = np.ones(1024, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        # Max of [0.2, 0.85] = 0.85 — speech in the second sub-chunk
        # is detected. Under OLD truncation, prob would be 0.2 (missed).
        assert prob == 0.85, f"Expected max prob 0.85 (speech in 2nd sub-chunk), got {prob}"

    def test_very_long_chunk_processes_all_subchunks(self, monkeypatch):
        """AUDIO-10: a 5120-sample chunk (10× the Silero block size)
        produces exactly 10 model calls — verifies the slicing loop."""
        import voice_typer.server.vad as vad

        state = {"count": 0, "sizes": []}

        class MockModel:
            def __call__(self, tensor, sr):
                state["count"] += 1
                state["sizes"].append(tensor.shape[0])

                class MockResult:
                    def item(self):
                        return 0.6

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        audio = np.ones(5120, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert state["count"] == 10, f"Expected 10 sub-chunk calls for 5120 samples, got {state['count']}"
        assert all(s == 512 for s in state["sizes"]), f"All sub-chunks must be 512 samples, got {state['sizes']}"
        assert prob == 0.6

    def test_long_chunk_with_odd_remainder_drops_remainder(self, monkeypatch):
        """AUDIO-10: a 1500-sample chunk yields 2 sub-chunks of 512
        (1024 samples) + 476-sample remainder dropped (not padded)."""
        import voice_typer.server.vad as vad

        state = {"count": 0, "sizes": []}

        class MockModel:
            def __call__(self, tensor, sr):
                state["count"] += 1
                state["sizes"].append(tensor.shape[0])

                class MockResult:
                    def item(self):
                        return 0.4

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        audio = np.ones(1500, dtype=np.float32) * 0.1
        prob = vad.compute_vad_prob(audio, sample_rate=16000)

        assert state["count"] == 2, f"Expected 2 sub-chunk calls for 1500 samples, got {state['count']}"
        assert state["sizes"] == [512, 512]
        assert prob == 0.4


# ── WR-12: classes moved from tests/test_waveform_bubble.py ──────────
#
# These three classes were originally defined in
# ``tests/test_waveform_bubble.py`` (lines 526-701). They were moved
# here because they test the VAD wrapper module
# (``voice_typer.server.vad``) and the audio-chunk wiring through
# ``RecordingController.on_recorder_rms`` / ``WaveformBubble.update_level``
# — VAD concerns, not waveform-bubble concerns. Keeping them in
# ``test_waveform_bubble.py`` conflated two SUTs in one file. The class
# bodies below are unchanged from their original implementations; only
# their home file has changed.
#
# Note: ``TestWaveformVADGate`` uses a local ``bubble`` fixture (also
# moved from ``test_waveform_bubble.py``) — it is identical to the
# ``bubble`` fixture still defined in that file. A future cleanup
# should hoist both copies into ``tests/conftest.py`` so the fixture
# is shared.


@pytest.fixture
def bubble():
    """WaveformBubble fixture — local copy of the one in
    ``tests/test_waveform_bubble.py`` (moved alongside
    ``TestWaveformVADGate`` in WR-12)."""
    from voice_typer.server.waveform import WaveformBubble

    return WaveformBubble()


# ── T021: Silero VAD integration tests ──────────────────────────────


class TestVADModule:
    """Test the VAD wrapper module (voice_typer.server.vad)."""

    def test_is_available_returns_bool(self):
        """is_available() should return True or False, not raise."""
        from voice_typer.server.vad import is_available

        result = is_available()
        assert isinstance(result, bool)

    def test_compute_vad_prob_without_torch(self, monkeypatch):
        """When torch is not available, compute_vad_prob returns None."""
        from voice_typer.server import vad

        monkeypatch.setitem(__import__("sys").modules, "torch", None)
        vad.reset()
        result = vad.compute_vad_prob(np.zeros(16000, dtype=np.float32))
        assert result is None

    def test_is_speech_fallback_rms(self, monkeypatch):
        """Without VAD, is_speech falls back to RMS energy check.

        VAD-001: Previously this test relied on vad.reset() + a 16000-sample
        chunk erroring through Silero VAD (which requires exactly 512 samples)
        to exercise the RMS fallback. On machines with a cached Silero model,
        VAD-001's pad/truncate fix made the model succeed instead of erroring,
        causing the test to fail. We now properly mock _load_model to return
        (None, None) so the RMS fallback is deterministically exercised
        regardless of whether torch/Silero is installed.
        """
        from voice_typer.server import vad

        vad.reset()
        # Force VAD to be unavailable so RMS fallback is exercised
        monkeypatch.setattr(vad, "_load_model", lambda: (None, None))
        # Silence
        assert vad.is_speech(np.zeros(16000, dtype=np.float32)) is False
        # Loud audio
        assert vad.is_speech(np.full(16000, 0.1, dtype=np.float32)) is True

    def test_is_speech_empty_audio(self):
        """Empty audio chunk should return False."""
        from voice_typer.server.vad import is_speech

        assert is_speech(np.array([], dtype=np.float32)) is False

    def test_reset_clears_model(self):
        """reset() should clear the cached model."""
        from voice_typer.server import vad

        vad.reset()
        assert vad._model is None
        assert vad._utils is None


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


class TestT021ProductionWiring:
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
