"""OS-level cache prewarming for fast cold-boot startup.

The dominant cost on first launch after a Windows reboot is *not* Python
or the model code — it is reading ~6 GB of files (torch + transformers +
Parakeet weights) off disk into the Windows file (standby) cache for the
first time.  Once those pages are resident, subsequent ``import torch``
and ``from_pretrained()`` calls hit RAM instead of the spindle, and
startup drops from ~45 s to a few seconds.

This module provides a standalone entry point — ``python -m
voice_typer.server.prewarm`` — that the platform scheduler runs shortly
after logon / at boot (Windows LogonTrigger, macOS RunAtLoad, Linux
OnBootSec).  It performs, in order, with **low I/O priority**
so it never competes with the user's real work:

1.  **Config / RAM guard.**  Bail out immediately if the user has disabled
    the RAM guard: if free RAM is below the budget (default 6 GB) —
    prewarming on a memory-starved machine would evict the user's
    working set, which is the opposite of helpful.
2.  **Import warmup.**  ``import torch`` + ``from transformers import
    AutoModelForTDT, AutoProcessor``.  This pages in ~4.5 GB of ``.pyc``,
    ``.dll``, and ``.pyd`` files.  The imported modules are then dropped
    (process exits) — we only wanted their bytes in the OS cache.
3.  **Weights warmup.**  Sequentially read the cached
    ``model.safetensors`` (2.4 GB for Parakeet) with a small buffer and
    discard the bytes.  Because the read is sequential and the file is
    already on disk, this just populates the standby list; the process's
    own working set stays a few MB.

The whole script is designed to be **safe to run at any time**: it is
idempotent, never writes anything, never imports the full app, and exits
within a minute on a warm disk (longer on a cold one — that is the point).

Run manually for diagnostics::

    python -m voice_typer.server.prewarm
    python -m voice_typer.server.prewarm --force   # skip config/RAM guards
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from voice_typer.server.platform_utils import is_windows, is_macos, is_linux

log = logging.getLogger("voice_typer.prewarm")

# Skip prewarming when free RAM is below this.  ~6 GB covers the torch
# package (4.2 GB) + Parakeet weights (2.4 GB) without catastrophically
# displacing the user's working set.  Tunable via --min-ram-mb.
DEFAULT_MIN_FREE_RAM_MB = 6 * 1024

# Read weights in this-sized chunk.  Small enough to keep the process's
# own working set tiny, large enough to amortise per-read overhead.
_READ_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MB

# Sentinel file written after successful prewarm.  If a second instance
# fires within the same boot session (known Windows LogonTrigger +
# Hidden=true quirk), the sentinel check skips it early.
_PREWARM_SENTINEL = Path.home() / ".voice-typer" / ".prewarm-sentinel"


# ─── Exit codes (distinct for diagnostics in Task Scheduler history) ─────

EXIT_OK = 0
EXIT_DISABLED = 10          # user turned fast_startup off
EXIT_LOW_RAM = 20           # not enough free RAM to prewarm safely
EXIT_NO_MODEL = 30          # model not cached yet (first-ever run)
EXIT_IMPORT_FAILED = 40     # torch/transformers missing or broken


def _setup_logging() -> None:
    """Minimal logging — prewarm runs detached, so log to the app log file.

    Uses the shared :func:`log.setup_logging` so the format is
    consistent with the main app.  Avoids importing app.py to keep
    prewarm's cold-start cost minimal.
    """
    from voice_typer.server.log import setup_logging as _setup_logging_shared
    log_dir = Path.home() / ".voice-typer"
    try:
        _setup_logging_shared(log_dir)
    except Exception:
        # Fall back to bare stderr so the script is still usable standalone.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            stream=sys.stderr,
        )


# ─── Guards ──────────────────────────────────────────────────────────────

def _fast_startup_enabled() -> bool:
    """Always return True — fast_startup is always enabled.

    The prewarm scheduled task and RAM guard (DEFAULT_MIN_FREE_RAM_MB)
    handle themselves: if free RAM is below the budget, prewarm skips
    with EXIT_LOW_RAM. There is no need for a user-facing toggle.
    """
    return True


def _free_ram_mb() -> int | None:
    """Return available physical RAM in MB, or None if it can't be queried."""
    try:
        import psutil  # type: ignore[import-untyped]
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except ImportError:
        pass
    # Windows fallback via ctypes (no extra dependency).
    if is_windows():
        try:
            import ctypes
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
            return int(stat.ullAvailPhys / (1024 * 1024))
        except Exception as exc:
            log.debug("[PREWARM] ctypes RAM query failed: %s", exc)
    return None


def _lower_io_priority() -> None:
    """Drop this process's I/O and CPU priority so prewarming never
    competes with real user work.  Best-effort — silently no-op off Windows
    or on older builds.

    Uses ``SetPriorityClass(PROCESS_MODE_BACKGROUND_BEGIN)``, which is the
    API Microsoft recommends for background maintenance tasks: it lowers
    CPU, I/O, and memory scheduling priorities atomically.  Falls back to
    ``BELOW_NORMAL_PRIORITY_CLASS`` if background mode is unavailable
    (e.g. the process is already background, or an older Windows build).

    STARTUP-5: on POSIX, lowers CPU priority via os.nice() and I/O priority
    via ionice (Linux only). Best-effort — silently no-ops if the calls
    fail (e.g. insufficient permissions).
    """
    if not is_windows():
        # STARTUP-5: POSIX priority lowering.
        try:
            # Lower CPU priority (POSIX nice). +10 = below normal.
            os.nice(10)
            log.debug("[PREWARM] POSIX: lowered CPU priority via os.nice(10)")
        except (OSError, PermissionError) as e:
            log.debug("[PREWARM] POSIX: os.nice failed: %s", e)
        # On Linux, also try to lower I/O priority to "idle" (class 3).
        # Bug fix: ioprio_set is a SYSCALL, not a libc exported function.
        # The previous code checked hasattr(libc, "ioprio_set") which always
        # returned False (the symbol doesn't exist in libc), so the I/O
        # priority lowering silently no-opped. The correct way is to call
        # syscall(SYS_ioprio_set, which, who, ioprio) via libc.syscall.
        # SYS_ioprio_set = 251 on x86_64, 314 on aarch64 (we try both).
        # IOPRIO_WHO_PROCESS=1, IOPRIO_CLASS_IDLE=3,
        # IOPRIO_PRIO_VALUE(class, level) = (class << 13) | level
        if is_linux():
            try:
                import ctypes
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                libc.syscall.restype = ctypes.c_long
                libc.syscall.argtypes = [
                    ctypes.c_long, ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
                ]
                IOPRIO_WHO_PROCESS = 1
                IOPRIO_CLASS_IDLE = 3
                ioprio = (IOPRIO_CLASS_IDLE << 13) | 0
                # Try x86_64 syscall number first, then aarch64
                for sys_num in (251, 314):
                    rc = libc.syscall(sys_num, IOPRIO_WHO_PROCESS, 0, ioprio)
                    if rc == 0:
                        log.debug("[PREWARM] Linux: set I/O priority to idle (syscall %d)", sys_num)
                        break
                else:
                    log.debug("[PREWARM] Linux: ioprio_set syscall failed for both 251 and 314")
            except Exception as e:
                log.debug("[PREWARM] Linux: ioprio_set failed: %s", e)
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        hproc = kernel32.GetCurrentProcess()

        # PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000.  This is a
        # SetPriorityClass value (NOT SetProcessInformation) and is exactly
        # what Microsoft recommends for background I/O-heavy work.
        PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000
        if kernel32.SetPriorityClass(hproc, PROCESS_MODE_BACKGROUND_BEGIN):
            log.debug("[PREWARM] Set process to BACKGROUND priority mode")
            return

        # Fallback: below-normal priority.  Less aggressive (doesn't lower
        # I/O priority) but works everywhere and never raises.
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        if kernel32.SetPriorityClass(hproc, BELOW_NORMAL_PRIORITY_CLASS):
            log.debug("[PREWARM] Set process priority to BELOW_NORMAL")
            return

        log.debug(
            "[PREWARM] could not lower process priority (err=%d)",
            kernel32.GetLastError(),
        )
    except Exception as exc:
        log.debug("[PREWARM] could not lower process priority: %s", exc)


# ─── Warmup stages ───────────────────────────────────────────────────────

def _warm_imports() -> None:
    """Import torch + transformers so their files enter the OS cache.

    Uses the same import path as ``parakeet_engine.ParakeetEngine._ensure_imports``
    so we warm exactly the bytes that will be needed.

    STARTUP-3: filter the import path by active backend. Previously this
    unconditionally imported torch + transformers (which takes ~30-60 s
    cold, ~400 s when contended). Whisper users don't need transformers —
    they only need faster_whisper (ctranslate2, ~3 s cold). Parakeet/Qwen
    users still need the full torch + transformers stack.
    """
    # STARTUP-3: determine which imports are needed based on the active
    # backend. Whisper → only faster_whisper; parakeet/qwen → full stack.
    active_backend = "whisper"  # default
    try:
        from voice_typer.server.config import Config
        cfg = Config.load()
        active_backend = getattr(cfg, "asr_backend", "whisper")
    except Exception:
        pass

    needs_full_stack = active_backend in ("parakeet", "qwen")

    if needs_full_stack:
        # Parakeet / Qwen both use the HuggingFace transformers stack,
        # so we need torch + transformers (the heavy imports).
        t0 = time.perf_counter()
        import torch  # noqa: F401  — import is the side effect we want
        elapsed = time.perf_counter() - t0
        log.info("[PREWARM] import torch: %.2fs", elapsed)

        t0 = time.perf_counter()
        from transformers import AutoModelForTDT, AutoProcessor  # noqa: F401
        elapsed = time.perf_counter() - t0
        log.info("[PREWARM] import transformers (AutoModelForTDT, AutoProcessor): %.2fs", elapsed)
    else:
        # STARTUP-3: whisper backend — skip torch/transformers (~400 s saved).
        # Whisper uses faster_whisper (ctranslate2) which has no torch
        # dependency. We still import faster_whisper below to warm the
        # CPU-fallback path; the whisper fallback (tiny.en) is what
        # AsrBackendRegistry.load_with_fallback() falls back to.
        log.info(
            "[PREWARM] active backend=%s — skipping torch/transformers import "
            "(whisper only needs faster_whisper)",
            active_backend,
        )

    # Always touch the faster-whisper path. Cheap (ctranslate2 is much
    # smaller than torch) and ensures the whisper fallback branch is warm
    # for both whisper users (primary) and parakeet/qwen users (fallback).
    try:
        t0 = time.perf_counter()
        import faster_whisper  # noqa: F401
        log.info("[PREWARM] import faster_whisper: %.2fs", time.perf_counter() - t0)
    except Exception as exc:
        log.debug("[PREWARM] faster_whisper not importable (skipping): %s", exc)


def _find_parakeet_weights() -> Path | None:
    """Locate the cached Parakeet ``model.safetensors``, or None if absent."""
    try:
        from voice_typer.server.parakeet_engine import _PARAKERT_MODEL_ID
        from voice_typer.server.config import _config_dir
    except Exception:
        return None

    cache_root = _config_dir() / "huggingface" / "hub"
    model_dir = cache_root / f"models--{_PARAKERT_MODEL_ID.replace('/', '--')}"
    snapshots = model_dir / "snapshots"
    if not snapshots.is_dir():
        return None
    try:
        for entry in snapshots.iterdir():
            if entry.is_dir():
                weights = entry / "model.safetensors"
                if weights.exists():
                    return weights
    except OSError:
        pass
    return None


# STARTUP-4: Whisper model sizes that are valid fallback targets.
# AsrBackendRegistry.load_with_fallback() falls back to whisper/tiny.en
# when the active backend fails to load, so we always warm tiny.en as
# the declared fallback (in addition to the active backend's model).
_WHISPER_FALLBACK_MODEL_SIZE = "tiny.en"


def _active_model_cache_dirs() -> list[Path]:
    """STARTUP-4: Return HF cache dirs for the active model + declared fallback.

    Walks the HF cache only for the model directories that the app would
    actually use at runtime:
      - The active backend's model (parakeet / qwen / whisper-<model_size>)
      - The Whisper fallback (tiny.en) that AsrBackendRegistry falls
        back to when the active backend fails to load.

    Previously this walked ALL models--* dirs in the cache, warming ~2.1 GB
    of inactive Whisper variants when the active backend was parakeet.
    """
    dirs: list[Path] = []
    try:
        from voice_typer.server.config import Config, _config_dir
        cfg = Config.load()
        cache_root = _config_dir() / "huggingface" / "hub"
        if not cache_root.exists():
            return dirs

        active_backend = getattr(cfg, "asr_backend", "whisper")
        active_model_size = getattr(cfg, "model_size", "small.en")

        # Build the set of HF repo IDs whose cache dirs we want to warm.
        target_repo_ids: set[str] = set()

        if active_backend == "parakeet":
            try:
                from voice_typer.server.parakeet_engine import _PARAKERT_MODEL_ID
                target_repo_ids.add(_PARAKERT_MODEL_ID)
            except Exception:
                pass
        elif active_backend == "qwen":
            # Qwen auto-downloads on first use via qwen_engine.py; no fixed
            # repo ID. The configured qwen_model_path is a local directory,
            # not an HF repo — we don't prewarm it here.
            pass
        else:
            # Whisper backend: warm the configured model_size
            if active_model_size and active_model_size not in ("parakeet", "qwen"):
                target_repo_ids.add(f"Systran/faster-whisper-{active_model_size}")

        # Always include the declared Whisper fallback (tiny.en) so the
        # AsrBackendRegistry's fallback path is warm too — UNLESS the
        # active backend is whisper with model_size=tiny.en (already covered).
        if not (active_backend == "whisper" and active_model_size == _WHISPER_FALLBACK_MODEL_SIZE):
            target_repo_ids.add(f"Systran/faster-whisper-{_WHISPER_FALLBACK_MODEL_SIZE}")

        # Map repo IDs to cache dir paths and filter to existing ones.
        for repo_id in target_repo_ids:
            cache_dir_name = f"models--{repo_id.replace('/', '--')}"
            cache_dir = cache_root / cache_dir_name
            if cache_dir.is_dir():
                dirs.append(cache_dir)
    except Exception as e:
        log.debug("[PREWARM] _active_model_cache_dirs failed: %s", e)
    return dirs


def _warm_file(path: Path) -> int:
    """Sequentially read *path* so its bytes enter the OS standby cache.

    Returns the number of bytes read.  Uses a small buffer so the process
    working set stays tiny; the goal is to populate the *system* cache,
    not to hold the data ourselves.
    """
    size = path.stat().st_size
    read = 0
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            read += len(chunk)
            # ``del`` immediately so the buffer doesn't accumulate.
            del chunk
    rate = (read / (1024 * 1024)) / max(time.perf_counter() - t0, 1e-6)
    log.info(
        "[PREWARM] warmed %s: %.0f MB in %.1fs (%.0f MB/s)",
        path.name, read / (1024 * 1024), time.perf_counter() - t0, rate,
    )
    return read


# ─── Boot-session dedup (prevent LogonTrigger re-fire) ────────────────────

def _boot_time() -> int | None:
    """Return system boot time as Unix timestamp, or None."""
    try:
        import psutil
        return int(psutil.boot_time())
    except Exception:
        pass
    if is_windows():
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # GetTickCount64 -> ms since boot, subtract from now.
            ms = kernel32.GetTickCount64()
            return int(time.time() - ms / 1000)
        except Exception:
            pass
    return None


def _already_warmed() -> bool:
    """Return True if prewarm already succeeded in this boot session."""
    try:
        bt = _boot_time()
        if bt is None or not _PREWARM_SENTINEL.exists():
            return False
        stored = int(_PREWARM_SENTINEL.read_text().strip())
        return stored == bt
    except Exception:
        return False


def _mark_warmed() -> None:
    """Record successful prewarm for this boot session."""
    try:
        bt = _boot_time()
        if bt is None:
            return
        _PREWARM_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        _PREWARM_SENTINEL.write_text(str(bt))
    except Exception:
        pass


# ─── Orchestration ───────────────────────────────────────────────────────

def run(
    min_ram_mb: int = DEFAULT_MIN_FREE_RAM_MB,
    force: bool = False,
    delay: float = 0.0,
) -> int:
    """Run the prewarm pipeline.  Returns an exit code (see module docstring).

    ``delay`` seconds sleeps before doing anything.  This replaces the
    Task Scheduler logon-trigger delay for the HKCU Run-key fallback
    path (which has no native delay), letting login settle so prewarm
    does not contend with every other startup program hitting disk.
    """
    _setup_logging()
    if delay > 0:
        log.info("[PREWARM] delaying %.0fs to let login settle", delay)
        time.sleep(delay)
    log.info("[PREWARM] starting (force=%s, min_ram_mb=%d)", force, min_ram_mb)
    t_start = time.perf_counter()

    if not force and not _fast_startup_enabled():
        log.info("[PREWARM] fast_startup disabled — exiting")
        return EXIT_DISABLED

    if not force:
        free = _free_ram_mb()
        if free is not None and free < min_ram_mb:
            log.info(
                "[PREWARM] free RAM %d MB < %d MB budget — skipping to avoid "
                "evicting the user's working set", free, min_ram_mb,
            )
            return EXIT_LOW_RAM

    if not force and _already_warmed():
        log.info("[PREWARM] already ran this boot session — skipping")
        return EXIT_OK

    _lower_io_priority()

    # 1) Imports — these warm torch/transformers/sympy/numpy/etc.
    try:
        _warm_imports()
    except ImportError as exc:
        log.warning("[PREWARM] required import missing — aborting: %s", exc)
        return EXIT_IMPORT_FAILED
    except Exception:
        log.exception("[PREWARM] import stage failed — aborting")
        return EXIT_IMPORT_FAILED

    # 2) Model weights — only if already cached.  On the very first run
    #    (model not yet downloaded) there is nothing to warm; the app's
    #    normal download path will populate the cache, and subsequent
    #    prewarms will pick it up.
    #
    # STARTUP-4: previously this walked ALL models--* directories in the
    #    HF cache, warming ~2.1 GB of inactive Whisper variants when the
    #    active backend was parakeet. Now we only warm the ACTIVE model
    #    (config.asr_backend / config.model_size) plus the tiny.en
    #    Whisper model that the AsrBackendRegistry would actually fall
    #    back to on CUDA/init failure.
    warmed_any = False

    # Determine which model cache dirs are relevant to the active config.
    active_cache_dirs = _active_model_cache_dirs()
    log.info(
        "[PREWARM] Active model cache dirs to warm: %s",
        [d.name for d in active_cache_dirs] or "(none cached yet)",
    )

    # Walk only the active + fallback cache dirs.
    try:
        for model_dir in active_cache_dirs:
            log.info("[PREWARM] Warming model: %s", model_dir.name)
            snapshots_dir = model_dir / "snapshots"
            if not snapshots_dir.exists():
                continue
            try:
                for snapshot_dir in snapshots_dir.iterdir():
                    if not snapshot_dir.is_dir():
                        continue
                    for f in snapshot_dir.rglob("*"):
                        if f.is_file() and f.suffix in (".bin", ".safetensors", ".pt", ".json", ".txt"):
                            _warm_file(f)
                    warmed_any = True
            except OSError as e:
                log.debug("[PREWARM] walk of %s failed: %s", model_dir.name, e)
    except Exception as e:
        log.debug("[PREWARM] HF cache walk failed: %s", e)

    if not warmed_any:
        log.info("[PREWARM] No model weights cached yet — skipping weights warmup")
        return EXIT_NO_MODEL

    elapsed = time.perf_counter() - t_start
    log.info("[PREWARM] complete (%.1fs)", elapsed)
    _mark_warmed()
    return EXIT_OK


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="voice_typer.server.prewarm",
        description="Prewarm the OS file cache for fast Voice Typer startup.",
    )
    p.add_argument(
        "--min-ram-mb", type=int, default=DEFAULT_MIN_FREE_RAM_MB,
        help=f"Skip prewarm if free RAM is below this (default {DEFAULT_MIN_FREE_RAM_MB}).",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Skip config and RAM guards (run unconditionally).",
    )
    p.add_argument(
        "--delay", type=float, default=0.0,
        help=(
            "Sleep this many seconds before starting.  Used by the HKCU "
            "Run-key fallback (which has no native delay) to let login settle."
        ),
    )
    return p.parse_args(argv)


def main() -> int:
    args = _parse_args()
    return run(min_ram_mb=args.min_ram_mb, force=args.force, delay=args.delay)


if __name__ == "__main__":
    sys.exit(main())
