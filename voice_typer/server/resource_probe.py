"""Pre-flight RAM / disk / GPU resource probe.

Extracted from ``voice_typer/server/dictation_pipeline.py:_check_resources``
(the 185-LOC self-contained probe that the original docstring flagged as a
DEFERRED refactor — the monolith-split phase has now done the extraction).

This module is a SIBLING HELPER, NOT a pipeline stage: the probe runs
BEFORE the stage pipeline starts (it is a pre-flight diagnostic that
provides context if a heap-corruption crash occurs during model
inference). It belongs in this dedicated module rather than in
``dictation_stages.py`` because it is not a stage.

It has NO dependencies on ``DictationPipeline`` instance state — only
stdlib imports (``os``, ``pathlib``, ``shutil``, ``ctypes``), optional
third-party probes (``psutil``, ``torch``), and the module-level
``log`` logger. The ``DictationPipeline._check_resources`` method is
preserved as a 1-line delegator for test compatibility (tests call
``pipeline._check_resources()`` directly) — see
``dictation_pipeline.py``.

CONSTRAINTS.md C-DATA-1: this module performs NO network calls. It only
reads local system state via ``psutil.virtual_memory`` /
``shutil.disk_usage`` / ``os.statvfs`` / ``ctypes.windll.kernel32.GlobalMemoryStatusEx``
/ ``torch.cuda.memory_*`` — all in-process local probes (no sockets,
no HTTP, no DNS).

Exit code 0xC0000374 (STATUS_HEAP_CORRUPTION) during transcription is
often caused by low memory (RAM) or insufficient disk space (affecting
pagefile/swap). The logs emitted here help diagnose the root cause when
paired with a crash dump.
"""

import logging
import os
import pathlib
import time

# default throttle interval — once per 60s. The values change slowly
# and are only needed for post-crash triage, not per-utterance decisions.
# Previously the probe ran every utterance (~2-5ms of system/driver calls).
DEFAULT_CHECK_INTERVAL: float = 60.0

log = logging.getLogger(__name__)


def check_resources(*, logger: logging.Logger | None = None) -> None:
    """Pre-flight RAM / disk / GPU resource probe.

    Checks available RAM, disk space, and GPU memory (if CUDA) and logs
    warnings when resources are critically low.  The check is best-effort
    — failures are logged at DEBUG level and do NOT abort the pipeline
    (the user may still succeed even with low resources).

    Exit code 0xC0000374 (STATUS_HEAP_CORRUPTION) during transcription is
    often caused by low memory (RAM) or insufficient disk space (affecting
    pagefile/swap).  These logs help diagnose the root cause when paired
    with a crash.

    Args:
        logger: optional logger to emit records under. Defaults to this
            module's logger (``voice_typer.server.resource_probe``). The
            ``DictationPipeline._check_resources`` delegator passes its
            own logger (``voice_typer.server.dictation_pipeline``) so log
            records continue to appear under the historical logger name
            (preserving the behavior pinned by existing tests).
    """
    _log = logger if logger is not None else log

    # ── RAM check ───────────────────────────────────────────────
    free_mb: float | None = None
    try:
        import psutil

        free_mb = psutil.virtual_memory().available / (1024 * 1024)
    except ImportError:
        try:
            import ctypes

            if os.name == "nt":

                class _MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                stat = _MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(stat)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                free_mb = stat.ullAvailPhys / (1024 * 1024)
        except Exception:
            #  previously a bare ``except
            # Exception: pass`` — the docstring at the top of
            # ``check_resources`` promises "failures are logged at
            # DEBUG level", but this branch silently swallowed the
            # ctypes fallback failure (e.g. ``GlobalMemoryStatusEx``
            # returning an error code on a stripped-down Windows
            # IoT build), leaving operators with no clue why the
            # RAM INFO line was missing. Emit a DEBUG line with the
            # traceback so the docstring's promise is honored.
            _log.debug(
                "[RESOURCE] RAM check (ctypes fallback) failed (non-fatal)",
                exc_info=True,
            )

    if free_mb is not None:
        _log.info(
            "[RESOURCE] Available RAM: %.0f MB",
            free_mb,
        )
        if free_mb < 1024:
            _log.warning(
                "[RESOURCE] Low RAM (%.0f MB < 1024 MB) — "
                "heap corruption (0xC0000374) is possible during "
                "model inference.  Close other apps or try a "
                "smaller transcription model.",
                free_mb,
            )
        elif free_mb < 2048:
            _log.info(
                "[RESOURCE] RAM is moderate (%.0f MB) — large models may struggle.",
                free_mb,
            )
    else:
        _log.debug("[RESOURCE] Could not query available RAM")

    # ── Disk space check ────────────────────────────────────────
    # Check both the system drive (for pagefile) and the model
    # cache drive (for model downloads).
    drives_to_check: list[pathlib.Path] = []
    try:
        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
        drives_to_check.append(config_dir)
        drives_to_check.append(pathlib.Path.home())
        # Add the drive where the model cache lives (HF_HOME)
        hf_home = os.environ.get("HF_HOME")
        if hf_home:
            drives_to_check.append(pathlib.Path(hf_home))
    except Exception:
        drives_to_check.append(pathlib.Path.home())

    seen_drives: set[str] = set()
    for path in drives_to_check:
        try:
            drive_info = os.statvfs(path) if hasattr(os, "statvfs") else None
        except Exception:
            continue
        if drive_info is None:
            # Windows: use shutil.disk_usage
            try:
                import shutil

                usage = shutil.disk_usage(path)
                free_gb = usage.free / (1024**3)
                # Deduplicate by mount point (same drive may appear
                # via multiple paths like home dir + config dir)
                drive_key = str(path.resolve())
                if drive_key in seen_drives:
                    continue
                seen_drives.add(drive_key)
                _log.info(
                    "[RESOURCE] Disk free on %s: %.1f GB",
                    path,
                    free_gb,
                )
                if free_gb < 1.0:
                    _log.warning(
                        "[RESOURCE] Critically low disk space on %s "
                        "(%.1f GB < 1 GB) — heap corruption is possible "
                        "if the system pagefile cannot grow.  Free up "
                        "disk space or move the model cache to a "
                        "drive with more free space.",
                        path,
                        free_gb,
                    )
            except Exception:
                continue
        else:
            # POSIX: use statvfs
            free_gb = (drive_info.f_bavail * drive_info.f_frsize) / (1024**3)
            _log.info(
                "[RESOURCE] Disk free: %.1f GB",
                free_gb,
            )
            if free_gb < 1.0:
                _log.warning(
                    "[RESOURCE] Critically low disk space (%.1f GB) — heap corruption risk for pagefile.",
                    free_gb,
                )

    # ── GPU memory check (if CUDA) ──────────────────────────────
    try:
        import torch

        if torch.cuda.is_available():
            allocated = torch.cuda.memory_allocated() / (1024**2)
            reserved = torch.cuda.memory_reserved() / (1024**2)
            total = torch.cuda.get_device_properties(0).total_memory / (1024**2)
            free_gpu = total - allocated
            _log.info(
                "[RESOURCE] GPU memory: %.0f MB allocated, %.0f MB reserved, %.0f MB free (total %.0f MB)",
                allocated,
                reserved,
                free_gpu,
                total,
            )
            if free_gpu < 512:
                _log.warning(
                    "[RESOURCE] Low GPU memory (%.0f MB free) — CUDA out-of-memory errors are likely.",
                    free_gpu,
                )
    except Exception:
        #  previously ``except (ImportError,
        # Exception): pass``. ``ImportError`` was redundant (Exception
        # already covers it) and the bare ``pass`` contradicted the
        # docstring's promise that "failures are logged at DEBUG
        # level". Emit a DEBUG line with the traceback so an
        # operator looking at voice-typer.log sees why the GPU
        # INFO line is absent (e.g. torch installed but CUDA
        # driver mismatch, ``torch.cuda.get_device_properties``
        # raising on a headless CI runner).
        _log.debug(
            "[RESOURCE] GPU check failed (non-fatal)",
            exc_info=True,
        )

    _log.debug("[RESOURCE] Pre-flight health check complete")


def check_resources_throttled(
    last_check_ts: float,
    interval: float = DEFAULT_CHECK_INTERVAL,
    *,
    now: float | None = None,
    logger: logging.Logger | None = None,
) -> float:
    """Throttled wrapper around :func:`check_resources`.

    Runs the actual check at most once per ``interval`` seconds (default
    60s). The values change slowly and are only needed for post-crash
    triage, not per-utterance decisions.

    The throttle state (last-check timestamp) is NOT held as module-level
    mutable state — instead it is passed in by the caller and the new
    timestamp is returned. This keeps the function pure with respect to
    its throttle inputs and lets the caller (``DictationPipeline``)
    persist the state on its instance (preserving the existing
    ``pipeline._last_resources_check_ts`` contract pinned by tests).

    Args:
        last_check_ts: monotonic timestamp of the last real check.
        interval: minimum seconds between real checks (default 60s).
        now: override for ``time.monotonic()`` (for tests). Defaults to
            the real monotonic clock.
        logger: optional logger forwarded to :func:`check_resources`.

    Returns:
        The new last-check timestamp: equal to ``now`` if the check ran,
        or the unchanged ``last_check_ts`` if it was skipped. The caller
        is responsible for persisting the returned value.
    """
    _now = time.monotonic() if now is None else now
    if _now - last_check_ts < interval:
        return last_check_ts
    check_resources(logger=logger)
    return _now
