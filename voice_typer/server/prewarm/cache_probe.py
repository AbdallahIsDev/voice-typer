"""HF cache probing + file-warming primitives."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import random
import sys
import time
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from voice_typer.server.duration import format_duration
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger("voice_typer.server.prewarm")

# Read weights in this-sized chunk.  Small enough to keep the process's
_READ_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MB

# Only warm file types whose bytes actually contribute to import-time
_WARM_PACKAGE_SUFFIXES: frozenset[str] = frozenset({".pyc", ".so", ".pyd", ".dll", ".dylib", ".json", ".txt"})

# directory names whose contents NEVER contribute to import-time
_WARM_PACKAGE_SKIP_DIRS: frozenset[str] = frozenset({"tests", "test", "docs", "__pycache__"})

# ADR-0009 Issue 3: parameters for the _cache_ratio() probe. Reads this
_CACHE_RATIO_SAMPLES = 20
_CACHE_RATIO_PAGE_BYTES = 4096
# 50µs threshold: SSD cold read ~100-500µs, RAM cache hit <10µs. Pages
_CACHE_RATIO_HIT_THRESHOLD_US = 50.0

# Whisper model sizes that are valid fallback targets.
_WHISPER_FALLBACK_MODEL_SIZE = "tiny"

# Weight-file suffixes warmed by :func:`_warm_model_weights` and probed
_MODEL_WEIGHTS_SUFFIXES: frozenset[str] = frozenset({".bin", ".onnx", ".safetensors"})

# A weight/file is considered "already hot" at this sampled cache
_PREWARM_SKIP_HOT_RATIO = 0.9


def _iter_warmable_files(root: Path) -> Iterator[Path]:
    """Iterate warmable files under ``root`` without per-file ``stat()``."""
    seen: set[Path] = set()
    stack: list[Path] = [root]
    suffixes = _WARM_PACKAGE_SUFFIXES
    skip_dirs = _WARM_PACKAGE_SKIP_DIRS
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    # Follow directories (iterative, not recursive —
                    try:
                        is_dir = entry.is_dir(follow_symlinks=True)
                    except OSError:
                        # broken symlink / permission denied on the
                        continue
                    if is_dir:
                        # Skip-dir pruning INSIDE the walk: these
                        if (
                            entry.name in skip_dirs
                            or entry.name.endswith(".dist-info")
                            or entry.name.endswith(".egg-info")
                        ):
                            continue
                        try:
                            real = Path(entry.path).resolve()
                        except OSError:
                            continue
                        if real in seen:
                            continue
                        seen.add(real)
                        stack.append(Path(entry.path))
                        continue
                    # File: filter by suffix with the SAME Path.suffix
                    if Path(entry.name).suffix not in suffixes:
                        continue
                    yield Path(entry.path)
        except (FileNotFoundError, PermissionError, NotADirectoryError):
            # Root may have been deleted between list + scandir; or we
            continue


def _warm_package_files(pkg_name: str) -> int:
    """Read a package's installed files into the OS page cache WITHOUT"""
    spec = importlib.util.find_spec(pkg_name)
    if spec is None:
        log.debug("[PREWARM] %s not installed, skip file warmup", pkg_name)
        return 0
    if pkg_name in sys.modules:
        # find_spec must never import, but if it ever does we must not claim
        log.debug("[PREWARM] %s already imported, skip", pkg_name)
        return 0

    roots: list[Path] = []
    if spec.submodule_search_locations:
        roots.extend(Path(p) for p in spec.submodule_search_locations)
    elif spec.origin and spec.origin != "namespace":
        roots.append(Path(spec.origin))

    if not roots:
        log.debug("[PREWARM] %s has no locatable files, skip", pkg_name)
        return 0

    total = 0
    t0 = time.perf_counter()
    for root in roots:
        # Single shared walker: os.scandir-based (no per-file stat),
        for path in _iter_warmable_files(root):
            try:
                total += _warm_file(path)
            except OSError as exc:
                log.debug("[PREWARM] skip %s: %s", path, exc)
    elapsed = time.perf_counter() - t0
    # Defensive: file warmup must never have imported the package.
    assert pkg_name not in sys.modules, f"{pkg_name} was imported during file warmup, must stay unimported"
    # C-LOG-2: lifecycle-completion log line carries the canonical
    log.info(
        "[PREWARM] file-warmed %s: %.0f MB%s",
        pkg_name,
        total / (1024 * 1024),
        format_duration(elapsed),
    )
    return total


@lru_cache(maxsize=1)
def _cached_active_config():
    """Return the active ``Config`` instance, cached per prewarm run ()."""
    try:
        from voice_typer.server.config import Config

        return Config.load()
    except Exception:
        log.debug("[PREWARM] Config.load() failed, using default backend", exc_info=True)
        return None


# Plan §6.2: the warm list for the ONNX-only runtime
_WORKER_WARM_PACKAGES: tuple[str, ...] = (
    "onnxruntime",
    "ctranslate2",
    "numpy",
    "scipy",
    "faster_whisper",
)


def _warm_imports() -> None:
    """Page the runtime-pack libraries' files into the OS cache (no import)."""
    t0 = time.perf_counter()
    warmed: list[str] = []
    for pkg in _WORKER_WARM_PACKAGES:
        try:
            bytes_read = _warm_package_files(pkg)
        except Exception as exc:
            # Best-effort: a missing package (e.g. ``scipy`` not
            log.debug("[PREWARM] %s not warmable (skipping): %s", pkg, exc)
            continue
        if bytes_read > 0:
            warmed.append(pkg)
    # Warm the active model's WEIGHT files too (the multi-GB payload
    try:
        weight_bytes = _warm_model_weights(_active_model_cache_dirs())
        if weight_bytes > 0:
            warmed.append("model-weights")
    except Exception:
        log.debug("[PREWARM] model-weights warm pass failed, continuing", exc_info=True)
    elapsed = time.perf_counter() - t0
    # C-LOG-2: lifecycle-completion log line carries the canonical
    log.info(
        "[PREWARM] worker warm-imports complete: %d packages (%s)%s",
        len(warmed),
        ", ".join(warmed) if warmed else "none",
        format_duration(elapsed),
    )


def warm_imports_for_worker() -> None:
    """Public entry point the worker exe calls once at startup.

    Thin wrapper around :func:`_warm_imports` so the worker entry point
    (``voice_typer/worker/__main__.py``) does not depend on a
    underscore-prefixed name. Per master plan §6.2 P-1, this is the
    single call site for the prewarm-as-startup-phase logic.

    The function is idempotent and best-effort: any failure inside
    ``_warm_imports`` is logged at DEBUG and swallowed so the worker
    can still start (a cold cache only costs latency, never
    correctness).
    """
    try:
        _warm_imports()
    except Exception:
        log.debug("[PREWARM] warm_imports_for_worker failed, continuing with cold cache", exc_info=True)


@lru_cache(maxsize=1)
def _resolve_hf_cache_dir() -> Path:
    """Resolve the HF cache directory, robust to pre-session execution."""
    # Primary path: the canonical app config dir. This is what every
    primary_candidate: Path | None = None
    try:
        from voice_typer.server.config import _config_dir

        cache = _config_dir() / "huggingface"
        # Review fix C2: only accept absolute paths. A relative path
        if cache.is_absolute():
            if cache.exists():
                return cache
            primary_candidate = cache  # remember for the final fallback
    except Exception:
        log.debug("[PREWARM] _config_dir() lookup failed", exc_info=True)

    # Fallback 1: environment variables (LogonTrigger, normal session).
    if primary_candidate is None:
        home = os.environ.get("USERPROFILE") if is_windows() else os.environ.get("HOME")
        if home:
            cache = Path(home) / ".lausu" / "huggingface"
            if cache.is_absolute():
                try:
                    if cache.exists():
                        return cache
                except OSError:
                    # An inaccessible cache path (e.g. a dangling
                    log.debug(
                        "[PREWARM] HF cache path %s inaccessible, skipping fallback",
                        cache,
                    )
                primary_candidate = cache

    # Fallback 2: Windows registry (needed when BootTrigger fires before
    if primary_candidate is None and is_windows():
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Volatile Environment",
                0,
                winreg.KEY_READ,
            )
            try:
                profile = winreg.QueryValueEx(key, "USERPROFILE")[0]
            finally:
                winreg.CloseKey(key)
            if profile:
                return Path(profile) / ".lausu" / "huggingface"
        except OSError:
            pass
        except Exception:
            # Defensive: never let a registry read failure abort prewarm.
            log.debug("[PREWARM] Windows registry HF cache lookup failed", exc_info=True)

    # Fallback 3: POSIX getpwuid (needed when LaunchDaemon fires before
    if primary_candidate is None and (is_linux() or is_macos()):
        try:
            import pwd

            # The inline ``# type: ignore[attr-defined]`` matches the
            pw = pwd.getpwuid(  # type: ignore[attr-defined]
                os.getuid()  # type: ignore[attr-defined]
            )
            if pw.pw_dir:
                return Path(pw.pw_dir) / ".lausu" / "huggingface"
        except (KeyError, ImportError):
            pass
        except Exception:
            log.debug("[PREWARM] POSIX getpwuid HF cache lookup failed", exc_info=True)

    # Final best-effort: prefer the primary candidate (absolute path from
    if primary_candidate is not None:
        return primary_candidate
    # delegate to _paths.legacy_hf_cache_dir() so the literal
    from voice_typer.server import _paths

    return _paths.legacy_hf_cache_dir()


def _model_weight_files(active_dirs: list[Path]) -> list[Path]:
    """Return the model WEIGHT files (any snapshot) for the active model dirs."""
    files: list[Path] = []
    for d in active_dirs:
        snapshots_dir = d / "snapshots"
        if not snapshots_dir.is_dir():
            continue
        try:
            entries = list(snapshots_dir.iterdir())
        except OSError:
            continue
        for snapshot in entries:
            if not snapshot.is_dir():
                continue
            try:
                for f in snapshot.iterdir():
                    if f.is_file() and f.suffix in _MODEL_WEIGHTS_SUFFIXES:
                        files.append(f)
            except OSError:
                continue
    return files


def _warm_model_weights(active_dirs: list[Path]) -> int:
    """Page the active model's weight files into the OS standby cache.

    (2) of the Fast-Startup fixes: the library warm list above only
    covers runtime-pack libraries, the multi-GB weight files
    dominate post-reboot cold-start cost, so they are warmed here,
    once per worker start. Each file is first probed with
    :func:`_cache_ratio`: at or above ``_PREWARM_SKIP_HOT_RATIO`` the
    file is SKIPPED (fix (1), an already-hot multi-GB file is not
    re-read byte-for-byte; the latency probe costs a few dozen 4K
    reads). Best-effort: OSError per file is logged at DEBUG.

    Returns the number of bytes actually read (0 when everything was
    already hot or no weights exist).
    """
    t0 = time.perf_counter()
    total = 0
    skipped_hot = 0
    weight_files = _model_weight_files(active_dirs)
    for path in weight_files:
        try:
            if _cache_ratio(path) >= _PREWARM_SKIP_HOT_RATIO:
                skipped_hot += 1
                continue
            total += _warm_file(path)
        except OSError as exc:
            log.debug("[PREWARM] skip weight %s: %s", path, exc)
    elapsed = time.perf_counter() - t0
    log.info(
        "[PREWARM] model weights warm pass: %d file(s), %d already hot, %.1f MB read%s",
        len(weight_files),
        skipped_hot,
        total / (1024 * 1024),
        format_duration(elapsed),
    )
    return total


def _active_model_cache_dirs() -> list[Path]:
    """Return HF cache dirs for the active model + declared fallback."""
    dirs: list[Path] = []
    cfg = _cached_active_config()
    if cfg is None:
        return dirs
    try:
        cache_root = _resolve_hf_cache_dir() / "hub"
        if not cache_root.exists():
            return dirs

        active_backend = getattr(cfg, "asr_backend", "whisper")
        active_model_size = getattr(cfg, "model_size", "tiny")

        # Build the set of HF repo IDs whose cache dirs we want to warm.
        target_repo_ids: set[str] = set()

        if active_backend == "parakeet":
            try:
                from voice_typer.server.parakeet_engine import _PARAKERT_MODEL_ID

                target_repo_ids.add(_PARAKERT_MODEL_ID)
            except Exception:
                # Previously a bare ``except Exception: pass``. Log at
                log.debug(
                    "[PREWARM] Parakeet model ID lookup failed; skipping Parakeet in cache probe",
                    exc_info=True,
                )
        elif active_backend == "qwen":
            # Qwen auto-downloads on first use via qwen_engine.py; no fixed
            pass
        else:
            # Whisper backend (ctranslate2 + faster-whisper): warm the
            if active_model_size and active_model_size not in ("parakeet", "qwen"):
                try:
                    from voice_typer.server.model_registry import get_model_metadata as _get_meta

                    _meta = _get_meta(active_model_size)
                    target_repo_ids.add(
                        _meta.repo_id if _meta and _meta.repo_id else f"Systran/faster-whisper-{active_model_size}"
                    )
                except Exception:
                    target_repo_ids.add(f"Systran/faster-whisper-{active_model_size}")

        # Always include the declared Whisper fallback (tiny) so the
        if not (active_backend == "whisper" and active_model_size == _WHISPER_FALLBACK_MODEL_SIZE):
            target_repo_ids.add("Systran/faster-whisper-tiny")

        # Map repo IDs to cache dir paths and filter to existing ones.
        from voice_typer.server.model_availability import shared_hub_dir

        cache_roots = [shared_hub_dir(), cache_root]
        for repo_id in target_repo_ids:
            cache_dir_name = f"models--{repo_id.replace('/', '--')}"
            for root in cache_roots:
                cache_dir = root / cache_dir_name
                if cache_dir.is_dir() and cache_dir not in dirs:
                    dirs.append(cache_dir)
    except Exception as e:
        log.debug("[PREWARM] _active_model_cache_dirs failed: %s", e)
    return dirs


def _warm_file(path: Path) -> int:
    """Sequentially read *path* so its bytes enter the OS standby cache.

    Returns the number of bytes read.  Uses a small buffer so the process
    """
    read = 0
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            read += len(chunk)
            # CPython's reference counting frees the previous bytes
    rate = (read / (1024 * 1024)) / max(time.perf_counter() - t0, 1e-6)
    # per-file log demoted to DEBUG. Large packages contain tens of
    log.debug(
        "[PREWARM] warmed %s: %.0f MB in %.1fs (%.0f MB/s)",
        path.name,
        read / (1024 * 1024),
        time.perf_counter() - t0,
        rate,
    )
    return read


def _cache_ratio(path: Path, samples: int = _CACHE_RATIO_SAMPLES) -> float:
    """Estimate what fraction of ``path`` is in the OS standby cache.

    Returns 0.0 (cold) to 1.0 (fully cached).
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
