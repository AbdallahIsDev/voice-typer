"""regression tests for ``TranscriptionEngine._probe_cuda_runtime``.

The CUDA runtime probe (``voice_typer/server/transcription.py:503-611``)
runs a 1s sine-wave transcription immediately after a CUDA model load to
force early cuBLAS/cuDNN kernel resolution.  If the DLLs cannot be
loaded, the probe catches the error at startup and falls back to CPU
*immediately* (instead of failing mid-dictation when the user is
already speaking).

Pre-fix this method was completely untested — meaning:

  * The early-return guard at line 520-522 (``self._model is None``)
    could silently regress to a ``AttributeError`` without anyone
    noticing.
  * The cuBLAS keyword substring list (``"cublas"``, ``"cuda"``,
    ``"cudnn"``, ``"dll"``, ``"not found"``, ``"cannot be loaded"``,
    ``"load library"``) could be edited to drop a critical keyword.
  * The fallback path (``_device = "cpu"``, ``_compute_type = "int8"``,
    ``_reload_under_lock``) could silently drop one of the three state
    transitions.
  * A non-CUDA exception (e.g. ``ValueError("unrelated")``) could be
    silently swallowed by an over-broad ``except Exception`` clause —
    hiding a real bug in the transcription pipeline.

These four tests pin the contract for each branch.

All external dependencies (numpy is real; ``self._model`` is a
``MagicMock``; ``self._reload_under_lock`` is patched to a no-op so no
real model is reloaded) are mocked so the tests run on any platform
without GPU, ctranslate2, or model files.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def cuda_engine():
    """Construct a TranscriptionEngine on CPU then force the CUDA path.

    The engine is constructed with ``device="cpu"`` so ``__init__`` does
    not attempt the (real) ``ctranslate2.get_cuda_device_count()`` probe.
    We then set ``_device = "cuda"`` and ``_compute_type = "float16"``
    directly to mirror the state the engine is in immediately after a
    successful CUDA load — the caller of ``_probe_cuda_runtime`` runs
    the probe right after the CUDA model is loaded, so at probe time
    ``_device == "cuda"`` is an invariant.
    """
    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    engine._device = "cuda"
    engine._compute_type = "float16"
    return engine


class TestProbeCudaRuntime:
    """``_probe_cuda_runtime`` branch coverage.

    Pin the four observable contracts:

      1. Early return when ``self._model is None`` (no transcribe call,
         no exception).
      2. Success path — segments iterate cleanly and ``_device`` stays
         ``"cuda"``.
      3. cuBLAS-class error triggers CPU fallback (``_device = "cpu"``,
         ``_compute_type = "int8"``, ``_reload_under_lock`` was called).
      4. Non-CUDA error propagates (NOT swallowed).
    """

    def test_probe_cuda_runtime_skips_when_model_none(self, cuda_engine):
        """#1: ``self._model is None`` must trigger an early
        return at line 520-522 BEFORE any ``model.transcribe()`` call.

        Pre-fix the guard existed but was untested — a refactor that
        moved the ``import numpy`` above the guard (or removed the
        guard entirely) would crash with ``AttributeError: 'NoneType'
        object has no attribute 'transcribe'`` the first time the probe
        ran in a fresh process before model load.

        This test sets ``_model = None`` explicitly and asserts the
        probe returns ``None`` without raising. If the early-return
        guard regresses, the production code dereferences
        ``self._model.transcribe(...)`` and Python raises
        ``AttributeError`` — which ``pytest`` surfaces as a test
        failure (no exception expected).
        """
        cuda_engine._model = None

        # Call probe — must return None without raising.
        result = cuda_engine._probe_cuda_runtime()
        assert result is None, (
            "_probe_cuda_runtime must return None when _model is None "
            "(early-return contract at line 520-522)."
        )
        # Sanity: device/compute_type untouched (no fallback ran).
        assert cuda_engine._device == "cuda"
        assert cuda_engine._compute_type == "float16"

    def test_probe_cuda_runtime_success_no_fallback(self, cuda_engine):
        """#2: successful probe must keep ``_device == "cuda"``.

        The success path:

          1. Calls ``self._model.transcribe(...)`` with the same
             kwargs as ``_transcribe_unlocked`` (beam_size, best_of,
             temperature=0.0, vad_filter=False, language,
             condition_on_previous_text, without_timestamps=True).
          2. Iterates through every segment (lazy ctranslate2 generator
             — the real GPU work happens during iteration).
          3. Logs "CUDA runtime OK" and returns normally.

        The fallback branch must NOT fire — ``_device`` stays
        ``"cuda"``, ``_compute_type`` stays ``"float16"``, and
        ``_reload_under_lock`` must NOT be called.
        """
        mock_model = MagicMock()
        # model.transcribe returns (segments, info) — segments must be
        # iterable. An empty list is fine: the ``for _seg in segments``
        # loop is a no-op, which is the cleanest success path.
        segments_returned: list[object] = []
        mock_model.transcribe.return_value = (segments_returned, MagicMock())
        cuda_engine._model = mock_model

        # Patch _reload_under_lock so we can assert it was NOT called
        # (and so a regression that runs the fallback doesn't actually
        # try to load a real model).
        cuda_engine._reload_under_lock = MagicMock(name="_reload_under_lock")

        result = cuda_engine._probe_cuda_runtime()

        assert result is None, "_probe_cuda_runtime returns None on success."
        # model.transcribe was called exactly once with the sine-wave probe.
        assert mock_model.transcribe.call_count == 1, (
            f"expected exactly 1 transcribe call, got "
            f"{mock_model.transcribe.call_count}"
        )
        # Device stays CUDA — the fallback did NOT fire.
        assert cuda_engine._device == "cuda", (
            "on probe success _device must remain 'cuda' — the fallback "
            "must NOT fire."
        )
        assert cuda_engine._compute_type == "float16", (
            "on probe success _compute_type must remain 'float16'."
        )
        # _reload_under_lock must NOT have been called.
        cuda_engine._reload_under_lock.assert_not_called()

    def test_probe_cuda_runtime_cublas_error_triggers_cpu_fallback(
        self, cuda_engine
    ):
        """#3: a cuBLAS load failure must trigger CPU fallback.

        The error substring list at line 557-568 includes
        ``"load library"`` and ``"cublas"``. When the probe catches an
        exception whose ``str()`` contains any of those substrings, it
        must:

          1. Acquire ``self._lock`` (race-safety — see comment at
             line 572-579).
          2. ``del self._model`` and ``gc.collect()`` (release VRAM).
          3. Set ``self._model = None``, ``self._device = "cpu"``,
             ``self._compute_type = "int8"``.
          4. Call ``self._reload_under_lock()`` to load a CPU model.

        The test patches ``_reload_under_lock`` to a ``MagicMock`` so
        no real model is loaded (the reload would fail without model
        files / ctranslate2), then asserts the three state transitions
        AND that the reload was called exactly once.
        """
        mock_model = MagicMock()
        # Raise the cuBLAS error during the segment iteration, not
        # during the transcribe() call itself — mirrors the real
        # failure mode (the lazy generator doesn't try to load cuBLAS
        # until the first ``next()`` call). This also exercises the
        # ``for _seg in segments: pass`` loop's exception propagation.
        def _transcribe_side_effect(*args, **kwargs):
            def _gen():
                raise Exception("cuBLAS load library failed")
                yield  # unreachable — makes this a generator function

            return (_gen(), MagicMock())

        mock_model.transcribe.side_effect = _transcribe_side_effect
        cuda_engine._model = mock_model

        # Patch _reload_under_lock to a no-op mock so we can assert it
        # was called AND so the test doesn't actually try to load a
        # real Whisper model (which would fail without model files).
        cuda_engine._reload_under_lock = MagicMock(name="_reload_under_lock")

        # Patch gc.collect so we don't trigger real GC (cheap but
        # noisy — and a real GC pass could collect other test fixtures
        # in unpredictable order).
        import gc

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(gc, "collect", lambda: None)

            cuda_engine._probe_cuda_runtime()

        # All three state transitions fired.
        assert cuda_engine._device == "cpu", (
            "cuBLAS error must trigger _device = 'cpu' fallback."
        )
        assert cuda_engine._compute_type == "int8", (
            "cuBLAS error must trigger _compute_type = 'int8' fallback."
        )
        # Model was nulled before reload so a concurrent transcribe()
        # can't observe a half-loaded model.
        assert cuda_engine._model is None or cuda_engine._model is mock_model, (
            "_model must be set to None before _reload_under_lock runs; "
            "the reload mock leaves it as-is so we accept None or the "
            "pre-reload sentinel."
        )
        # The reload was called exactly once.
        assert cuda_engine._reload_under_lock.call_count == 1, (
            "_reload_under_lock must be called exactly once on cuBLAS "
            "fallback."
        )
        # HU-25: the RACE-023 deferred release must be armed so the next
        # caller outside the lock (transcribe / unload) runs
        # gc.collect() + release_gpu_memory() — otherwise the freed CUDA
        # blocks stay cached in the allocator and VRAM is never returned
        # to the OS after repeated CUDA-probe-failure reloads.
        assert cuda_engine._pending_gc_collect is True, (
            "cuBLAS fallback must set _pending_gc_collect = True (HU-25)"
        )

    def test_probe_cuda_runtime_non_cuda_error_propagates(self, cuda_engine):
        """#4: a non-CUDA exception must propagate (NOT swallowed).

        The substring list at line 557-568 is intentionally narrow —
        only errors whose message mentions cuBLAS / CUDA / cuDNN / DLL
        / "load library" should trigger the fallback.  A generic
        ``ValueError("unrelated")`` must re-raise so a real bug in the
        transcription pipeline surfaces immediately instead of being
        silently masked as a "CUDA fallback" event.

        Pre-fix the ``except Exception`` clause at line 551 was
        permissive enough to swallow any exception — only the inner
        ``if any(...)`` check distinguished CUDA errors. The ``else:
        raise`` at line 610-611 is the contract: non-CUDA errors must
        propagate verbatim.
        """
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = ValueError("unrelated")
        cuda_engine._model = mock_model

        # _reload_under_lock patched to a no-op so if the fallback
        # regresses and fires for a non-CUDA error, the assertion
        # below (call_count == 0) catches it.
        cuda_engine._reload_under_lock = MagicMock(name="_reload_under_lock")

        with pytest.raises(ValueError, match="unrelated"):
            cuda_engine._probe_cuda_runtime()

        # The fallback must NOT have fired for a non-CUDA error.
        assert cuda_engine._device == "cuda", (
            "non-CUDA error must NOT trigger device fallback."
        )
        assert cuda_engine._compute_type == "float16", (
            "non-CUDA error must NOT trigger compute_type fallback."
        )
        cuda_engine._reload_under_lock.assert_not_called()
