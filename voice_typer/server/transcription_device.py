"""Transcription input-device helpers."""

from __future__ import annotations

import logging
import os

# Use the ``transcription`` logger name so log records emitted from this
log = logging.getLogger("voice_typer.server.transcription")


def resolve_device(engine, device: str) -> tuple[str, str]:
    """Auto-detect best device and compute type."""
    # Late binding: tests may patch
    from voice_typer.server import transcription as _t

    if device == "cpu":
        return "cpu", "int8"

    # Try CUDA
    if device in ("auto", "cuda"):
        try:
            _t._configure_nvidia_dll_paths()
            # Windows fast path: when the CUDA runtime DLLs cannot
            if _t._cuda_runtime_available() is False:
                log.warning(
                    "[MODEL] CUDA runtime DLLs unavailable on Windows, using CPU directly (skipped ~20s CUDA probe)"
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
            log.warning(
                "[MODEL] CUDA detection failed, falling back to CPU",
                exc_info=True,
            )

        if device == "cuda":
            log.warning("[MODEL] CUDA requested but not available, falling back to CPU")

    log.info("[MODEL] Using CPU for transcription")
    return "cpu", "int8"


def resolve_device_once(engine) -> None:
    """Logs the probe wall time (C-LOG-2 suffix): cold-boot"""
    if engine._requested_device is None:
        return
    import time

    from voice_typer.server.duration import format_duration

    _t0 = time.perf_counter()
    device = engine._requested_device
    engine._requested_device = None
    engine._device, engine._compute_type = engine._resolve_device(device)
    engine._apply_auto_beam_size()
    log.info(
        "[MODEL] Device probe (ctranslate2 import + CUDA enum)%s",
        format_duration(time.perf_counter() - _t0),
    )


# Hard ceiling on the CTranslate2 intra-op thread budget. Without a
_WHISPER_CPU_THREADS_CAP = 8


def whisper_cpu_threads() -> int:
    """Return the ``cpu_threads`` budget to pass to ``WhisperModel``."""
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
    """Re-resolve ``engine.beam_size`` when the user left it on auto."""
    if not getattr(engine, "_beam_size_auto", False):
        return
    # Late binding: ``_auto_beam_size`` (and the AUTO_CUDA_BEAM_SIZE
    from voice_typer.server import transcription as _t

    engine.beam_size = _t._auto_beam_size(engine.model_size, engine._device)
