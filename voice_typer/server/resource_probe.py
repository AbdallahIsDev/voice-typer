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
third-party probes (``psutil``, ``onnxruntime``, ``pynvml``), and the
module-level ``log`` logger. The ``DictationPipeline._check_resources`` method is
preserved as a 1-line delegator for test compatibility (tests call
``pipeline._check_resources()`` directly) — see
``dictation_pipeline.py``.

AGENTS.md C-DATA-1: this module performs NO network calls. It only
reads local system state via ``psutil.virtual_memory`` /
``shutil.disk_usage`` / ``os.statvfs`` / ``ctypes.windll.kernel32.GlobalMemoryStatusEx``
/ ``onnxruntime.get_device()`` / ``nvidia-smi`` subprocess / ``pynvml`` — all
in-process local probes (no sockets, no HTTP, no DNS).

Exit code 0xC0000374 (STATUS_HEAP_CORRUPTION) during transcription is
often caused by low memory (RAM) or insufficient disk space (affecting
pagefile/swap). The logs emitted here help diagnose the root cause when
paired with a crash dump.

Phase 1c (PLAN_ONNX_INTEGRATION.md §6.4): the GPU-memory block was
rewritten to drop the ``torch.cuda.*`` dependency. ``onnxruntime.get_device()``
is used for the CUDA-availability check and ``nvidia-smi`` (or ``pynvml``
if installed) is used for the memory query. The block is still wrapped
in ``try/except Exception`` with DEBUG fallback so the probe remains
best-effort.
"""

import contextlib
import logging
import os
import pathlib
import subprocess
import time

# default throttle interval — once per 60s. The values change slowly
# and are only needed for post-crash triage, not per-utterance decisions.
# Previously the probe ran every utterance (~2-5ms of system/driver calls).
DEFAULT_CHECK_INTERVAL: float = 60.0

log = logging.getLogger(__name__)


def _probe_ort_device() -> str | None:
    """Return ``onnxruntime.get_device()`` if ORT is importable.

    Returns ``"cuda"``, ``"cpu"``, or another ORT device string when
    ``onnxruntime`` is installed and ``get_device()`` succeeds. Returns
    ``None`` when ORT is not installed OR the call raises (so the caller
    can fall through to the ``nvidia-smi`` subprocess path or skip the
    GPU log line entirely).

    Wrapped in ``try/except Exception`` (not just ``ImportError``)
    because ORT can fail at import time on a broken CUDA install
    (e.g. ``onnxruntime-gpu`` wheel installed but cuDNN DLLs missing
    on Windows raises ``RuntimeError`` during ``import onnxruntime``).
    """
    try:
        import onnxruntime as ort

        return str(ort.get_device())
    except Exception:
        return None


def _probe_gpu_memory_via_pynvml() -> tuple[float | None, float | None]:
    """Query GPU total/free memory (MB) via ``pynvml`` if installed.

    Returns ``(total_mb, free_mb)``. Returns ``(None, None)`` when
    ``pynvml`` is not installed, no NVIDIA driver is present, or any
    error occurs (the caller falls through to the ``nvidia-smi``
    subprocess path). All errors are caught — this helper is a
    best-effort probe, not a hard dependency.
    """
    try:
        import pynvml  # type: ignore[import-untyped]
    except ImportError:
        return (None, None)
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        total_mb = info.total / (1024 * 1024)
        free_mb = info.free / (1024 * 1024)
        return (total_mb, free_mb)
    except Exception:
        return (None, None)
    finally:
        with contextlib.suppress(Exception):
            pynvml.nvmlShutdown()


def _probe_gpu_memory_via_nvidia_smi() -> tuple[float | None, float | None]:
    """Query GPU total/free memory (MB) via ``pynvml`` or ``nvidia-smi``.

    Tries ``pynvml`` first (in-process, more efficient). Falls back to
    spawning ``nvidia-smi`` subprocess (no Python deps, but ~10-30ms
    overhead per call). Returns ``(None, None)`` when neither path
    succeeds — the caller (``check_resources``) then queries ORT's
    device string so the log line at least records whether ORT sees a
    CUDA device.

    The ``nvidia-smi`` query uses
    ``--query-gpu=memory.total,memory.free --format=csv,noheader,nounits``
    which returns a single line like ``8192, 5120``. Output is split on
    the comma and parsed as floats (MB).

    Wrapped in ``try/except Exception`` because the subprocess can fail
    in many ways: ``nvidia-smi`` not on PATH (most headless CI), exit
    code nonzero (no NVIDIA driver), malformed output (older
    ``nvidia-smi`` builds), or ``TimeoutExpired`` (driver hang).
    """
    total_mb, free_mb = _probe_gpu_memory_via_pynvml()
    if total_mb is not None and free_mb is not None:
        return (total_mb, free_mb)

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return (None, None)
        first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if not first_line:
            return (None, None)
        parts = [p.strip() for p in first_line.split(",")]
        if len(parts) < 2:
            return (None, None)
        total_mb = float(parts[0])
        free_mb = float(parts[1])
        return (total_mb, free_mb)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return (None, None)


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
    # Phase 1c (PLAN_ONNX_INTEGRATION.md §6.4): replaced the 13-line
    # ``torch.cuda.memory_*`` block with ``onnxruntime.get_device()``
    # (CUDA-availability check) + ``nvidia-smi`` subprocess (memory
    # query). ``pynvml`` is used if available — it is more efficient
    # than spawning ``nvidia-smi`` per check, but the wheel is not in
    # the project's hard deps so the subprocess is the safe fallback.
    # The block is wrapped in the same ``try/except Exception`` pattern
    # with DEBUG fallback as the original torch block.
    try:
        gpu_total_mb, gpu_free_mb = _probe_gpu_memory_via_nvidia_smi()
        if gpu_total_mb is not None and gpu_free_mb is not None:
            gpu_used_mb = gpu_total_mb - gpu_free_mb
            _log.info(
                "[RESOURCE] GPU memory: %.0f MB used, %.0f MB free (total %.0f MB)",
                gpu_used_mb,
                gpu_free_mb,
                gpu_total_mb,
            )
            if gpu_free_mb < 512:
                _log.warning(
                    "[RESOURCE] Low GPU memory (%.0f MB free) — CUDA out-of-memory errors are likely.",
                    gpu_free_mb,
                )
        else:
            # nvidia-smi unavailable (no NVIDIA GPU, headless CI, macOS,
            # or the binary is not on PATH). Check ORT's device report so
            # the log line at least records whether ORT sees a CUDA
            # device. ``onnxruntime.get_device()`` returns "cuda" or "cpu".
            ort_device = _probe_ort_device()
            if ort_device is not None:
                _log.info(
                    "[RESOURCE] GPU: onnxruntime reports device='%s' (nvidia-smi unavailable)",
                    ort_device,
                )
            else:
                _log.debug("[RESOURCE] GPU memory probe skipped (nvidia-smi + onnxruntime both unavailable)")
    except Exception:
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
