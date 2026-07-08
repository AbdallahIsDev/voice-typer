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
import random
import subprocess
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
# fires within the same boot session (defense-in-depth against the rare
# double-fire of BootTrigger + EventTrigger in the same boot session),
# the sentinel check skips it early.
#
# ADR-0009 Issue 3: the sentinel now stores THREE lines:
#   line 1: boot timestamp (Unix seconds) — dedup key (same as before)
#   line 2: elapsed seconds (float, e.g. "20.4") — used by the
#           get_prewarm_status IPC endpoint to show "Last run: 20.4s"
#           in the About page without re-probing the cache.
#   line 3: wall-clock completion time (ISO 8601) — used by
#           get_prewarm_status to show "Last run: 3 hours ago". Line 1
#           is the BOOT time, not the completion time, so without line 3
#           the UI would show the same "last run" for every prewarm in
#           the same boot session.
# `_already_warmed()` reads ONLY line 1, so old single-line and two-line
# sentinels written by previous builds continue to work (backward
# compatible).
#
# ADR-0009 Issue 1 (review fix C1): the sentinel path is resolved
# LAZILY by _sentinel_path() (not at module-import time) so it benefits
# from _resolve_hf_cache_dir()'s BootTrigger fallback chain. The old
# module-level constant `Path.home() / ".voice-typer" / ".prewarm-sentinel"`
# was evaluated at import time, which could produce a relative "~\..." path
# on Windows or a root "/..." path on POSIX when env vars were unset
# (exactly the BootTrigger scenario the fallbacks were added for).

# ADR-0009 Issue 4: PID file written at the start of run() and removed in
# a finally block. The app's model_manager.try_load() polls this file via
# is_prewarm_running() to decide whether to wait for prewarm to finish
# before loading the model (avoids the disk-I/O fight when the user logs
# in faster than prewarm can warm the cache).
#
# ADR-0009 Issue 1 (review fix C1): same as the sentinel — the PID file
# path is resolved lazily by _pid_file_path() so it uses the same
# resolution chain as the sentinel. This is CRITICAL for the app↔prewarm
# handshake: if prewarm (BootTrigger, env vars missing) writes the PID
# file to a different path than the app (post-logon, env vars set) looks
# for it, is_prewarm_running() returns False even though prewarm is
# running, and the disk-I/O fight ADR-0009 Issue 4 was designed to
# prevent still happens.

# ADR-0009 Issue 3: parameters for the _cache_ratio() probe. Reads this
# many random 4K pages from the model file and counts how many return in
# under the latency threshold (cache hit). 20 samples gives a 5% resolution
# which is plenty for the Hot/Partial/Cold UI label.
_CACHE_RATIO_SAMPLES = 20
_CACHE_RATIO_PAGE_BYTES = 4096
# 50µs threshold: SSD cold read ~100-500µs, RAM cache hit <10µs. Pages
# that return in under 50µs are considered cached.
_CACHE_RATIO_HIT_THRESHOLD_US = 50.0


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


def _resolve_hf_cache_dir() -> Path:
    """Resolve the HF cache directory, robust to pre-session execution.

    ADR-0009 Issue 1: at BootTrigger time, the user session may not be
    fully initialized. ``Path.home()`` relies on ``%USERPROFILE%``
    (Windows) / ``$HOME`` (POSIX), which may not be set yet. Fall back to
    platform-specific resolution so prewarm can find the cache even when
    fired before any user logs in.

    Resolution order:
      1. ``config._config_dir() / "huggingface"`` — the canonical app
         config path. Used by every other module, respects the
         monkey-patch hook tests rely on, and centralizes the
         ``Path.home() / ".voice-typer"`` convention. ONLY accepted if
         the result is an absolute path (review fix C2: a relative path
         like ``~/.voice-typer`` from an unexpanded ``~`` indicates env
         vars are missing, so we fall through to the fallbacks).
      2. Environment variable (``USERPROFILE`` on Windows, ``HOME`` on
         POSIX) — set during normal sessions and LogonTrigger firings.
         Used when ``_config_dir()`` itself fails or returns a relative
         path.
      3. Windows registry ``Volatile Environment\\USERPROFILE`` — set by
         Winlogon at session creation; readable from BootTrigger context
         because the registering user's hive is already mounted.
      4. POSIX ``pwd.getpwuid(os.getuid())`` — reads /etc/passwd; works
         from LaunchDaemon context where ``$HOME`` is not inherited.

    Returns the ``~/.voice-typer/huggingface`` directory. The directory
    may not exist (first-ever run, no model downloaded yet); callers must
    check ``.exists()`` before walking.
    """
    # Primary path: the canonical app config dir. This is what every
    # other module uses, and it's the path tests monkey-patch.
    primary_candidate: Path | None = None
    try:
        from voice_typer.server.config import _config_dir
        cache = _config_dir() / "huggingface"
        # Review fix C2: only accept absolute paths. A relative path
        # (e.g. "~/.voice-typer" from an unexpanded "~" when env vars
        # are missing) means _config_dir() couldn't resolve home — fall
        # through to the fallbacks instead of returning a bad path.
        if cache.is_absolute():
            if cache.exists():
                return cache
            primary_candidate = cache  # remember for the final fallback
    except Exception:
        log.debug("[PREWARM] _config_dir() lookup failed", exc_info=True)

    # Fallback 1: environment variables (LogonTrigger, normal session).
    # Review fix M2: validate the env var is set and produces an absolute
    # path before using it. Don't fall back to str(Path.home()) here —
    # Path.home() is what we're trying to avoid depending on.
    # Only run fallbacks if the primary candidate is not set (i.e.
    # _config_dir() failed or returned a relative path). If we have a
    # valid absolute primary candidate, it wins over the fallbacks —
    # the fallbacks exist for the BootTrigger scenario where
    # _config_dir() can't resolve home at all.
    if primary_candidate is None:
        home = os.environ.get("USERPROFILE") if is_windows() else os.environ.get("HOME")
        if home:
            cache = Path(home) / ".voice-typer" / "huggingface"
            if cache.is_absolute() and cache.exists():
                return cache
            if cache.is_absolute():
                primary_candidate = cache

    # Fallback 2: Windows registry (needed when BootTrigger fires before
    # session init). The Volatile Environment key is populated by Winlogon
    # at session creation; even in a pre-logon BootTrigger context, the
    # registering user's hive is mounted so the key is readable.
    # Only run if we still don't have a candidate.
    if primary_candidate is None and is_windows():
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Volatile Environment",
                0, winreg.KEY_READ,
            )
            try:
                profile = winreg.QueryValueEx(key, "USERPROFILE")[0]
            finally:
                winreg.CloseKey(key)
            if profile:
                return Path(profile) / ".voice-typer" / "huggingface"
        except OSError:
            pass
        except Exception:
            # Defensive: never let a registry read failure abort prewarm.
            log.debug("[PREWARM] Windows registry HF cache lookup failed", exc_info=True)

    # Fallback 3: POSIX getpwuid (needed when LaunchDaemon fires before
    # session init, or when $HOME is not inherited).
    # Only run if we still don't have a candidate.
    if primary_candidate is None and (is_linux() or is_macos()):
        try:
            import pwd
            pw = pwd.getpwuid(os.getuid())
            if pw.pw_dir:
                return Path(pw.pw_dir) / ".voice-typer" / "huggingface"
        except (KeyError, ImportError):
            pass
        except Exception:
            log.debug("[PREWARM] POSIX getpwuid HF cache lookup failed", exc_info=True)

    # Final best-effort: prefer the primary candidate (absolute path from
    # _config_dir() or env vars) even if it doesn't exist yet (first-ever
    # run). If we have no absolute candidate at all, fall back to
    # Path.home() — which may itself be wrong, but it's the best we can do.
    if primary_candidate is not None:
        return primary_candidate
    return Path.home() / ".voice-typer" / "huggingface"


def _config_root() -> Path:
    """Return the voice-typer config directory (parent of the HF cache).

    ADR-0009 Issue 1 (review fix C1): the sentinel and PID file paths
    are derived from this function so they use the SAME resolution chain
    as the HF cache dir. This ensures prewarm (BootTrigger, env vars
    possibly missing) and the app (post-logon, env vars set) agree on
    the path — critical for the PID-file handshake.
    """
    return _resolve_hf_cache_dir().parent


def _sentinel_path() -> Path:
    """Return the path to the prewarm sentinel file.

    ADR-0009 Issue 1 (review fix C1): resolved lazily via
    ``_config_root()`` so it benefits from the BootTrigger fallback chain.
    The old module-level constant ``Path.home() / ".voice-typer" /
    ".prewarm-sentinel"`` was evaluated at import time and could produce
    a relative "~\\..." path on Windows or a root "/..." path on POSIX
    when env vars were unset.
    """
    return _config_root() / ".prewarm-sentinel"


def _pid_file_path() -> Path:
    """Return the path to the prewarm PID file.

    ADR-0009 Issue 1 (review fix C1): same lazy resolution as
    ``_sentinel_path()``. Critical for the app↔prewarm handshake — both
    sides must agree on the path.
    """
    return _config_root() / ".prewarm.pid"


def _find_parakeet_weights() -> Path | None:
    """Locate the cached Parakeet ``model.safetensors``, or None if absent.

    ADR-0009 Issue 1: uses ``_resolve_hf_cache_dir()`` instead of
    ``_config_dir()`` so the lookup still works when prewarm is fired by
    the BootTrigger before the user session is fully initialized.
    """
    try:
        from voice_typer.server.parakeet_engine import _PARAKERT_MODEL_ID
    except Exception:
        return None

    cache_root = _resolve_hf_cache_dir() / "hub"
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

    ADR-0009 Issue 1: uses ``_resolve_hf_cache_dir()`` instead of
    ``_config_dir()`` so the lookup still works when prewarm is fired by
    the BootTrigger before the user session is fully initialized.
    """
    dirs: list[Path] = []
    try:
        from voice_typer.server.config import Config
        cfg = Config.load()
        cache_root = _resolve_hf_cache_dir() / "hub"
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
    """Return True if prewarm already succeeded in this boot session.

    ADR-0009 Issue 3: the sentinel file may now contain TWO lines
    (boot_ts + elapsed_s). We read only the first line so old
    single-line sentinels written by previous builds still work.
    """
    try:
        bt = _boot_time()
        sentinel = _sentinel_path()
        if bt is None or not sentinel.exists():
            return False
        content = sentinel.read_text()
        first_line = content.split("\n", 1)[0].strip()
        if not first_line:
            return False
        stored = int(first_line)
        return stored == bt
    except (ValueError, OSError):
        return False
    except Exception:
        return False


def _mark_warmed(elapsed_s: float) -> None:
    """Record successful prewarm for this boot session.

    ADR-0009 Issue 3: stores ``boot_ts\nelapsed_s`` so the
    ``get_prewarm_status`` IPC endpoint can show "Last run: 20.4s" in the
    About page without re-probing the cache. ``_already_warmed()`` reads
    only line 1, so the format change is backward-compatible.
    """
    try:
        bt = _boot_time()
        if bt is None:
            return
        sentinel = _sentinel_path()
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        # ADR-0009 Issue 3 (review fix H2): store THREE lines:
        #   line 1: boot timestamp (dedup key)
        #   line 2: elapsed seconds (for the About page)
        #   line 3: wall-clock completion time (ISO 8601) so the UI can
        #           show "Last run: 3 hours ago". Line 1 is the BOOT
        #           time, not the completion time.
        import datetime as _dt
        now_iso = _dt.datetime.now().isoformat(timespec="seconds")
        sentinel.write_text(f"{bt}\n{elapsed_s:.1f}\n{now_iso}")
    except Exception:
        # Review fix H5: log the failure (was silently swallowed).
        # A failed sentinel write means the next prewarm trigger will
        # re-warm (wasted work) and the About page will show "Last run:
        # None" — both are user-visible and need a diagnostic.
        log.warning(
            "[PREWARM] could not write sentinel file %s", _sentinel_path(),
            exc_info=True,
        )


# ─── Cache ratio probe (ADR-0009 Issue 3) ─────────────────────────────────

def _cache_ratio(path: Path, samples: int = _CACHE_RATIO_SAMPLES) -> float:
    """Estimate what fraction of ``path`` is in the OS standby cache.

    Returns 0.0 (cold) to 1.0 (fully cached).

    Reads ``samples`` random 4K pages and measures latency:
      - <50µs → page is in OS standby cache (RAM)
      - >50µs → page is on disk (cache miss)

    The slight cache-warming side effect (reading a cold page pulls it
    into cache) is acceptable and actually beneficial — it re-warms
    evicted pages, which is exactly what the user wants when they click
    "Refresh cache status" in the About page.

    Safe to call from the IPC handler thread: small reads, no
    allocation, no blocking syscalls beyond the read itself.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return 0.0
    if size < _CACHE_RATIO_PAGE_BYTES:
        return 0.0

    hot = 0
    try:
        with open(path, "rb") as f:
            for _ in range(samples):
                offset = random.randint(0, size - _CACHE_RATIO_PAGE_BYTES)
                f.seek(offset)
                t0 = time.perf_counter_ns()
                f.read(_CACHE_RATIO_PAGE_BYTES)
                elapsed_us = (time.perf_counter_ns() - t0) / 1000.0
                if elapsed_us < _CACHE_RATIO_HIT_THRESHOLD_US:
                    hot += 1
    except OSError:
        return 0.0
    return hot / samples if samples > 0 else 0.0


# ─── PID file + process-liveness helpers (ADR-0009 Issue 4) ───────────────

def _write_pid_file() -> None:
    """Write the current PID to the prewarm PID file.

    Called at the start of the warming phase (after all early-exit
    guards). The app's ``is_prewarm_running()`` polls this file to
    decide whether to wait for prewarm to finish before loading the
    model. ``_remove_pid_file()`` removes it in a finally block.
    """
    try:
        pid_file = _pid_file_path()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))
    except OSError as exc:
        log.debug("[PREWARM] could not write PID file: %s", exc)


def _remove_pid_file() -> None:
    """Remove the prewarm PID file on exit.

    Idempotent: ``missing_ok=True`` so this is safe to call even if
    ``_write_pid_file()`` never ran (e.g. run() bailed out before the
    warming phase).
    """
    try:
        _pid_file_path().unlink(missing_ok=True)
    except OSError as exc:
        log.debug("[PREWARM] could not remove PID file: %s", exc)


def _process_alive(pid: int) -> bool:
    """Return True if the process with ``pid`` is currently running.

    Cross-platform:
      - Windows: ``OpenProcess`` + ``GetExitCodeProcess`` (STILL_ACTIVE=259)
      - POSIX: ``os.kill(pid, 0)`` (raises OSError if the process is dead)

    ADR-0009 Issue 4: used by ``is_prewarm_running()`` to check the PID
    file. Treating "process does not exist" as "not running" (rather
    than raising) is intentional — a stale PID file pointing at a
    recycled PID is a known failure mode, and the worst case is that
    the app skips the wait and loads the model from a possibly-cold
    cache, which is exactly the pre-ADR-0009 behavior.
    """
    if pid <= 0:
        return False
    if is_windows():
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except OSError:
            return False
        except Exception:
            log.debug("[PREWARM] Windows process liveness check failed", exc_info=True)
            return False
    else:
        import errno
        try:
            os.kill(pid, 0)
            return True
        except OSError as exc:
            # ESRCH = no such process → not alive.
            # EPERM = process exists but not ours → treat as alive
            # (the prewarm process is owned by the same user, so EPERM
            # shouldn't happen in practice; treating it as alive is
            # the safe default — the worst case is we wait for a
            # prewarm that already finished, which is bounded by the
            # timeout in wait_for_prewarm()).
            if exc.errno == errno.ESRCH:
                return False
            return exc.errno == errno.EPERM


def _read_process_cmdline_windows(pid: int) -> str | None:
    """Read the full command line of another Windows process, no elevation.

    Tasks 1+2: the previous Windows check used QueryFullProcessImageNameW
    and only verified the image was python.exe — which means ANY Python
    process (including pytest) passed the "is prewarm" check, causing
    the PID recycling guard to fail on Windows.

    This implementation walks the target process's PEB
    (Process Environment Block) to read
    RTL_USER_PROCESS_PARAMETERS.CommandLine, the same technique Task
    Manager and Process Explorer use. It works without elevation for
    processes owned by the same user (which is always the case for
    prewarm — it's launched by the user's scheduled task).

    Architecture:
      1. OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ).
         Both flags are granted to same-user processes without elevation.
      2. NtQueryInformationProcess(ProcessBasicInformation) → PROCESS_BASIC_INFORMATION.
         This gives us the PEB address inside the target's address space.
      3. ReadProcessMemory(PEB) → read the ProcessParameters pointer.
      4. ReadProcessMemory(RTL_USER_PROCESS_PARAMETERS) → read the
         CommandLine UNICODE_STRING (Length, Buffer pointer).
      5. ReadProcessMemory(Buffer) → read the UTF-16 command line.

    Falls back to WMI (powershell Get-CimInstance Win32_Process) if the
    PEB walk fails (protected process, PEB paged out, 32-bit/64-bit
    mismatch, etc.). WMI is slower (~200ms) but works in all cases
    where the caller has at least PROCESS_QUERY_LIMITED_INFORMATION.

    Returns the command line as a UTF-8 string, or None if it can't be
    read (process dead, access denied, all methods failed).
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll

    # ── Struct definitions ──────────────────────────────────────────
    # N801: class names follow CapWords per ruff; the Win32 SDK names
    # (_UNICODE_STRING, _PROCESS_BASIC_INFORMATION) are preserved in
    # comments for anyone cross-referencing the Microsoft docs.
    class UnicodeString(ctypes.Structure):
        """NT UNICODE_STRING — length-prefixed UTF-16 string pointer."""
        _fields_ = [
            ("Length", wintypes.USHORT),          # bytes, excluding NUL
            ("MaximumLength", wintypes.USHORT),   # bytes, including NUL
            ("Buffer", wintypes.LPWSTR),          # pointer into target's memory
        ]

    class ProcessBasicInformation(ctypes.Structure):
        """NtQueryInformationProcess output for ProcessBasicInformation."""
        _fields_ = [
            ("ExitStatus", wintypes.LONG),        # NTSTATUS
            ("PebBaseAddress", wintypes.LPVOID),  # PEB* inside target's memory
            ("AffinityMask", wintypes.ULONG_PTR),
            ("BasePriority", wintypes.LONG),
            ("UniqueProcessId", wintypes.ULONG_PTR),
            ("InheritedFromUniqueProcessId", wintypes.ULONG_PTR),
        ]

    # ── Function signatures (best practice: set argtypes/restype) ───
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010

    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]

    # NtQueryInformationProcess is in ntdll, not kernel32.
    # PROCESSINFOCLASS.ProcessBasicInformation = 0 (Win32 SDK enum value).
    process_info_class_basic = 0
    ntdll.NtQueryInformationProcess.restype = wintypes.LONG  # NTSTATUS
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, wintypes.ULONG, wintypes.LPVOID,
        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
    ]

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid,
    )
    if not handle:
        return None  # access denied or process dead
    try:
        # ── Step 1: NtQueryInformationProcess → PEB address ────────
        pbi = ProcessBasicInformation()
        returned = wintypes.ULONG(0)
        status = ntdll.NtQueryInformationProcess(
            handle, process_info_class_basic, ctypes.byref(pbi),
            ctypes.sizeof(pbi), ctypes.byref(returned),
        )
        # NTSTATUS >= 0 means success (0 = STATUS_SUCCESS, >0 = informational)
        if status < 0 or not pbi.PebBaseAddress:
            return _read_process_cmdline_windows_wmi(pid)  # fallback
        peb_addr = pbi.PebBaseAddress

        # ── Step 2: Read PEB → ProcessParameters pointer ───────────
        # On 64-bit Windows, ProcessParameters is at PEB offset 0x20.
        # On 32-bit Windows, it's at PEB offset 0x10. We detect the
        # pointer size via sizeof(ULONG_PTR) (8 on 64-bit, 4 on 32-bit).
        is_64bit = ctypes.sizeof(wintypes.ULONG_PTR) == 8
        params_offset = 0x20 if is_64bit else 0x10

        params_ptr = wintypes.LPVOID()
        bytes_read = ctypes.c_size_t(0)
        if not kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(ctypes.cast(peb_addr, ctypes.c_void_p).value + params_offset),
            ctypes.byref(params_ptr),
            ctypes.sizeof(params_ptr),
            ctypes.byref(bytes_read),
        ):
            return _read_process_cmdline_windows_wmi(pid)  # fallback
        if not params_ptr.value:
            return _read_process_cmdline_windows_wmi(pid)  # fallback

        # ── Step 3: Read RTL_USER_PROCESS_PARAMETERS → CommandLine ─
        # CommandLine is a UNICODE_STRING. Its offset within
        # RTL_USER_PROCESS_PARAMETERS is 0x70 on 64-bit, 0x40 on 32-bit.
        cmd_offset = 0x70 if is_64bit else 0x40
        cmd_unicode = UnicodeString()
        if not kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(ctypes.cast(params_ptr, ctypes.c_void_p).value + cmd_offset),
            ctypes.byref(cmd_unicode),
            ctypes.sizeof(cmd_unicode),
            ctypes.byref(bytes_read),
        ):
            return _read_process_cmdline_windows_wmi(pid)  # fallback

        # ── Step 4: Read the actual command-line string ────────────
        if cmd_unicode.Length == 0 or not cmd_unicode.Buffer:
            return ""  # process has no command line (rare)
        # Length is in bytes; UTF-16 = 2 bytes per char. Read into a
        # wchar array sized to the char count + 1 (NUL terminator).
        char_count = cmd_unicode.Length // 2
        buf = ctypes.create_unicode_buffer(char_count + 1)
        if not kernel32.ReadProcessMemory(
            handle, cmd_unicode.Buffer, buf,
            cmd_unicode.Length, ctypes.byref(bytes_read),
        ):
            return _read_process_cmdline_windows_wmi(pid)  # fallback
        return buf.value
    except OSError:
        return None
    except Exception:
        log.debug("[PREWARM] Windows PEB walk failed", exc_info=True)
        return _read_process_cmdline_windows_wmi(pid)  # fallback
    finally:
        kernel32.CloseHandle(handle)


def _read_process_cmdline_windows_wmi(pid: int) -> str | None:
    """WMI fallback for reading a Windows process's command line.

    Used when the PEB walk (_read_process_cmdline_windows) fails (e.g.
    protected process, PEB paged out, 32/64-bit mismatch). Spawns a
    powershell subprocess (~200ms) to query Get-CimInstance Win32_Process.

    Returns the command line string, or None if WMI fails.
    """
    try:
        # Using Get-CimInstance instead of the deprecated wmic CLI.
        # -Filter avoids fetching all processes (faster, less memory).
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-CimInstance Win32_Process -Filter "
                f"'ProcessId={pid}').CommandLine",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            cmdline = result.stdout.strip()
            return cmdline if cmdline else None
        return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    except Exception:
        log.debug("[PREWARM] Windows WMI command-line read failed", exc_info=True)
        return None


def _process_is_prewarm(pid: int) -> bool:
    """Best-effort check that ``pid`` is actually a prewarm process.

    ADR-0009 Issue 4 (review fix H4) + Tasks 1+2: after prewarm exits
    normally (PID file removed by finally) or is killed
    (SIGKILL/TerminateProcess — finally doesn't run), the OS may recycle
    the PID for an unrelated process. Without this check,
    ``is_prewarm_running()`` returns True for the unrelated process, and
    ``wait_for_prewarm()`` blocks the model load for the full 60s
    timeout on every app launch until the unrelated process exits.

    Detection is best-effort but cross-platform consistent:
      - Linux: read /proc/{pid}/cmdline and check for "prewarm" +
        "voice_typer".
      - macOS: use ``ps -o command= -p {pid}`` (no /proc on macOS).
      - Windows (Tasks 1+2): walk the target process's PEB via
        NtQueryInformationProcess + ReadProcessMemory to read the actual
        command line, then check for "prewarm" + "voice_typer". Falls
        back to WMI (powershell Get-CimInstance) if the PEB walk fails.
        This is the same technique Task Manager/Process Explorer use and
        works without elevation for same-user processes. The previous
        coarse image-name check (only verified "python" was in the image
        path) could not distinguish prewarm from pytest or any other
        Python process, causing test_is_prewarm_running_pid_recycled to
        fail on Windows.

    Returns True if the process looks like prewarm, False if it doesn't
    (or if the check fails — fail-safe toward "not prewarm" so the stale
    PID file gets cleaned up).
    """
    if pid <= 0:
        return False
    # ── Linux: /proc/{pid}/cmdline ─────────────────────────────────
    if is_linux():
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            cmdline_str = cmdline.replace(b"\x00", b" ").decode("utf-8", "ignore")
            return "prewarm" in cmdline_str and "voice_typer" in cmdline_str
        except OSError:
            return False
    # ── macOS: ps ──────────────────────────────────────────────────
    if is_macos():
        try:
            result = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            cmdline = result.stdout
            return "prewarm" in cmdline and "voice_typer" in cmdline
        except (OSError, subprocess.TimeoutExpired):
            return False
    # ── Windows: PEB walk + WMI fallback (Tasks 1+2) ───────────────
    if is_windows():
        cmdline = _read_process_cmdline_windows(pid)
        if cmdline is None:
            # Couldn't read the command line — fail safe (treat as not
            # prewarm so the stale PID file gets cleaned up). This is
            # the correct default: if we can't verify the process IS
            # prewarm, we shouldn't block the app for 60s.
            return False
        return "prewarm" in cmdline and "voice_typer" in cmdline
    return False


def is_prewarm_running() -> bool:
    """Return True if a prewarm process is currently running.

    ADR-0009 Issue 4: checks for the prewarm PID file written by the
    prewarm process at startup. If the PID file exists and the process
    is alive AND looks like prewarm (review fix H4: PID recycling
    guard), prewarm is running. If the PID file is missing, points at a
    dead process, or points at a recycled unrelated process, prewarm is
    not running and the stale PID file is cleaned up.

    Safe to call from any thread (the app's model_manager.try_load()
    calls this from a daemon thread).
    """
    pid_file = _pid_file_path()
    if not pid_file.exists():
        return False
    try:
        pid_text = pid_file.read_text().strip()
        pid = int(pid_text)
    except (ValueError, OSError):
        return False
    if not _process_alive(pid):
        return False
    # H4: the PID is alive, but is it actually prewarm? If the OS
    # recycled the PID for an unrelated process, treat the PID file as
    # stale and clean it up so the next wait_for_prewarm() doesn't block.
    if not _process_is_prewarm(pid):
        log.info(
            "[PREWARM] PID file points at pid %d which is not prewarm "
            "(PID recycled) — removing stale PID file", pid,
        )
        _remove_pid_file()
        return False
    return True


def wait_for_prewarm(timeout_s: float = 60.0) -> bool:
    """Wait for prewarm to finish if it's running.

    ADR-0009 Issue 4: returns True if prewarm completed (or wasn't
    running), False if the timeout was reached. Polls every 500ms.

    Called by ``model_manager.try_load()`` before loading the model so
    the app doesn't fight prewarm for disk I/O when the user logs in
    faster than prewarm can warm the cache.

    Task 5: when this returns False (timeout), the caller should call
    ``spawn_background_prewarm()`` to ensure prewarm restarts for the
    next app launch (the current boot's prewarm was preempted by the
    app's model load and may not have finished warming).
    """
    if not is_prewarm_running():
        return True  # nothing to wait for

    log.info(
        "[PREWARM] waiting for prewarm to finish (timeout=%.0fs)", timeout_s,
    )
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        time.sleep(0.5)
        if not is_prewarm_running():
            # Review fix L10: don't claim "warm cache" — prewarm might
            # have exited with EXIT_LOW_RAM (cache NOT warm). Just say
            # "finished".
            log.info("[PREWARM] prewarm finished — proceeding")
            return True

    log.warning(
        "[PREWARM] prewarm still running after %.0fs — proceeding anyway",
        timeout_s,
    )
    # Review fix M3: attempt cleanup of a stale PID file. If the PID
    # was recycled (per H4), is_prewarm_running() already cleaned it
    # up on the next call. If prewarm is genuinely still running, the
    # PID file is NOT removed (prewarm's finally block will do that).
    # This is a no-op in the common case and helps the next launch
    # avoid re-blocking on a stale PID.
    return False


def spawn_background_prewarm(force: bool = True) -> int | None:
    """Spawn a detached prewarm subprocess for the next app launch.

    Task 5: when ``wait_for_prewarm()`` times out (prewarm is still
    running after 60s), the app loads the model from a cold cache
    (~50s). But prewarm was preempted — the app's disk I/O starved it.
    This function ensures prewarm restarts (or continues) so the cache
    is warm for the NEXT time the app starts.

    Launches ``pythonw.exe -m voice_typer.server.prewarm [--force]`` as
    a detached subprocess. The subprocess survives the app's lifetime
    (detached process group on POSIX, CREATE_NO_WINDOW on Windows).

    Parameters
    ----------
    force : bool
        If True (default), pass ``--force`` to bypass the boot-sentinel
        dedup. This is correct for the timeout case: if we're calling
        this, the current boot's prewarm hasn't finished, so we want to
        re-run it unconditionally.

    Returns the subprocess PID on success, or None if the spawn failed.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    python_bin = _sys.executable
    if is_windows():
        pythonw = _Path(_sys.executable).parent / "pythonw.exe"
        if pythonw.exists():
            python_bin = str(pythonw)

    cmd = [python_bin, "-m", "voice_typer.server.prewarm"]
    if force:
        cmd.append("--force")

    log.info("[PREWARM] spawning background prewarm: %s", " ".join(cmd))

    kwargs: dict = {}
    if is_windows():
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    kwargs["stdout"] = subprocess.DEVNULL
    kwargs["stderr"] = subprocess.DEVNULL
    kwargs["stdin"] = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(cmd, **kwargs)
        log.info(
            "[PREWARM] background prewarm spawned (pid=%d, force=%s)",
            proc.pid, force,
        )
        return proc.pid
    except FileNotFoundError as exc:
        log.warning("[PREWARM] could not spawn background prewarm: %s", exc)
        return None
    except OSError as exc:
        log.warning("[PREWARM] could not spawn background prewarm: %s", exc)
        return None


# ─── Status query (ADR-0009 Issue 3) ──────────────────────────────────────

def get_prewarm_status() -> dict:
    """Return a snapshot of the prewarm cache state for the UI.

    ADR-0009 Issue 3: called by the ``get_prewarm_status`` IPC handler
    to populate the "Cache Status" card in the About page. Returns:

      ``{
        "last_run": "2026-07-08T13:48:49" | None,   # ISO timestamp
        "elapsed_s": 20.4 | None,                    # float seconds
        "cache_ratio": 0.73,                         # 0.0–1.0
        "cache_label": "hot" | "partial" | "cold" | "unknown",
        "cached_bytes": 1750000000,                  # estimated bytes in RAM
        "total_bytes": 2400000000,                   # total model file size
        "prewarm_running": False,                    # is prewarm running now?
      }``

    The probe is best-effort: if the sentinel is missing or the model
    file is absent, the fields degrade gracefully to ``None`` / 0.0 /
    ``"unknown"`` rather than raising.
    """
    # ── Sentinel: last_run + elapsed_s ──────────────────────────────
    last_run: str | None = None
    elapsed_s: float | None = None
    sentinel = _sentinel_path()
    sentinel_exists = sentinel.exists()
    try:
        if sentinel_exists:
            content = sentinel.read_text()
            lines = content.split("\n")
            # Line 1: boot timestamp (dedup key). Always present.
            boot_ts: int | None = None
            if lines and lines[0].strip():
                try:
                    boot_ts = int(lines[0].strip())
                except ValueError:
                    boot_ts = None
            # Line 2: elapsed seconds (float). Present in 2-line and 3-line sentinels.
            if len(lines) > 1 and lines[1].strip():
                try:
                    elapsed_s = float(lines[1].strip())
                except ValueError:
                    elapsed_s = None
            # Line 3: wall-clock completion time (ISO 8601). Present in
            # 3-line sentinels only (review fix H2). This is the ACTUAL
            # completion time, not the boot time — use it for last_run
            # so the UI shows "3 hours ago" correctly.
            if len(lines) > 2 and lines[2].strip():
                last_run = lines[2].strip()
            elif boot_ts is not None:
                # Backward-compat with 1-line and 2-line sentinels:
                # approximate last_run as boot_ts + elapsed_s. This is
                # the boot time plus the prewarm duration, which is the
                # best estimate of when prewarm completed.
                from datetime import datetime
                approx_ts = boot_ts + (elapsed_s if elapsed_s is not None else 0)
                last_run = datetime.fromtimestamp(approx_ts).isoformat()
    except (ValueError, OSError):
        pass
    except Exception:
        log.debug("[PREWARM] get_prewarm_status sentinel read failed", exc_info=True)

    # ── Cache ratio probe (review fix H3: weighted by file size) ────
    cache_ratio = 0.0
    cached_bytes = 0
    total_bytes = 0
    active_dirs: list[Path] = []
    try:
        active_dirs = _active_model_cache_dirs()
        sizes: list[int] = []
        ratios: list[float] = []
        for d in active_dirs:
            snapshots_dir = d / "snapshots"
            if not snapshots_dir.is_dir():
                continue
            for snapshot in snapshots_dir.iterdir():
                if not snapshot.is_dir():
                    continue
                weights = snapshot / "model.safetensors"
                if weights.exists():
                    try:
                        size = weights.stat().st_size
                    except OSError:
                        continue
                    sizes.append(size)
                    ratios.append(_cache_ratio(weights))
                    total_bytes += size
        if sizes and total_bytes > 0:
            # H3: weighted sum — each file contributes size * ratio to
            # cached_bytes. The overall cache_ratio is then
            # cached_bytes / total_bytes, which correctly accounts for
            # heterogeneous file sizes (a 2.4 GB file at 80% + a 1 MB
            # file at 100% → 1.92 GB cached, not 2.16 GB).
            cached_bytes = sum(int(s * r) for s, r in zip(sizes, ratios, strict=True))
            cache_ratio = cached_bytes / total_bytes
    except Exception:
        log.debug("[PREWARM] get_prewarm_status cache probe failed", exc_info=True)

    # ── Label: hot / partial / cold / unknown ───────────────────────
    # M4: reuse sentinel_exists and active_dirs instead of re-calling.
    active_dirs_any = bool(active_dirs)
    if not sentinel_exists and not active_dirs_any:
        label = "unknown"
    elif cache_ratio >= 0.9:
        label = "hot"
    elif cache_ratio >= 0.1:
        label = "partial"
    else:
        label = "cold"

    return {
        "last_run": last_run,
        "elapsed_s": elapsed_s,
        "cache_ratio": round(cache_ratio, 2),
        "cache_label": label,
        "cached_bytes": cached_bytes,
        "total_bytes": total_bytes,
        "prewarm_running": is_prewarm_running(),
    }


def active_dirs_exist() -> bool:
    """Return True if any HF cache model dir exists.

    ADR-0009 Issue 3: helper for ``get_prewarm_status()`` so the label
    can distinguish "never ran + no model" (``unknown``) from "ran but
    cache evicted" (``cold``). Kept tiny so it can be monkey-patched in
    tests without spinning up the full HF cache walk.
    """
    try:
        return bool(_active_model_cache_dirs())
    except Exception:
        return False


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

    ADR-0009 Issue 2: the sentinel check now runs BEFORE the RAM check.
    The sentinel is the cheapest guard (one stat + one read of a tiny
    file) and produces the correct log message when the trigger re-fires
    (e.g. on Windows session unlock). The previous order — RAM first —
    logged "free RAM < budget — skipping" on re-fire, which misled users
    into thinking the sentinel had failed.

    ADR-0009 Issue 4: writes a PID file at the start of the warming
    phase and removes it in a finally block. The app's
    ``model_manager.try_load()`` polls this file via
    ``is_prewarm_running()`` to decide whether to wait for prewarm to
    finish before loading the model (avoids the disk-I/O fight when the
    user logs in faster than prewarm can warm the cache).
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

    # SENTINEL FIRST — cheapest check, prevents all redundant work.
    # ADR-0009 Issue 2: this also produces the correct log message when
    # the trigger re-fires (e.g., on Windows session unlock).
    if not force and _already_warmed():
        log.info("[PREWARM] already ran this boot session — skipping")
        return EXIT_OK

    # RAM GUARD SECOND — only check if we're actually going to run.
    if not force:
        free = _free_ram_mb()
        if free is not None and free < min_ram_mb:
            log.info(
                "[PREWARM] free RAM %d MB < %d MB budget — skipping to avoid "
                "evicting the user's working set", free, min_ram_mb,
            )
            return EXIT_LOW_RAM

    _lower_io_priority()

    # ADR-0009 Issue 4: write the PID file AFTER all the early-exit
    # guards so we don't leak a PID file for a process that bailed out
    # without doing any work. The finally block below removes it.
    _write_pid_file()

    # ADR-0009 Issue 4: ensure the PID file is always removed, even if
    # the warming pipeline raises or returns early. Without this, the
    # app's wait_for_prewarm() would block forever on a stale PID file
    # pointing at a dead process.
    try:
        return _run_warming_pipeline(min_ram_mb, force, t_start)
    finally:
        _remove_pid_file()


def _run_warming_pipeline(
    min_ram_mb: int, force: bool, t_start: float,
) -> int:
    """Run the import + weights warming pipeline.

    ADR-0009 Issue 4: extracted from run() so the PID-file finally block
    in run() can wrap the entire warming phase without duplicating the
    pipeline logic. Returns the exit code.
    """
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
    # Review fix H1: wrap _warm_file() per-file so one unreadable file
    # (permission denied, file locked, IO error, file deleted mid-walk)
    # doesn't abort the entire warming pipeline. Set warmed_any per-
    # snapshot so a fully-unreadable snapshot doesn't count as warmed.
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
                    snapshot_warmed_any = False
                    for f in snapshot_dir.rglob("*"):
                        if f.is_file() and f.suffix in (".bin", ".safetensors", ".pt", ".json", ".txt"):
                            try:
                                _warm_file(f)
                                snapshot_warmed_any = True
                            except OSError as e:
                                log.debug(
                                    "[PREWARM] could not warm %s: %s", f, e,
                                )
                    if snapshot_warmed_any:
                        warmed_any = True
            except OSError as e:
                log.debug("[PREWARM] walk of %s failed: %s", model_dir.name, e)
    except Exception as e:
        log.debug("[PREWARM] HF cache walk failed: %s", e)

    # Review fix M6: distinguish "no model cache dirs" (first-ever run)
    # from "dirs exist but no files could be warmed" (permissions issue).
    if not active_cache_dirs:
        log.info("[PREWARM] No model cache dirs found — first-ever run")
        return EXIT_NO_MODEL
    if not warmed_any:
        log.warning(
            "[PREWARM] Model dirs exist but no files could be warmed "
            "(permissions? file locks?) — skipping sentinel write"
        )
        return EXIT_NO_MODEL

    elapsed = time.perf_counter() - t_start
    log.info("[PREWARM] complete (%.1fs)", elapsed)
    # ADR-0009 Issue 3: store the elapsed time in the sentinel so the
    # get_prewarm_status IPC endpoint can show "Last run: 20.4s" in the
    # About page without re-probing.
    _mark_warmed(elapsed)
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
    # Task 4: --status prints the prewarm cache state and exits without
    # running the warming pipeline. Pure CLI — no Electron, no IPC.
    # Useful for remote diagnostics, SSH sessions, or automation scripts.
    p.add_argument(
        "--status", action="store_true",
        help=(
            "Print the prewarm cache status (last run, cache ratio, "
            "Hot/Partial/Cold label, etc.) as JSON and exit. Does NOT "
            "run the warming pipeline."
        ),
    )
    return p.parse_args(argv)


def _print_status() -> int:
    """Task 4: print the prewarm cache status as JSON and return exit code.

    Calls ``get_prewarm_status()`` and prints the result as a JSON blob
    (with an added ``sentinel_path`` field for diagnostics). Exits with
    code 0 on success, 1 if the status probe raised.
    """
    import json
    try:
        status = get_prewarm_status()
        # Add the sentinel path for diagnostics (useful for support
        # engineers to verify the sentinel file location).
        status["sentinel_path"] = str(_sentinel_path())
        # Add the PID file path too (helps diagnose stale-PID issues).
        status["pid_file_path"] = str(_pid_file_path())
        print(json.dumps(status, indent=2, default=str))
        return 0
    except Exception as exc:
        log.error("[PREWARM] --status failed: %s", exc, exc_info=True)
        # Print a minimal error JSON so scripts can parse it.
        print(json.dumps({
            "error": str(exc),
            "last_run": None,
            "elapsed_s": None,
            "cache_ratio": 0.0,
            "cache_label": "unknown",
            "cached_bytes": 0,
            "total_bytes": 0,
            "prewarm_running": False,
            "sentinel_path": str(_sentinel_path()),
            "pid_file_path": str(_pid_file_path()),
        }, indent=2))
        return 1


def main() -> int:
    args = _parse_args()
    # Task 4: --status short-circuits before any warming work.
    if args.status:
        return _print_status()
    return run(min_ram_mb=args.min_ram_mb, force=args.force, delay=args.delay)


if __name__ == "__main__":
    sys.exit(main())
