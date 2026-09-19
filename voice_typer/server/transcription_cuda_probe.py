"""CUDA availability probe."""

from __future__ import annotations

import logging
import time

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE as _WHISPER_SAMPLE_RATE
from voice_typer.server.duration import format_duration

# Use the ``transcription`` logger name so log records emitted from this
log = logging.getLogger("voice_typer.server.transcription")


def probe_cuda_runtime(engine, progress_callback=None):
    """Probe CUDA with a real transcription to force early cuBLAS/cuDNN loading."""
    #  (pyrefly): ``engine._model`` is declared ``engine._model = None``
    if engine._model is None:
        log.warning("[CUDA-PROBE] Skipping, no model loaded")
        return
    import numpy as np

    t = np.arange(int(_WHISPER_SAMPLE_RATE), dtype=np.float32) / _WHISPER_SAMPLE_RATE
    probe_audio: np.ndarray = np.sin(2 * np.pi * 440 * t, dtype=np.float32) * 0.1

    log.info("[CUDA-PROBE] Running CUDA runtime smoke test (1s sine wave)...")
    if progress_callback:
        progress_callback("Running CUDA runtime probe...")
    try:
        # Must exercise the same cuBLAS kernels as real dictation.
        segments, info = engine._model.transcribe(
            probe_audio,
            beam_size=engine.beam_size,
            temperature=0.0,
            vad_filter=False,
            language=engine.language,
            condition_on_previous_text=engine.condition_on_previous_text,
            without_timestamps=True,
        )
        # Force iteration through ALL segments, model.transcribe()
        for _seg in segments:
            pass
        log.info("[CUDA-PROBE] CUDA runtime OK, cuBLAS/cuDNN loaded successfully")
    except Exception as exc:
        error_str = str(exc)
        log.warning(
            "[CUDA-PROBE] CUDA runtime probe FAILED: %s",
            error_str,
        )
        if any(
            kw in error_str.lower()
            for kw in [
                "cublas",
                "cuda",
                "cudnn",
                "dll",
                "not found",
                "cannot be loaded",
                "load library",
            ]
        ):
            log.warning(
                "[CUDA-PROBE] cuBLAS/cuDNN runtime error detected, falling back to CPU immediately",
            )
            # wrap the null-and-reload sequence in
            with engine._lock:
                try:
                    del engine._model
                    import gc

                    gc.collect()
                    # ``del engine._model`` + ``gc.collect()`` trigger
                except Exception:
                    log.debug("[MODEL] GPU model teardown failed", exc_info=True)
                engine._model = None
                engine._device = "cpu"
                engine._compute_type = "int8"
                # CPU decode is the slow path, drop back to the
                engine._apply_auto_beam_size()
                # Arm the deferred GPU release BEFORE the reload so a
                engine._pending_gc_collect = True
                engine._reload_under_lock()
                log.warning(
                    "[CUDA-PROBE] Model reloaded on CPU after CUDA probe failure. Loaded via: %s",
                    engine.loaded_via,
                )
        else:
            raise


def warm_up_model(engine) -> None:
    """Run a warm-up inference with silence to prime CUDA kernels."""
    if engine._model is None or engine._device != "cuda":
        return
    try:
        import numpy as np

        # C-LOG-2: the warm-up completion line below carries the
        _t0 = time.perf_counter()
        warmup_audio = np.zeros(int(_WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
        # Same ``best_of`` reasoning as the probe above: a no-op under the
        segments, _ = engine._model.transcribe(
            warmup_audio,
            beam_size=1,
            temperature=0.0,
            vad_filter=False,
            language=engine.language,
            without_timestamps=True,
        )
        # Force iteration to complete the warm-up
        for _ in segments:
            pass
        # C-LOG-2: ``format_duration`` returns the suffix WITH its
        log.info(
            "[PERF] Warm-up inference completed. CUDA kernels primed%s",
            format_duration(time.perf_counter() - _t0),
        )
    except Exception as exc:
        # Warm-up failure is non-critical, log and continue
        log.debug("[PERF] Warm-up inference skipped: %s", exc)
