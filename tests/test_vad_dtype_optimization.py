"""TY-26: ``vad.py:251, 263`` ``.float()`` always clones data — replaced
with ``.to(torch.float32)`` (no-op when already float32).

``torch.Tensor.float()`` unconditionally returns a NEW tensor with a
CLONED data buffer, even when the dtype is already ``torch.float32``.
``torch.Tensor.to(torch.float32)`` is a no-op (returns the SAME tensor
object) when the dtype already matches. In production the audio chunk
passed to ``compute_vad_prob`` is always float32 (the recorder's
``filtered.ravel()`` is float32 and ``resample_poly(...).astype(
np.float32)`` is float32), so ``.to(torch.float32)`` saves ~2 KB of
allocation + memcpy per chunk at ~16 Hz = ~32 KB/s of needless
allocation + ~80-320 µs/s of CPU.

Test layers
-----------
1. **Source-level guards** (no torch required): ``inspect.getsource``
   assertions verify ``compute_vad_prob`` uses ``.to(torch.float32)``
   and does NOT use ``.float()`` on the audio-tensor paths. These run
   on the Linux sandbox and catch regressions where someone reverts
   the optimization.
2. **Numerical equivalence** (requires real torch): verifies that
   ``.to(torch.float32)`` is bit-identical to ``.float()`` for
   float32 inputs, and that ``compute_vad_prob`` produces the same
   output as before. These are SKIPPED on the Linux sandbox (no
   torch) and run on the CUDA host.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

# ─── Source-level guards (no torch required) ────────────────────────────


class TestVadSourceUsesToFloat32:
    """Source-level guard: ``vad.py`` must use ``.to(torch.float32)``
    and must NOT use ``.float()`` on the audio tensor paths. Catches
    regressions where someone reverts the TY-26 optimization."""

    def test_compute_vad_prob_uses_to_float32(self):
        """``compute_vad_prob`` source must use ``.to(torch.float32)``
        (optionally with ``copy=False`` — DJ-76 optimization)."""
        from voice_typer.server import vad

        src = inspect.getsource(vad.compute_vad_prob)
        # accept either the bare form (.to(torch.float32)) or
        # the copy=False form (.to(torch.float32, copy=False)). The
        # copy=False form is a strict improvement (no-op when dtype
        # already matches, same as the bare form).
        uses_to_float32 = ".to(torch.float32)" in src or ".to(torch.float32, copy=False)" in src
        assert uses_to_float32, (
            "TY-26: compute_vad_prob must use .to(torch.float32) instead "
            "of .float(). The .to() form is a no-op when the dtype "
            "already matches, avoiding a ~2KB memcpy per chunk at 16 Hz."
        )

    def test_compute_vad_prob_does_not_use_from_numpy_dot_float(self):
        """The two specific call sites (originally lines 251 and 263)
        must NOT use ``.float()`` on the ``torch.from_numpy(...)``
        result. We grep the source for the old patterns and assert
        they're gone."""
        from voice_typer.server import vad

        src = inspect.getsource(vad.compute_vad_prob)
        # The two pre- forms that always cloned the data buffer.
        assert "from_numpy(audio_chunk).float()" not in src, (
            "TY-26: compute_vad_prob must not use "
            "torch.from_numpy(audio_chunk).float() — use "
            ".to(torch.float32) instead to avoid the unconditional clone."
        )
        assert "from_numpy(padded).float()" not in src, (
            "TY-26: compute_vad_prob must not use "
            "torch.from_numpy(padded).float() — use .to(torch.float32) "
            "instead to avoid the unconditional clone."
        )

    def test_at_least_two_to_float32_call_sites(self):
        """There must be at least two ``.to(torch.float32)`` call sites
        in ``compute_vad_prob`` — one for the initial tensor
        conversion and one for the reflect-padded short-chunk path.
        Catches a regression where one site is reverted but the other
        is left in place.

        DJ-76: accepts both ``.to(torch.float32)`` and
        ``.to(torch.float32, copy=False)`` forms (the latter is a
        strict improvement — same no-op semantics when the dtype
        already matches)."""
        from voice_typer.server import vad

        src = inspect.getsource(vad.compute_vad_prob)
        # count both the bare form (.to(torch.float32)) and the
        # copy=False form (.to(torch.float32, copy=False)). The substring
        # `.to(torch.float32` (without the closing paren) matches both,
        # so we count occurrences of that prefix instead.
        count = src.count(".to(torch.float32")
        assert count >= 2, (
            f"TY-26: compute_vad_prob must have at least 2 "
            f".to(torch.float32) call sites (initial + reflect-pad). "
            f"Found {count}."
        )


# ─── Numerical equivalence (requires real torch) ────────────────────────

# Skip the entire numerical-equivalence section if torch isn't installed.
# The Linux sandbox doesn't have torch; the CUDA host does.
_HAS_TORCH = importlib.util.find_spec("torch") is not None


@pytest.mark.skipif(not _HAS_TORCH, reason="torch not installed — TY-26 numerical equivalence requires torch")
class TestToFloat32NoOp:
    """``.to(torch.float32)`` is a no-op on an already-float32 tensor,
    whereas ``.float()`` always clones. This is the core invariant
    that makes the TY-26 optimization safe."""

    def test_to_float32_returns_same_object_for_float32_tensor(self):
        """The whole point of TY-26: ``.to(torch.float32)`` returns
        the SAME tensor object (no clone) when the dtype already
        matches. ``.float()`` returns a NEW tensor (clone)."""
        import torch

        t = torch.zeros(512, dtype=torch.float32)
        # ``.to(torch.float32)`` must return the SAME object.
        assert t.to(torch.float32) is t, (
            "TY-26: .to(torch.float32) must be a no-op (return the same "
            "tensor object) when dtype already matches. If this fails, "
            "the optimization is broken — every chunk would still clone."
        )

    def test_float_always_clones_for_float32_tensor(self):
        """Sanity check: ``.float()`` should clone even for float32
        tensors — confirming the distinction that TY-26 exploits. If
        a future torch version changes this, the TY-26 optimization
        may no longer be necessary."""
        import torch

        t = torch.zeros(512, dtype=torch.float32)
        assert t.float() is not t, (
            "Sanity check: .float() should clone for float32 tensors. "
            "If torch changes this, the TY-26 optimization may no longer "
            "be necessary (but reverting to .float() would still be "
            "safe)."
        )

    def test_to_float32_returns_same_object_for_from_numpy(self):
        """``torch.from_numpy(arr).to(torch.float32)`` must be a no-op
        when ``arr`` is already float32 — mirroring the production
        code path in ``compute_vad_prob``."""
        import torch

        arr = np.zeros(512, dtype=np.float32)
        t = torch.from_numpy(arr)
        assert t.dtype == torch.float32
        assert t.to(torch.float32) is t

    def test_to_float32_clones_for_non_float32_input(self):
        """When the input is NOT float32 (e.g. int16 from a legacy
        device), ``.to(torch.float32)`` must produce a new float32
        tensor with correct values. This is the fallback path that
        still works correctly after TY-26."""
        import torch

        arr = np.full(512, 16384, dtype=np.int16)
        t = torch.from_numpy(arr)
        assert t.dtype == torch.int16
        t2 = t.to(torch.float32)
        assert t2 is not t
        assert t2.dtype == torch.float32
        # Values must be preserved (int16 → float32 is lossless).
        assert t2[0].item() == 16384.0


@pytest.mark.skipif(not _HAS_TORCH, reason="torch not installed — TY-26 numerical equivalence requires torch")
class TestComputeVadProbNumericalEquivalence:
    """End-to-end: ``compute_vad_prob`` produces identical output
    before and after the TY-26 change for float32 inputs (the
    production case). Verifies the optimization is non-regressing."""

    def test_compute_vad_prob_exact_fit_float32_input(self):
        """The exact-fit path (n == 512) with a float32 input must
        produce the expected probability — the model sees the same
        tensor it would have under ``.float()`` (bit-identical values,
        same dtype, same shape), so the output is identical."""
        import torch
        from voice_typer.server import vad

        class MockModel:
            def __call__(self, tensor, sr):
                # Silero expects float32.
                assert tensor.dtype == torch.float32, f"Silero VAD expects float32; got {tensor.dtype}"

                class MockResult:
                    def item(self):
                        return float(tensor[0].item())

                return MockResult()

        original_model = vad._model
        original_utils = vad._utils
        vad._model = MockModel()
        vad._utils = None
        try:
            # 512-sample float32 input — the exact-fit path.
            audio = np.full(512, 0.5, dtype=np.float32)
            prob = vad.compute_vad_prob(audio, sample_rate=16000)
            assert prob == pytest.approx(0.5)
        finally:
            vad._model = original_model
            vad._utils = original_utils

    def test_compute_vad_prob_short_chunk_float32_input(self):
        """The short-chunk path (n < 512 → reflect-padded) with a
        float32 input must produce the expected probability. The
        reflect-pad path uses the second ``.to(torch.float32)`` call
        site (originally line 263)."""
        import torch  # noqa: F401
        from voice_typer.server import vad

        class MockModel:
            def __call__(self, tensor, sr):
                assert tensor.dtype == torch.float32
                assert tensor.shape[0] == 512  # reflect-padded to 512

                class MockResult:
                    def item(self):
                        return 0.42

                return MockResult()

        original_model = vad._model
        original_utils = vad._utils
        vad._model = MockModel()
        vad._utils = None
        try:
            # 100-sample input — short, gets reflect-padded to 512.
            audio = np.full(100, 0.5, dtype=np.float32)
            prob = vad.compute_vad_prob(audio, sample_rate=16000)
            assert prob == pytest.approx(0.42)
        finally:
            vad._model = original_model
            vad._utils = original_utils

    def test_compute_vad_prob_long_chunk_float32_input(self):
        """The long-chunk path (n > 512 → sliced into sub-chunks)
        with a float32 input must produce the MAX probability across
        sub-chunks. Verifies slicing works correctly with
        ``.to(torch.float32)`` on the initial tensor."""
        import torch  # noqa: F401
        from voice_typer.server import vad

        call_count = [0]

        class MockModel:
            def __call__(self, tensor, sr):
                assert tensor.dtype == torch.float32
                call_count[0] += 1

                class MockResult:
                    def item(self):
                        # Second sub-chunk returns a higher prob —
                        # verifies MAX is taken.
                        return 0.9 if call_count[0] == 2 else 0.3

                return MockResult()

        original_model = vad._model
        original_utils = vad._utils
        vad._model = MockModel()
        vad._utils = None
        try:
            # 1024-sample input — slices into 2 sub-chunks of 512.
            audio = np.full(1024, 0.5, dtype=np.float32)
            prob = vad.compute_vad_prob(audio, sample_rate=16000)
            assert prob == pytest.approx(0.9)  # MAX of (0.3, 0.9)
            assert call_count[0] == 2
        finally:
            vad._model = original_model
            vad._utils = original_utils

    def test_compute_vad_prob_float32_input_no_extra_clone(self):
        """Verify the no-clone invariant end-to-end: when the input
        is float32, ``torch.from_numpy(arr).to(torch.float32)`` must
        return the SAME tensor (no clone). We patch the model to
        capture the tensor's ``data_ptr()`` and verify it matches the
        numpy array's pointer (i.e. no clone happened).

        This is the heart of TY-26: the model receives a view of the
        ORIGINAL numpy buffer, not a clone. (Under ``.float()`` the
        model would receive a CLONED buffer with a different
        ``data_ptr()``.)"""
        from voice_typer.server import vad

        captured_ptrs: list[int] = []

        class MockModel:
            def __call__(self, tensor, sr):
                # ``data_ptr()`` returns the address of the first
                # element. If ``.to(float32)`` cloned, this would
                # differ from the numpy array's pointer.
                captured_ptrs.append(tensor.data_ptr())

                class MockResult:
                    def item(self):
                        return 0.5

                return MockResult()

        original_model = vad._model
        original_utils = vad._utils
        vad._model = MockModel()
        vad._utils = None
        try:
            audio = np.full(512, 0.5, dtype=np.float32)
            numpy_ptr = audio.ctypes.data
            vad.compute_vad_prob(audio, sample_rate=16000)
            assert len(captured_ptrs) == 1
            # The tensor's data_ptr must equal the numpy array's
            # pointer — proving no clone happened.
            assert captured_ptrs[0] == numpy_ptr, (
                "TY-26: compute_vad_prob must pass the ORIGINAL numpy "
                "buffer to the model (no clone). If this fails, "
                ".to(torch.float32) was reverted to .float() (which "
                "always clones)."
            )
        finally:
            vad._model = original_model
            vad._utils = original_utils


# ─── Mock-torch path (no real torch required) ───────────────────────────
# These tests use a mock torch (mirroring tests/test_vad.py) so they run
# on the Linux sandbox. They verify the code path works end-to-end with
# the .to(float32) call sites, but do NOT verify numerical equivalence
# (that's the torch-required class above).


class _MockTensor:
    """Minimal torch.Tensor mock — mirrors the one in tests/test_vad.py.

    TY-26 note: ``.to(float32)`` returns ``self`` (no-op), mirroring
    real torch's behaviour for same-dtype tensors. ``.float()`` (kept
    for backward compat with any test that still calls it) would
    return a CLONE — but we don't use it in the SUT anymore.
    """

    def __init__(self, data):
        self.data = np.asarray(data, dtype=np.float32)
        self._shape = [len(self.data)]
        self.dtype = "float32"  # mock dtype

    @property
    def shape(self):
        return self._shape

    def dim(self):
        return 1

    def squeeze(self):
        return self

    def to(self, dtype, copy=True):
        # .to(float32) is a no-op for an already-float32 tensor.
        # ``copy=False`` kwarg accepted (real torch supports it).
        return self

    def float(self):
        # Legacy .float() — would clone in real torch. Kept for
        # backward compat with any test that still uses it.
        return _MockTensor(self.data.copy())

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
    """Install a minimal torch mock in sys.modules (mirrors
    tests/test_vad.py)."""
    mock_torch = MagicMock()
    mock_torch.from_numpy = lambda x: _MockTensor(x)
    mock_torch.zeros = lambda n: _MockTensor(np.zeros(n, dtype=np.float32))
    mock_torch.cat = lambda tensors: _MockTensor(np.concatenate([t.data for t in tensors]))
    mock_torch.no_grad = _MockNoGrad
    mock_torch.float32 = "float32"
    monkeypatch.setitem(sys.modules, "torch", mock_torch)


class TestComputeVadProbWithMockTorch:
    """Mock-torch end-to-end tests — verify the code path runs with
    ``.to(torch.float32)`` and produces a sensible result. These run
    on the Linux sandbox (no real torch required)."""

    def test_compute_vad_prob_exact_fit_with_mock_torch(self, monkeypatch):
        """Exact-fit path (n == 512) runs end-to-end with the mock
        torch and the ``.to(torch.float32)`` call sites."""
        import voice_typer.server.vad as vad

        class MockModel:
            def __call__(self, tensor, sr):
                assert tensor.shape[0] == 512

                class MockResult:
                    def item(self):
                        return 0.75

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        audio = np.full(512, 0.5, dtype=np.float32)
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == pytest.approx(0.75)

    def test_compute_vad_prob_short_chunk_with_mock_torch(self, monkeypatch):
        """Short-chunk path (n < 512 → reflect-padded) runs end-to-end
        with the mock torch."""
        import voice_typer.server.vad as vad

        class MockModel:
            def __call__(self, tensor, sr):
                assert tensor.shape[0] == 512  # reflect-padded

                class MockResult:
                    def item(self):
                        return 0.6

                return MockResult()

        monkeypatch.setattr(vad, "_model", MockModel())
        monkeypatch.setattr(vad, "_utils", None)
        _setup_torch_mock(monkeypatch)

        audio = np.full(100, 0.5, dtype=np.float32)
        prob = vad.compute_vad_prob(audio, sample_rate=16000)
        assert prob == pytest.approx(0.6)
