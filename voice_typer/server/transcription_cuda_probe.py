"""CUDA runtime probe + kernel warm-up for ``TranscriptionEngine``.

Extracted from ``voice_typer/server/transcription.py`` (which stays the
public facade and keeps thin one-line delegator methods on the engine
class) so the CUDA smoke-test concern can be unit-tested in isolation:

* :func:`probe_cuda_runtime` — force-loads cuBLAS/cuDNN with a 1s sine
  wave so DLL failures surface at startup (with a clean CPU fallback),
  not mid-recording.
* :func:`warm_up_model` — runs a 0.5s silence inference after load so
  the first real dictation doesn't pay the CUDA kernel-compilation cost.

TEST PATCH COMPATIBILITY
------------------------
The CPU-fallback branch dispatches through the ENGINE object
(``engine._apply_auto_beam_size()`` / ``engine._reload_under_lock()``)
so tests that monkeypatch those as instance attributes (e.g.
``engine._reload_under_lock = MagicMock()``) keep taking effect. The
RACE-023 deferred release is armed via ``engine._pending_gc_collect =
True`` — the actual ``release_gpu_memory()`` call runs later, outside
the lock, in ``TranscriptionEngine._run_deferred_gc`` (which stays in
``transcription.py``).
"""

from __future__ import annotations

import logging
import time

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE as _WHISPER_SAMPLE_RATE
from voice_typer.server.duration import format_duration

# Use the ``transcription`` logger name so log records emitted from this
# extracted module are captured by tests that filter by
# ``logger="voice_typer.server.transcription"`` (the historical logger
# name when this code lived inline in ``transcription.py``).
log = logging.getLogger("voice_typer.server.transcription")


def probe_cuda_runtime(engine, progress_callback=None):
    """Probe CUDA with a real transcription to force early cuBLAS/cuDNN loading.

    Uses a 1s sine-wave tone and the exact same parameters as
    ``_transcribe_unlocked`` — including ``vad_filter=True`` and
    ``without_timestamps=True`` — then **iterates every segment** so
    the underlying cuBLAS kernels are actually resolved.  If the DLLs
    can't be loaded, catches the error at startup and falls back to
    CPU immediately instead of failing mid-recording.
    """
    #  (pyrefly): ``engine._model`` is declared ``engine._model = None``
    # in __init__ and only assigned a real model instance inside the
    # load path. The sole caller only invokes us after a successful
    # load, but pyrefly cannot prove that contract across method
    # boundaries — so guard explicitly. Returning early here also
    # makes the function safe to call from tests / future callers
    # that haven't loaded a model yet.
    if engine._model is None:
        log.warning("[CUDA-PROBE] Skipping — no model loaded")
        return
    import numpy as np

    t = np.arange(int(_WHISPER_SAMPLE_RATE), dtype=np.float32) / _WHISPER_SAMPLE_RATE
    probe_audio: np.ndarray = np.sin(2 * np.pi * 440 * t, dtype=np.float32) * 0.1

    log.info("[CUDA-PROBE] Running CUDA runtime smoke test (1s sine wave)...")
    if progress_callback:
        progress_callback("Running CUDA runtime probe...")
    try:
        # Must exercise the same cuBLAS kernels as real dictation.
        # NOTE: vad_filter=False is deliberate — VAD would reject a
        # sine wave as non-speech, causing Whisper to be skipped.
        # ``best_of`` is deliberately NOT passed: faster-whisper only
        # honors it when sampling with non-zero temperature, so under
        # the pinned ``temperature=0.0`` it was a silent no-op.
        segments, info = engine._model.transcribe(
            probe_audio,
            beam_size=engine.beam_size,
            temperature=0.0,
            vad_filter=False,
            language=engine.language,
            condition_on_previous_text=engine.condition_on_previous_text,
            without_timestamps=True,
        )
        # Force iteration through ALL segments — model.transcribe()
        # returns lazily; the real GPU work (and DLL loading) happens
        # here.
        for _seg in segments:
            pass
        log.info("[CUDA-PROBE] CUDA runtime OK — cuBLAS/cuDNN loaded successfully")
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
                "[CUDA-PROBE] cuBLAS/cuDNN runtime error detected — falling back to CPU immediately",
            )
            # wrap the null-and-reload sequence in
            # ``with engine._lock:`` so a concurrent ``transcribe()`` from
            # another thread can't observe ``engine._model = None`` (or
            # worse, a half-loaded model mid-reload). Pre-fix, the probe
            # ran unlocked after the outer ``with engine._lock:`` block
            # had exited, leaving a race window where ``transcribe()``
            # would raise ``RuntimeError("Model not loaded")`` or see a
            # partially-initialized model.
            with engine._lock:
                try:
                    del engine._model
                    import gc

                    gc.collect()
                    # ``del engine._model`` + ``gc.collect()`` trigger
                    # PyTorch's __del__ hook which releases the parameter
                    # tensors' CUDA blocks, but the caching allocator
                    # keeps them until ``release_gpu_memory()`` runs.
                    # That call is deferred OUTSIDE this lock via the
                    # ``_pending_gc_collect`` flag — it is set EXPLICITLY
                    # in this branch, because ``_reload_under_lock()``
                    # does NOT set it (only ``_with_gpu_fallback`` does,
                    # and this CUDA-probe path is separate). Calling
                    # release_gpu_memory() inside this lock was a no-op
                    # for VRAM release + cost ~10-100ms of sync work
                    # (empty_cache blocks the calling thread while it
                    # iterates the allocator) holding the IPC dispatch
                    # lock for no benefit.
                except Exception:
                    log.debug("[MODEL] GPU model teardown failed", exc_info=True)
                engine._model = None
                engine._device = "cpu"
                engine._compute_type = "int8"
                # CPU decode is the slow path — drop back to the
                # snappy greedy beam when the width was on auto.
                engine._apply_auto_beam_size()
                # Arm the deferred GPU release BEFORE the reload so a
                # reload failure (model missing / ctranslate2 error)
                # can't leak the already-freed CUDA blocks — the next
                # caller outside the lock (transcribe / unload) runs
                # gc.collect() + release_gpu_memory() regardless (OOMs
                # on RTX 3060/4060 after repeated CUDA-probe-failure
                # reloads).
                engine._pending_gc_collect = True
                engine._reload_under_lock()
                log.warning(
                    "[CUDA-PROBE] Model reloaded on CPU after CUDA probe failure. Loaded via: %s",
                    engine.loaded_via,
                )
        else:
            raise


def warm_up_model(engine) -> None:
    """Run a warm-up inference with silence to prime CUDA kernels.

    The first CUDA inference typically takes 2-5 seconds longer than
    subsequent ones because the GPU kernels need to be compiled (JIT)
    and memory allocated. This runs a 0.5-second silence transcription
    after model load so the first real dictation is fast.

    If the model is on CPU or warm-up fails, this is a no-op.
    """
    if engine._model is None or engine._device != "cuda":
        return
    try:
        import numpy as np

        # C-LOG-2: the warm-up completion line below carries the
        # duration suffix; the timer covers the whole warm-up
        # (silence buffer alloc + inference + iteration).
        _t0 = time.perf_counter()
        warmup_audio = np.zeros(int(_WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
        # Same ``best_of`` reasoning as the probe above: a no-op under the
        # pinned ``temperature=0.0``, so it is not forwarded.
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
        # leading space — splice with a bare %s, no extra separator.
        log.info(
            "[PERF] Warm-up inference completed — CUDA kernels primed%s",
            format_duration(time.perf_counter() - _t0),
        )
    except Exception as exc:
        # Warm-up failure is non-critical — log and continue
        log.debug("[PERF] Warm-up inference skipped: %s", exc)
