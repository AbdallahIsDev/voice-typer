import numpy as np
import sys
from unittest.mock import MagicMock


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
    mock_torch.cat = lambda tensors: _MockTensor(
        np.concatenate([t.data for t in tensors])
    )
    mock_torch.no_grad = _MockNoGrad
    monkeypatch.setitem(sys.modules, "torch", mock_torch)


class TestVad001ChunkSizeHandling:
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
                assert tensor.shape[0] == 512, (
                    f"Expected 512 samples, got {tensor.shape[0]}"
                )
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
