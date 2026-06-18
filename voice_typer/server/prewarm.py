"""OS-level cache prewarming for fast cold-boot startup.

The dominant cost on first launch after a Windows reboot is *not* Python
or the model code — it is reading ~6 GB of files (torch + transformers +
Parakeet weights) off disk into the Windows file (standby) cache for the
first time.  Once those pages are resident, subsequent ``import torch``
and ``from_pretrained()`` calls hit RAM instead of the spindle, and
startup drops from ~45 s to a few seconds.

This module provides a standalone entry point — ``python -m
voice_typer.server.prewarm`` — that Task Scheduler runs shortly after
logon and again on idle.  It performs, in order, with **low I/O priority**
so it never competes with the user's real work:

1.  **Config / RAM guard.**  Bail out immediately if the user has disabled
    ``fast_startup`` in Settings, or if free RAM is below the budget
    (default 6 GB) — prewarming on a memory-starved machine would evict
    the user's working set, which is the opposite of helpful.
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

log = logging.getLogger("voice_typer.prewarm")

# Skip prewarming when free RAM is below this.  ~6 GB covers the torch
# package (4.2 GB) + Parakeet weights (2.4 GB) without catastrophically
# displacing the user's working set.  Tunable via --min-ram-mb.
DEFAULT_MIN_FREE_RAM_MB = 6 * 1024

# Read weights in this-sized chunk.  Small enough to keep the process's
# own working set tiny, large enough to amortise per-read overhead.
_READ_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MB


# ─── Exit codes (distinct for diagnostics in Task Scheduler history) ─────

EXIT_OK = 0
EXIT_DISABLED = 10          # user turned fast_startup off
EXIT_LOW_RAM = 20           # not enough free RAM to prewarm safely
EXIT_NO_MODEL = 30          # model not cached yet (first-ever run)
EXIT_IMPORT_FAILED = 40     # torch/transformers missing or broken


def _setup_logging() -> None:
    """Minimal logging — prewarm runs detached, so log to the app log file.

    PERF-NEW-011: previously this imported the full app.py (which
    pulls in sounddevice, faster_whisper, pynput, pystray, PIL, etc.)
    just to call _setup_logging().  That doubled the cold-start cost
    of the prewarm task.  Now we replicate the logging setup locally
    without importing app.py at all.
    """
    import os
    from pathlib import Path
    from datetime import datetime

    try:
        # Mirror app.py's _setup_logging: log to ~/.voice-typer/voice-typer.log
        log_dir = Path.home() / ".voice-typer"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "voice-typer.log"

        # Use a rotating file handler so the log doesn't grow unbounded
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(
            str(log_file), maxBytes=2_000_000, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        # Remove any existing handlers to avoid duplicate logs
        root.handlers.clear()
        root.addHandler(handler)
    except Exception:
        # Fall back to bare stderr so the script is still usable standalone.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] prewarm: %(message)s",
            stream=sys.stderr,
        )
    logging.getLogger("voice_typer").setLevel(logging.INFO)


# ─── Guards ──────────────────────────────────────────────────────────────

def _fast_startup_enabled() -> bool:
    """Return True if the user's config has fast_startup enabled.

    Defaults to True if the config can't be read (fail-open: the task is
    registered precisely because the feature is on; a missing/corrupt
    config should not defeat it).
    """
    try:
        from voice_typer.server.config import Config
        cfg = Config.load()
        return bool(getattr(cfg, "fast_startup", True))
    except Exception as exc:
        log.warning("[PREWARM] Could not read config (fail-open=True): %s", exc)
        return True


def _free_ram_mb() -> int | None:
    """Return available physical RAM in MB, or None if it can't be queried."""
    try:
        import psutil  # type: ignore[import-untyped]
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except ImportError:
        pass
    # Windows fallback via ctypes (no extra dependency).
    if sys.platform == "win32":
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
    """
    if sys.platform != "win32":
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
    """
    t0 = time.perf_counter()
    import torch  # noqa: F401  — import is the side effect we want
    elapsed = time.perf_counter() - t0
    log.info("[PREWARM] import torch: %.2fs", elapsed)

    t0 = time.perf_counter()
    from transformers import AutoModelForTDT, AutoProcessor  # noqa: F401
    elapsed = time.perf_counter() - t0
    log.info("[PREWARM] import transformers (AutoModelForTDT, AutoProcessor): %.2fs", elapsed)

    # Also touch the faster-whisper path used by the Whisper fallback —
    # cheap (ctranslate2 is much smaller than torch) and ensures the
    # CPU-fallback branch is warm too.
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

    if not force and not _fast_startup_enabled():
        log.info("[PREWARM] fast_startup disabled in config — exiting")
        return EXIT_DISABLED

    if not force:
        free = _free_ram_mb()
        if free is not None and free < min_ram_mb:
            log.info(
                "[PREWARM] free RAM %d MB < %d MB budget — skipping to avoid "
                "evicting the user's working set", free, min_ram_mb,
            )
            return EXIT_LOW_RAM

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
    # PERF-NEW-012: previously only prewarmed Parakeet weights.  Now
    #    walks ALL models--* directories in the HF cache to prewarm
    #    Whisper, Qwen, and Parakeet weights alike.
    warmed_any = False

    # Try Parakeet first (original path)
    weights = _find_parakeet_weights()
    if weights is not None:
        _warm_file(weights)
        warmed_any = True
        # Also warm the processor/tokenizer files (tiny but free).
        try:
            for sibling in weights.parent.iterdir():
                if sibling.is_file() and sibling.suffix in (".json",):
                    _warm_file(sibling)
        except OSError:
            pass

    # PERF-NEW-012: walk ALL model directories in the HF cache
    try:
        from voice_typer.server.config import _config_dir
        hf_cache = _config_dir() / "huggingface" / "hub"
        if hf_cache.exists():
            for model_dir in hf_cache.iterdir():
                if model_dir.is_dir() and model_dir.name.startswith("models--"):
                    log.info("[PREWARM] Warming model: %s", model_dir.name)
                    for snapshot_dir in (model_dir / "snapshots").iterdir() if (model_dir / "snapshots").exists() else []:
                        for f in snapshot_dir.rglob("*"):
                            if f.is_file() and f.suffix in (".bin", ".safetensors", ".pt", ".json", ".txt"):
                                _warm_file(f)
                    warmed_any = True
    except Exception as e:
        log.debug("[PREWARM] HF cache walk failed: %s", e)

    if not warmed_any:
        log.info("[PREWARM] No model weights cached yet — skipping weights warmup")
        return EXIT_NO_MODEL

    log.info("[PREWARM] complete")
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
