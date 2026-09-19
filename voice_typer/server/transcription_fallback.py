"""Transcription fallback engines."""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server.asr_utils import is_oom_error

# Use the ``transcription`` logger name so log records emitted from this
log = logging.getLogger("voice_typer.server.transcription")


def with_gpu_fallback(engine, inner, audio, *args, **kwargs):
    """Run ``inner(audio, *args, **kwargs)`` with GPU→CPU fallback."""
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
        with contextlib.suppress(Exception):
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "gpu_cpu_fallback",
                    "data": {"device": "cpu", "reason": str(first_err)[:200]},
                }
            )
        # Tear down GPU model, reload on CPU.
        with contextlib.suppress(Exception):
            del engine._model
        engine._model = None
        engine._device = "cpu"
        engine._compute_type = "int8"
        # CPU decode is the slow path, drop back to the snappy
        engine._apply_auto_beam_size()
        engine._reload_under_lock()
        engine._pending_gc_collect = True
        return inner(audio, *args, **kwargs)


def is_gpu_runtime_error(engine, exc: Exception) -> bool:
    """detect GPU/CUDA runtime errors via class hierarchy +"""
    if engine._device == "cpu":
        return False
    # 1. OOM check (replaces the typed CUDA OOM isinstance check).
    if is_oom_error(exc):
        return True
    # ctranslate2 errors (faster-whisper wraps these)
    try:
        import ctranslate2

        # Some ctranslate2 builds don't expose CUDAError as a class.
        for attr_name in ("CUDAError", "RuntimeError"):
            cls = getattr(ctranslate2, attr_name, None)
            if isinstance(cls, type) and isinstance(exc, cls):
                return True
    except (ImportError, AttributeError, ValueError):
        # ImportError: ctranslate2 not installed.
        pass
    # 2. MRO-based class-name check (catches wrapped exceptions
    for cls in type(exc).__mro__:
        cls_name = cls.__name__.lower()
        if any(kw in cls_name for kw in ["cudnn", "cublas", "cuda", "ctranslate2"]):
            return True
        cls_module = getattr(cls, "__module__", "") or ""
        if any(kw in cls_module.lower() for kw in ["ctranslate2", "cudnn", "cublas"]):
            return True
    # 3. Attribute check: some libraries attach a `.cuda_error`
    if getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False):
        return True
    # 4. Fallback to string matching for re-raised / wrapped errors
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
    """Transcribe with automatic CPU fallback on GPU runtime errors."""
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
    engine._run_deferred_gc()
    return result
