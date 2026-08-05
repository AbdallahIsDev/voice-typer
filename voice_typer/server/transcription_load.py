"""Model-load helpers for ``TranscriptionEngine``.

Extracted from ``voice_typer/server/transcription.py`` so the engine
module stays under the 500-line maintenance budget. This module hosts:

* :class:`TranscriberProtocol` — the typing protocol any transcription
  engine must implement (moved here from ``transcription.py`` to keep
  that module lean; re-exported from ``transcription`` for back-compat).
* :func:`load_transcriber_impl` — shared model-load body used by both
  ``_load_model_outside_lock`` and ``_reload_under_lock``.
* :func:`probe_cuda_runtime` — force-loads cuBLAS/cuDNN with a 1s sine
  wave so DLL failures surface at startup, not mid-recording.
* :func:`warm_up_model` — runs a 0.5s silence inference
  after load so the first real dictation is fast.
* :func:`resolve_device` — auto-detect best (device, compute_type).
* :func:`build_fallback_chain` — build the load fallback chain.

TEST PATCH COMPATIBILITY
------------------------
``release_gpu_memory`` and ``_configure_nvidia_dll_paths`` are
re-exported into the ``transcription`` module namespace (for back-compat
with callers and tests that import them from there). Any future test
that patches ``voice_typer.server.transcription.release_gpu_memory`` or
``voice_typer.server.transcription._configure_nvidia_dll_paths`` should
keep taking effect — the helpers below resolve them via **late binding**
(``from voice_typer.server import transcription as _t`` then
``_t.release_gpu_memory()``) so the patched binding is read at call
time.

``_reload_under_lock`` and ``_build_fallback_chain`` are *engine*
methods — accessed via ``engine._reload_under_lock()`` /
``engine._build_fallback_chain()`` so tests that monkeypatch them on
the engine instance (e.g. ``engine._reload_under_lock = MagicMock()``)
continue to take effect.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE as _WHISPER_SAMPLE_RATE
from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")

# Use the ``transcription`` logger name so log records emitted from this
# extracted module are captured by tests that filter by
# ``logger="voice_typer.server.transcription"`` (the historical logger
# name when this code lived inline in ``transcription.py``).
log = logging.getLogger("voice_typer.server.transcription")


@runtime_checkable
class TranscriberProtocol(Protocol):
    """Protocol that any transcription engine must implement.

    ``isinstance(backend, TranscriberProtocol)`` correctly identifies
    backends that support streaming (including the ``transcribe_words``
    method used by ``streaming.py`` and ``recording_controller.py``).
    """

    @property
    def is_loaded(self) -> bool: ...

    def load(self, progress_callback=None) -> None: ...

    def transcribe(self, audio: np.ndarray, audio_stats: tuple[float, float, float] | None = None) -> str: ...

    def transcribe_with_fallback(
        self, audio: np.ndarray, audio_stats: tuple[float, float, float] | None = None
    ) -> str: ...

    def unload(self) -> None: ...

    @property
    def device_info(self) -> str: ...

    @property
    def loaded_via(self) -> str: ...

    def transcribe_words(self, audio: np.ndarray, offset_seconds: float = 0.0) -> object: ...


def load_transcriber_impl(
    engine,
    chain: list[tuple[str, str, str]],
    *,
    acquire_lock: bool,
    progress_callback=None,
    verb: str = "Loading",
) -> None:
    """Shared model-load body used by both _load_model_outside_lock
    and _reload_under_lock.

    previously two near-identical 30-line bodies that
    differed only in lock acquisition. Now a single function with
    an ``acquire_lock`` flag.

    Raises:
        RuntimeError: if every entry in the fallback chain failed.
    """
    # Map progressive verb → base form for error messages.
    verb_base = "load" if verb.lower() == "loading" else "reload"

    # Late binding: tests may patch
    # ``voice_typer.server.transcription._configure_nvidia_dll_paths``.
    from voice_typer.server import transcription as _t

    _t._configure_nvidia_dll_paths()
    from faster_whisper import WhisperModel

    last_error = None
    for device, compute_type, model_size in chain:
        try:
            log.info(
                "[MODEL] %s Whisper model '%s' on %s (%s)...",
                verb,
                model_size,
                device,
                compute_type,
            )
            if progress_callback:
                progress_callback(f"{verb} model '{model_size}'...")
            # time WhisperModel construction to measure
            # prewarm cache-hit effectiveness.
            _t0 = time.perf_counter()
            model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
            )
            _load_elapsed = time.perf_counter() - _t0
            _warm_label = "warm (page-cache)" if _load_elapsed < 5.0 else "cold (disk)"
            if acquire_lock:
                with engine._lock:
                    if engine._model is not None:
                        return
                    engine._model = model
                    engine._device = device
                    engine._compute_type = compute_type
                    engine._loaded_model_size = model_size
                    engine.model_size = engine._configured_model_size
            else:
                # Caller already holds the lock.
                engine._model = model
                engine._device = device
                engine._compute_type = compute_type
                engine._loaded_model_size = model_size
                engine.model_size = engine._configured_model_size
            log.info(
                "[MODEL] Model %s via %s (%s) — %.1fs",
                verb.lower(),
                engine.loaded_via,
                _warm_label,
                _load_elapsed,
            )

            # CUDA probe: force a tiny transcription to smoke-test
            # cuBLAS loading at startup, so failures surface here
            # (with a clean fallback to CPU) rather than mid-recording.
            if engine._device == "cuda" and acquire_lock:
                probe_cuda_runtime(engine, progress_callback)

            # warm-up inference with 0.5s of silence.
            # Primes CUDA kernels so the first real transcription
            # doesn't pay the kernel compilation cost (~2-5s).
            warm_up_model(engine)
            return
        except Exception as exc:
            last_error = exc
            log.warning(
                "Model %s failed on %s (%s) model=%s: %s",
                verb.lower(),
                device,
                compute_type,
                model_size,
                exc,
            )
            if not acquire_lock:
                engine._model = None

    raise RuntimeError(
        f"Failed to {verb_base} Whisper model on any device/model. Last error: {last_error}"
    ) from last_error


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
    # load path. The sole caller (load_transcriber_impl above) only
    # invokes us after a successful load, but pyrefly cannot prove
    # that contract across method boundaries — so guard explicitly.
    # Returning early here also makes the function safe to call from
    # tests / future callers that haven't loaded a model yet.
    if engine._model is None:
        log.warning("[CUDA-PROBE] Skipping — no model loaded")
        return
    import numpy as np

    t = np.arange(int(_WHISPER_SAMPLE_RATE), dtype=np.float32) / _WHISPER_SAMPLE_RATE
    probe_audio = np.sin(2 * np.pi * 440 * t, dtype=np.float32) * 0.1

    log.info("[CUDA-PROBE] Running CUDA runtime smoke test (1s sine wave)...")
    if progress_callback:
        progress_callback("Running CUDA runtime probe...")
    try:
        # Must exercise the same cuBLAS kernels as real dictation.
        # NOTE: vad_filter=False is deliberate — VAD would reject a
        # sine wave as non-speech, causing Whisper to be skipped.
        segments, info = engine._model.transcribe(
            probe_audio,
            beam_size=engine.beam_size,
            best_of=engine.best_of,
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
            # had exited, leaving a race window where
            # ``transcribe()`` would raise ``RuntimeError("Model not
            # loaded")`` or see a partially-initialized model.
            with engine._lock:
                try:
                    del engine._model
                    import gc

                    gc.collect()
                    # release PyTorch's cached CUDA blocks
                    # so the next backend (or CPU reload) can use them.
                    # Late binding: ``release_gpu_memory`` is re-exported
                    # into ``transcription`` so tests may patch it there.
                    from voice_typer.server import transcription as _t

                    _t.release_gpu_memory()
                except Exception:
                    log.debug("[MODEL] GPU memory release failed", exc_info=True)
                engine._model = None
                engine._device = "cpu"
                engine._compute_type = "int8"
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
    and memory allocated. This method runs a 0.5-second silence
    transcription after model load so the first real dictation is fast.

    If the model is on CPU or warm-up fails, this is a no-op.
    """
    if engine._model is None or engine._device != "cuda":
        return
    try:
        import numpy as np

        warmup_audio = np.zeros(int(_WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
        segments, _ = engine._model.transcribe(
            warmup_audio,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            vad_filter=False,
            language=engine.language,
            without_timestamps=True,
        )
        # Force iteration to complete the warm-up
        for _ in segments:
            pass
        log.info("[PERF-007] Warm-up inference completed — CUDA kernels primed")
    except Exception as exc:
        # Warm-up failure is non-critical — log and continue
        log.debug("[PERF-007] Warm-up inference skipped: %s", exc)


def resolve_device(engine, device: str) -> tuple[str, str]:
    """Auto-detect best device and compute type.

    CUDA-detection try/except is narrowed to ``(OSError, RuntimeError,
    ImportError)`` so genuine setup bugs propagate (driver mismatch,
    missing DLLs, ctranslate2 not installed).
    """
    # Late binding: tests may patch
    # ``voice_typer.server.transcription._configure_nvidia_dll_paths``.
    from voice_typer.server import transcription as _t

    if device == "cpu":
        return "cpu", "int8"

    if device in ("auto", "cuda"):
        try:
            _t._configure_nvidia_dll_paths()
            # Windows fast path: when the CUDA runtime DLLs cannot be
            # loaded (CPU-only torch, missing nvidia-* wheels), skip the
            # expensive ``import ctranslate2`` + CUDA device probe — the
            # import alone costs ~20s of CUDA enumeration and the probe
            # would fail at load time anyway, forcing a CPU reload.
            if getattr(_t, "_cuda_runtime_available", lambda: True)() is False:
                log.warning(
                    "[MODEL] CUDA runtime DLLs unavailable on Windows — using CPU directly (skipped ~20s CUDA probe)"
                )
                if device == "cuda":
                    log.warning("[MODEL] CUDA requested but DLLs unavailable, falling back to CPU")
                log.info("[MODEL] Using CPU for transcription")
                return "cpu", "int8"
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                log.info("[MODEL] Using CUDA device for transcription")
                return "cuda", "float16"
        except (OSError, RuntimeError, ImportError):
            log.warning(
                "[MODEL] CUDA detection failed, falling back to CPU",
                exc_info=True,
            )

        if device == "cuda":
            log.warning("[MODEL] CUDA requested but not available, falling back to CPU")

    log.info("[MODEL] Using CPU for transcription")
    return "cpu", "int8"


def build_fallback_chain(engine) -> list[tuple[str, str, str]]:
    """Build the fallback chain for model loading."""
    chain: list[tuple[str, str, str]] = []
    chain.append((engine._device, engine._compute_type, engine.model_size))
    if engine._device != "cpu" or engine._compute_type != "int8":
        chain.append(("cpu", "int8", engine.model_size))
    if engine.model_size != "tiny.en":
        chain.append(("cpu", "int8", "tiny.en"))
    chain.append(("cpu", "float32", "tiny.en"))
    return chain
