"""GPU→CPU fallback + error-classification helpers for ``TranscriptionEngine``.

Extracted from ``voice_typer/server/transcription.py`` (which stays the
public facade and keeps thin one-line delegator methods on the engine
class) so the fallback policy can be unit-tested in isolation:

* :func:`with_gpu_fallback` — the GPU→CPU teardown/reload/retry
  orchestration shared by the batch and streaming transcribe paths.
* :func:`is_gpu_runtime_error` — the layered GPU/CUDA runtime-error
  classifier (class hierarchy → MRO → attributes → substring).
* :func:`transcribe_with_fallback` — the public transcribe wrapper that
  pairs the inference-counter lock pattern with the fallback chain and
  the deferred-gc cleanup.

TEST PATCH COMPATIBILITY
------------------------
All engine-coupled state is dispatched through the ENGINE object
(``engine._is_gpu_runtime_error(...)`` /
``engine._transcribe_with_fallback_unlocked(...)`` /
``engine._run_deferred_gc()`` / ``engine._apply_auto_beam_size()`` /
``engine._reload_under_lock()``) so instance- and class-level
monkeypatches keep taking effect. The lock/GC choreography helpers
themselves (``_run_deferred_gc``, ``_with_lock_and_deferred_gc``) stay
in ``transcription.py`` — they are lock-coupled to the engine.
"""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server.asr_utils import is_oom_error

# Use the ``transcription`` logger name so log records emitted from this
# extracted module are captured by tests that filter by
# ``logger="voice_typer.server.transcription"`` (the historical logger
# name when this code lived inline in ``transcription.py``).
log = logging.getLogger("voice_typer.server.transcription")


def with_gpu_fallback(engine, inner, audio, *args, **kwargs):
    """Run ``inner(audio, *args, **kwargs)`` with GPU→CPU fallback.

    Extracts the duplicate teardown sequence shared by the batch path
    (``_transcribe_with_fallback_unlocked``) and the streaming path
    (``_transcribe_words_with_fallback_unlocked``).

    On a GPU runtime error (per ``engine._is_gpu_runtime_error``):
      1. Drop the GPU model reference (``del engine._model`` + set None).
      2. Switch device/compute to CPU/int8.
      3. Reload the model on CPU via ``engine._reload_under_lock()``.
      4. Set ``engine._pending_gc_collect = True`` so the caller runs
         ``gc.collect()`` + ``release_gpu_memory()`` AFTER releasing
         the lock.
      5. Retry ``inner(audio, *args, **kwargs)`` once on CPU.

    Non-GPU errors are re-raised unchanged. On a CPU device the
    GPU-error classifier short-circuits at the top of
    ``is_gpu_runtime_error`` (returns False), so the fallback never
    fires — the original exception propagates.
    """
    try:
        return inner(audio, *args, **kwargs)
    except Exception as first_err:
        if not engine._is_gpu_runtime_error(first_err):
            raise

        log.warning(
            "GPU transcription failed (%s), falling back to CPU",
            first_err,
        )
        # Surface a user-facing notification BEFORE the synchronous CPU
        # reload below freezes this thread for 5-50s. Mirrors the
        # parakeet engine's ``parakeet_cpu_fallback`` event contract
        # (same payload shape); consumed in-process by
        # tray_notifications.on_gpu_cpu_fallback. Best-effort: a publish
        # failure must never break the fallback itself.
        with contextlib.suppress(Exception):
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "gpu_cpu_fallback",
                    "data": {"device": "cpu", "reason": str(first_err)[:200]},
                }
            )
        # Tear down GPU model, reload on CPU.
        # gc.collect() and release_gpu_memory() are deferred outside
        # the lock via ``engine._pending_gc_collect`` (the caller's
        # ``_with_lock_and_deferred_gc`` or ``_run_deferred_gc`` call
        # fires them after the lock is released). Calling
        # ``release_gpu_memory()`` here would be a no-op — the
        # ctranslate2 model still holds the CUDA context until
        # ``del engine._model`` runs below.
        with contextlib.suppress(Exception):
            del engine._model
        engine._model = None
        engine._device = "cpu"
        engine._compute_type = "int8"
        # CPU decode is the slow path — drop back to the snappy
        # greedy beam when the width was on auto.
        engine._apply_auto_beam_size()
        engine._reload_under_lock()
        engine._pending_gc_collect = True
        return inner(audio, *args, **kwargs)


def is_gpu_runtime_error(engine, exc: Exception) -> bool:
    """detect GPU/CUDA runtime errors via class hierarchy +
    attribute checks first, falling back to substring matching
    only for wrapped/re-raised errors. Previously the substring
    list was the primary check, misclassifying new error classes
    (e.g. ROCm) and triggering wrong fallbacks.

    The ``isinstance(exc, torch.cuda.OutOfMemoryError)`` check was
    replaced with :func:`is_oom_error` (shared ASR utility) so this
    module no longer imports ``torch``. The OOM classifier is kept
    separate from the CUDA classifier
    (:func:`voice_typer.server.asr_utils.is_cuda_error`) because
    ``"out of memory"`` alone is too broad — it matches CPU RAM
    exhaustion which should NOT trigger the GPU→CPU fallback.
    """
    if engine._device == "cpu":
        return False
    # 1. OOM check (replaces torch.cuda.OutOfMemoryError isinstance).
    #    ``is_oom_error`` is the shared classifier in
    #    ``voice_typer.server.asr_utils`` — kept separate from the
    #    CUDA classifier so CPU RAM exhaustion does not false-positive.
    if is_oom_error(exc):
        return True
    # ctranslate2 errors (faster-whisper wraps these)
    try:
        import ctranslate2

        # Some ctranslate2 builds don't expose CUDAError as a class.
        # Guard with isinstance check on the attribute type.
        for attr_name in ("CUDAError", "RuntimeError"):
            cls = getattr(ctranslate2, attr_name, None)
            if isinstance(cls, type) and isinstance(exc, cls):
                return True
    except (ImportError, AttributeError, ValueError):
        # ImportError: ctranslate2 not installed.
        # AttributeError: ctranslate2 installed but missing CUDAError/RuntimeError attrs.
        # ValueError: ctranslate2's import chain (transformers → PIL vision check)
        #   can raise ValueError("PIL.__spec__ is not set") in environments where
        #   PIL was imported via a non-standard path. This is environment noise,
        #   not a real GPU error — fall through to the substring/MRO check below.
        pass
    # 2. MRO-based class-name check (catches wrapped exceptions
    #    whose original class still appears in the MRO).
    for cls in type(exc).__mro__:
        cls_name = cls.__name__.lower()
        if any(kw in cls_name for kw in ["cudnn", "cublas", "cuda", "ctranslate2"]):
            return True
        cls_module = getattr(cls, "__module__", "") or ""
        if any(kw in cls_module.lower() for kw in ["ctranslate2", "cudnn", "cublas"]):
            return True
    # 3. Attribute check: some libraries attach a `.cuda_error`
    #    or `.device` attribute to runtime errors.
    if getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False):
        return True
    # 4. Fallback to string matching for re-raised / wrapped errors
    #    where the original class info is lost. This is a last
    #    resort, not the primary signal.
    error_str = str(exc).lower()
    return any(
        kw in error_str
        for kw in [
            "cublas",
            "cuda",
            "cudnn",
            "gpu",
            "not found or cannot be loaded",
        ]
    )


def transcribe_with_fallback(engine, audio, audio_stats: tuple[float, float, float] | None = None) -> str:
    """Transcribe with automatic CPU fallback on GPU runtime errors.

    If the first attempt fails with a CUDA/cuBLAS/runtime error and
    the model was loaded on GPU, reload on CPU and retry once.

    ``audio_stats`` is an optional pre-computed
    ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
    When provided, the engine skips its own stats computation.

    The lock is released during the segment-decoding loop (mirrors
    ``transcribe()``). ``unload()`` waits on ``_inference_cond``
    for ``_active_inference == 0`` before nulling the model, so a
    stuck backend can be torn down without waiting for the full
    segment loop to complete.
    """
    with engine._lock:
        if engine._model is None:
            raise RuntimeError("Model not loaded. Call load() first.")
        if len(audio) == 0:
            return ""
        engine._active_inference += 1
    try:
        result = engine._transcribe_with_fallback_unlocked(audio, audio_stats=audio_stats)
    finally:
        with engine._inference_cond:
            engine._active_inference -= 1
            if engine._active_inference == 0:
                engine._inference_cond.notify_all()
    # perform deferred gc.collect() OUTSIDE the lock.
    # ``transcribe_with_fallback`` uses the inference-counter pattern
    # (lock released during transcription) so it can't use
    # ``_with_lock_and_deferred_gc`` directly — call the shared
    # helper instead.
    engine._run_deferred_gc()
    return result
