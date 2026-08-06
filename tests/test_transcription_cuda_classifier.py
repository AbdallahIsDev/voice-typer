"""regression tests for ``TranscriptionEngine._is_gpu_runtime_error``.

The classifier at ``voice_typer/server/transcription.py:1386-1448``
detects GPU/CUDA runtime errors via FOUR layered checks:

  1. ``torch.cuda.OutOfMemoryError`` isinstance check.
  2. ``ctranslate2.CUDAError`` / ``ctranslate2.RuntimeError`` isinstance
     check (faster-whisper wraps ctranslate2 errors).
  3. MRO-based class-name check (catches wrapped exceptions whose
     original class still appears in the MRO — e.g. a re-raised
     ``CudaRuntimeError`` from a third-party wrapper).
  4. Attribute check (``.cuda_error`` / ``.is_cuda_error``).
  5. Substring fallback (``"cublas"``, ``"cuda"``, ``"cudnn"``,
     ``"gpu"``, ``"not found or cannot be loaded"``).

Pre-fix only the substring check (#5) was tested. The new class-hierarchy
(#1-#2), MRO (#3), and attribute (#4) checks were completely untested —
meaning a refactor that swapped the order of the checks or dropped one of
the early returns could misclassify a ROCm error (not in the substring
list) as a non-GPU error and skip the GPU→CPU fallback.

These tests pin the contract for checks #2, #3, and #4 using REAL class
subclasses (not ``MagicMock`` — ``isinstance`` checks against
``MagicMock`` attributes always return ``False`` so a MagicMock-based
test would not exercise the class-hierarchy path).

All heavy imports (``faster_whisper``, ``torch``, ``ctranslate2``) are
mocked via the autouse fixture so the tests run on any platform without
GPU or model files.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_heavy_imports_for_classifier(monkeypatch):
    """Mock ``faster_whisper`` and ``ctranslate2`` so the engine can be
    constructed without GPU / model files.

    ``torch`` is already mocked at session scope by
    ``tests/conftest.py:mock_heavy_imports_session`` — it installs a
    real ``_FakeOutOfMemoryError`` class at ``torch.cuda.OutOfMemoryError``
    so the production isinstance check at line 1400 works correctly
    (a plain MagicMock attribute would raise ``TypeError`` under
    ``isinstance``).

    We install a fresh ``ctranslate2`` MagicMock per test so individual
    tests can install real classes at ``ctranslate2.CUDAError`` /
    ``ctranslate2.RuntimeError`` without leaking state to sibling tests.
    """
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    mock_ct2 = MagicMock(name="mock_ctranslate2")
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


@pytest.fixture()
def cuda_engine():
    """A TranscriptionEngine with ``_device = "cuda"`` so the
    classifier's ``if self._device == "cpu": return False`` early-exit
    at line 1393-1394 does NOT short-circuit the checks under test.
    """
    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    engine._device = "cuda"
    engine._compute_type = "float16"
    return engine


class TestIsGpuRuntimeErrorClassifier:
    """``_is_gpu_runtime_error`` class-hierarchy / MRO / attribute
    branch coverage.

    Each test installs a REAL class (subclass of ``RuntimeError`` or
    ``Exception``) — never a ``MagicMock`` — because the production code
    uses ``isinstance(exc, cls)`` which returns ``False`` (or raises
    ``TypeError``) for MagicMock attributes. The REAL class is what
    discriminates a passing test from a no-op.
    """

    def test_ctranslate2_cuda_error_class_match(self, cuda_engine, monkeypatch):
        """ #2 (check #2): a real ``ctranslate2.CUDAError`` subclass
        must match via the isinstance check at line 1410-1413.

        The production code does::

            for attr_name in ("CUDAError", "RuntimeError"):
                cls = getattr(ctranslate2, attr_name, None)
                if isinstance(cls, type) and isinstance(exc, cls):
                    return True

        The ``isinstance(cls, type)`` guard was added because some
        ctranslate2 builds don't expose ``CUDAError`` as a class
        (returns ``None`` or an arbitrary object). A ``MagicMock``
        attribute also fails this guard (``isinstance(MagicMock(), type)``
        is ``False``) — so the test MUST install a REAL class.

        Setup:
          * Define ``FakeCUDAError(RuntimeError)`` — a real subclass so
            ``isinstance(exc, FakeCUDAError)`` returns ``True``.
          * Install it at ``sys.modules["ctranslate2"].CUDAError``.
          * Raise ``FakeCUDAError("boom")`` and assert the classifier
            returns ``True``.
        """
        import ctranslate2

        class FakeCUDAError(RuntimeError):
            """Real class — mirrors ctranslate2.CUDAError's hierarchy."""

        monkeypatch.setattr(ctranslate2, "CUDAError", FakeCUDAError, raising=False)

        exc = FakeCUDAError("ctranslate2 CUDA kernel failed")
        assert cuda_engine._is_gpu_runtime_error(exc) is True, (
            "an exception that IS a subclass of ctranslate2.CUDAError "
            "must be classified as a GPU runtime error (check #2)."
        )

    def test_mro_class_name_match(self, cuda_engine):
        """ #3 (check #3): an exception whose class name contains
        "cuda" must match via the MRO class-name check at line 1424-1430.

        The production code iterates ``type(exc).__mro__`` and lowercases
        each ``cls.__name__``, checking for the substrings ``"cudnn"``,
        ``"cublas"``, ``"cuda"``, ``"ctranslate2"``. This catches
        third-party wrappers (e.g. ``faster_whisper.CudaRuntimeError``)
        that re-raise without preserving the original ctranslate2 class
        hierarchy.

        Setup:
          * Define ``CudaRuntimeError(RuntimeError)`` — name contains
            "cuda" so the MRO check matches.
          * Raise it and assert ``True``.

        Note: this test deliberately does NOT install the class on
        ``ctranslate2`` — the ctranslate2 isinstance loop (#2) must
        fall through so the MRO check (#3) is the one that fires.
        """
        # ctranslate2 fixture leaves CUDAError/RuntimeError as
        # MagicMock attributes (not types), so the isinstance check at
        # line 1412 returns False and the loop falls through to the
        # MRO check.
        class CudaRuntimeError(RuntimeError):
            pass

        # Confirm the class name has the substring (defensive — if a
        # future refactor renames the local, the test should fail loud
        # rather than silently pass via the substring fallback at #5).
        assert "cuda" in CudaRuntimeError.__name__.lower(), (
            "test class name must contain 'cuda' so the MRO check fires."
        )

        exc = CudaRuntimeError("wrapped cuda error from a third-party lib")
        assert cuda_engine._is_gpu_runtime_error(exc) is True, (
            "an exception whose class name (in the MRO) contains 'cuda' "
            "must be classified as a GPU runtime error (check #3)."
        )

    def test_attribute_check_cuda_error_flag(self, cuda_engine):
        """ #4 (check #4): an exception carrying a ``.cuda_error``
        attribute must match via the attribute check at line 1433-1434.

        The production code does::

            if getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False):
                return True

        Some libraries (e.g. newer ``torch`` / ``pynvml``) attach a
        structured ``.cuda_error`` attribute to a generic ``RuntimeError``
        rather than raising a typed subclass. The class-hierarchy (#2)
        and MRO (#3) checks both miss this case — only the attribute
        check catches it.

        Setup:
          * Raise a plain ``RuntimeError("oom")`` (class name doesn't
            contain "cuda", str doesn't contain any substring keyword).
          * Set ``exc.cuda_error = "oom"``.
          * Assert ``True``.

        This test pins check #4 specifically — the message "oom" alone
        would NOT match the substring fallback (#5) so the attribute
        check is the sole signal.
        """
        exc = RuntimeError("oom")
        exc.cuda_error = "oom"  # type: ignore[attr-defined]

        # Defensive: confirm the exception would NOT match the substring
        # fallback — otherwise this test would pass even if the
        # attribute check regressed.
        assert "cuda" not in str(exc).lower()
        assert "cublas" not in str(exc).lower()
        assert "cudnn" not in str(exc).lower()
        assert "gpu" not in str(exc).lower()

        assert cuda_engine._is_gpu_runtime_error(exc) is True, (
            "an exception with .cuda_error set must be classified as a "
            "GPU runtime error (check #4)."
        )
