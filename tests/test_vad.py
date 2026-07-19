import sys
from unittest.mock import MagicMock

import numpy as np


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
