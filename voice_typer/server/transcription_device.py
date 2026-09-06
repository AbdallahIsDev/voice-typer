"""Device-resolution helpers for ``TranscriptionEngine``.

Extracted from ``voice_typer/server/transcription.py`` (which stays the
public facade and keeps thin one-line delegator methods on the engine
class) so the device-detection concern can be unit-tested in isolation:

* :func:`resolve_device` — auto-detect the best (device, compute_type)
  pair for a requested device string ("auto" / "cuda" / "cpu").
* :func:`resolve_device_once` — run the (expensive) CUDA detection once,
  near load time, and cache the result on the engine.
* :func:`apply_auto_beam_size` — re-resolve ``engine.beam_size`` when the
  user left it on auto, after the resolved device changed.
* :func:`whisper_cpu_threads` — derive the CTranslate2 intra-op thread
  budget for ``WhisperModel`` from the machine's core count (capped), so
  CPU dictation uses the hardware instead of CTranslate2's fixed
  4-thread default.

TEST PATCH COMPATIBILITY
------------------------
``_configure_nvidia_dll_paths``, ``_cuda_runtime_available`` and
``_auto_beam_size`` live in (and are re-exported by) the
``voice_typer.server.transcription`` module. Tests monkeypatch them via
the ``voice_typer.server.transcription.<name>`` path, so the helpers
below resolve them through **late binding**
(``from voice_typer.server import transcription as _t`` then
``_t.<name>``) so the patched binding is read at call time — the same
pattern used by ``transcription_load.py``.

Engine methods (``_resolve_device`` etc.) are dispatched via the engine
object (``engine._resolve_device``) so instance/class-level monkeypatches
keep taking effect.
"""

from __future__ import annotations

import logging
import os

# Use the ``transcription`` logger name so log records emitted from this
# extracted module are captured by tests that filter by
# ``logger="voice_typer.server.transcription"`` (the historical logger
# name when this code lived inline in ``transcription.py``).
log = logging.getLogger("voice_typer.server.transcription")


def resolve_device(engine, device: str) -> tuple[str, str]:
    """Auto-detect best device and compute type.

    previously the CUDA-detection try/except used bare
    ``Exception``, hiding real setup errors (driver mismatch,
    missing DLLs). Narrowed to ``(OSError, RuntimeError,
    ImportError)`` so genuine bugs propagate.

    ``engine`` is accepted for signature parity with the other
    extracted engine helpers (the device probe reads no engine state).
    """
    # Late binding: tests may patch
    # ``voice_typer.server.transcription._configure_nvidia_dll_paths``
    # and ``voice_typer.server.transcription._cuda_runtime_available``.
    from voice_typer.server import transcription as _t

    if device == "cpu":
        return "cpu", "int8"

    # Try CUDA
    if device in ("auto", "cuda"):
        try:
            _t._configure_nvidia_dll_paths()
            # Windows fast path: when the CUDA runtime DLLs cannot
            # be loaded (CPU-only torch, missing nvidia-* wheels),
            # skip the expensive ``import ctranslate2`` + CUDA device
            # probe — the import alone costs ~20s of CUDA
            # enumeration and the probe would fail at load time
            # anyway, forcing a CPU reload.
            if _t._cuda_runtime_available() is False:
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
            # OSError: missing DLL / driver file
            # RuntimeError: ctranslate2 internal init failure
            # ImportError: ctranslate2 not installed
            log.warning(
                "[MODEL] CUDA detection failed, falling back to CPU",
                exc_info=True,
            )

        if device == "cuda":
            log.warning("[MODEL] CUDA requested but not available, falling back to CPU")

    log.info("[MODEL] Using CPU for transcription")
    return "cpu", "int8"


def resolve_device_once(engine) -> None:
    """Resolve the CUDA device if not already resolved.

    Separated from __init__ so the expensive ``import ctranslate2`` and
    CUDA DLL loading only happens when the model is actually about to
    load, not during construction.  This saves ~20s on startup when the
    user hasn't pressed F2 yet.
    """
    if engine._requested_device is None:
        return
    device = engine._requested_device
    engine._requested_device = None
    engine._device, engine._compute_type = engine._resolve_device(device)
    engine._apply_auto_beam_size()


# Hard ceiling on the CTranslate2 intra-op thread budget. Without a
# cap, wide machines would hand every core to the decoder and starve
# the audio pipeline's real-time capture path.
_WHISPER_CPU_THREADS_CAP = 8


def whisper_cpu_threads() -> int:
    """Return the ``cpu_threads`` budget to pass to ``WhisperModel``.

    CTranslate2 defaults to 4 intra-op threads when ``cpu_threads`` is
    left unset, which under-uses multi-core machines — the CPU decode
    path is the primary non-GPU path and the whole GPU→CPU fallback
    chain. This derives the budget from the available cores, capped at
    :data:`_WHISPER_CPU_THREADS_CAP` so decode threads never contend
    badly with the audio capture pipeline.

    Resolution order:

    1. ``psutil.cpu_count(logical=False)`` — physical cores (psutil is
       a declared project dependency; ``logical=False`` avoids paying
       SMT hyperthreads that barely help batched matrix work).
    2. ``os.sched_getaffinity(0)`` — affinity-aware logical CPU count
       (Linux; used when the physical count is unavailable).
    3. ``os.cpu_count()`` — final fallback (Windows/macOS have no
       ``sched_getaffinity``).

    Always returns ``>= 1``.
    """
    physical: int | None = None
    try:
        import psutil

        physical = psutil.cpu_count(logical=False)
    except ImportError:
        physical = None
    if physical is None:
        try:
            physical = len(os.sched_getaffinity(0))
        except AttributeError:
            physical = os.cpu_count() or 1
    return max(1, min(_WHISPER_CPU_THREADS_CAP, physical))


def apply_auto_beam_size(engine) -> None:
    """Re-resolve ``engine.beam_size`` when the user left it on auto.

    Called whenever the resolved device changes (initial load-time
    resolution and every GPU→CPU fallback) so the effective beam
    width always matches the current runtime: wide beams only while
    dictation runs on CUDA with a non-tiny model. An explicitly
    configured beam (legacy kwarg or ``whisper_beam_size``) is never
    touched.
    """
    if not getattr(engine, "_beam_size_auto", False):
        return
    # Late binding: ``_auto_beam_size`` (and the AUTO_CUDA_BEAM_SIZE
    # constant it reads) canonically live in ``transcription.py`` so
    # tests that import them from there keep resolving.
    from voice_typer.server import transcription as _t

    engine.beam_size = _t._auto_beam_size(engine.model_size, engine._device)
