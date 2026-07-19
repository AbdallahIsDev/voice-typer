# ARCH-045 / SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""HF cache probing + file-warming primitives.

Phase 4.5 / ARCH-045 — this module holds the helpers that locate the
HuggingFace cache, probe which model files are resident, and read bytes
into the OS standby cache without importing the packages they belong to:

- :func:`_resolve_hf_cache_dir` — robust cache-dir resolution (works at
  BootTrigger time before the user session is fully initialized).
- :func:`_find_parakeet_weights` — locate the cached Parakeet
  ``model.safetensors``.
- :func:`_active_model_cache_dirs` — return only the HF cache dirs the
  active backend would actually use (active model + Whisper fallback).
- :func:`_cache_ratio` — sample-based probe estimating the fraction of
  a file that's in the OS standby cache.
- :func:`_warm_file` — sequentially read a file into the standby cache.
- :func:`_warm_package_files` — read a package's installed files into
  the standby cache WITHOUT importing it.
- :func:`_warm_imports` — page torch + transformers files into the OS
  cache (no import).

Patch-path compatibility
------------------------
Tests patch ``_warm_file`` on the package namespace and then call
``prewarm._warm_package_files(...)`` directly, so :func:`_warm_package_files`
must look up ``_warm_file`` via ``_pkg._warm_file()`` at call time.  The
other helpers aren't patched via the package namespace by any test that
exercises them, so bare-name lookups are sufficient.

``inspect.getsource`` compatibility
-----------------------------------
Every function here is genuinely defined in this file, so
``inspect.getsource(prewarm._warm_file)`` etc. keep working.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import random
import sys
import time
from pathlib import Path

# Patch-path bridge: route lookups of cross-submodule helpers through
# the package namespace so test patches of the form
# ``monkeypatch.setattr(prewarm, "_warm_file", ...)`` keep affecting
# production code defined here.
from voice_typer.server import prewarm as _pkg
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger("voice_typer.server.prewarm")

# Read weights in this-sized chunk.  Small enough to keep the process's
# own working set tiny, large enough to amortise per-read overhead.
_READ_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MB

# ADR-0009 Issue 3: parameters for the _cache_ratio() probe. Reads this
# many random 4K pages from the model file and counts how many return in
# under the latency threshold (cache hit). 20 samples gives a 5% resolution
# which is plenty for the Hot/Partial/Cold UI label.
_CACHE_RATIO_SAMPLES = 20
_CACHE_RATIO_PAGE_BYTES = 4096
# 50µs threshold: SSD cold read ~100-500µs, RAM cache hit <10µs. Pages
# that return in under 50µs are considered cached.
_CACHE_RATIO_HIT_THRESHOLD_US = 50.0

# STARTUP-4: Whisper model sizes that are valid fallback targets.
# AsrBackendRegistry.load_with_fallback() falls back to whisper/tiny.en
# when the active backend fails to load, so we always warm tiny.en as
# the declared fallback (in addition to the active backend's model).
_WHISPER_FALLBACK_MODEL_SIZE = "tiny.en"


def _warm_package_files(pkg_name: str) -> int:
    """Read a package's installed files into the OS page cache WITHOUT
    importing it.

    Replaces the old ``import torch`` / ``import transformers`` warmup.
    ``import`` executes the package's code (~5 s of CPU for torch) and builds
    live objects we immediately throw away when prewarm exits — the only
    thing we actually want is the file *bytes* resident in the OS standby
    cache, so a later ``import torch`` in the real app reads them from RAM.
    Reading the files directly produces the same cache state but skips the
    CPU cost, so prewarm finishes in seconds instead of ~a minute and uses
    far less memory.  The app still has to execute torch's code once, in its
    own process — that is unavoidable and unchanged.

    Locating the files uses ``importlib.util.find_spec`` (the import *finder*
    phase), which does NOT execute the package's code — verified by asserting
    the package never lands in ``sys.modules``.
    """
    spec = importlib.util.find_spec(pkg_name)
    if spec is None:
        log.debug("[PREWARM] %s not installed — skip file warmup", pkg_name)
        return 0
    if pkg_name in sys.modules:
        # find_spec must never import, but if it ever does we must not claim
        # credit for warming something that was already loaded.
        log.debug("[PREWARM] %s already imported — skip", pkg_name)
        return 0

    roots: list[Path] = []
    if spec.submodule_search_locations:
        roots.extend(Path(p) for p in spec.submodule_search_locations)
    elif spec.origin and spec.origin != "namespace":
        roots.append(Path(spec.origin))

    if not roots:
        log.debug("[PREWARM] %s has no locatable files — skip", pkg_name)
        return 0

    total = 0
    t0 = time.perf_counter()
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                try:
                    total += _pkg._warm_file(path)
                except OSError as exc:
                    log.debug("[PREWARM] skip %s: %s", path, exc)
    elapsed = time.perf_counter() - t0
    # Defensive: file warmup must never have imported the package.
    assert pkg_name not in sys.modules, f"{pkg_name} was imported during file warmup — must stay unimported"
    log.info(
        "[PREWARM] file-warmed %s: %.0f MB in %.1fs",
        pkg_name,
        total / (1024 * 1024),
        elapsed,
    )
    return total


def _warm_imports() -> None:
    """Page torch + transformers files into the OS cache (no import).

    STARTUP-3: filter by active backend. Previously this unconditionally
    imported torch + transformers (which takes ~30-60 s cold, ~400 s when
    contended, and executes code we then throw away). Whisper users don't
    need transformers — they only need faster_whisper (ctranslate2, ~3 s
    cold). Parakeet/Qwen users still need the full torch + transformers
    stack, but we warm its *files* rather than importing it, so prewarm
    finishes in seconds and the app executes torch exactly once, later.
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
        # Parakeet / Qwen both use the HuggingFace transformers stack, so we
        # need torch + transformers resident in the OS cache.  Read their
        # installed files WITHOUT importing (see _warm_package_files): this
        # pages in the same ~4.5 GB of .pyc/.dll/.pyd bytes the old
        # ``import torch`` did, but skips executing torch (~5 s CPU) and the
        # live modules we'd discard on exit.  The app still executes torch
        # once, in its own process — unavoidable.
        _warm_package_files("torch")
        _warm_package_files("transformers")
    else:
        # STARTUP-3: whisper backend — skip torch/transformers (~400 s saved).
        # Whisper uses faster_whisper (ctranslate2) which has no torch
        # dependency. We still import faster_whisper below to warm the
        # CPU-fallback path; the whisper fallback (tiny.en) is what
        # AsrBackendRegistry.load_with_fallback() falls back to.
        log.info(
            "[PREWARM] active backend=%s — skipping torch/transformers import (whisper only needs faster_whisper)",
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
                0,
                winreg.KEY_READ,
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

            # PYREFLY-TASK-16: Windows false positive. This whole block
            # is guarded by ``is_linux() or is_macos()`` above, so on
            # Windows the body never executes (the guard short-circuits
            # to False before ``import pwd`` even runs). However pyrefly
            # on Windows does not propagate the guard's narrowing, so it
            # flags ``pwd.getpwuid`` and ``os.getuid`` as
            # ``missing-attribute`` (both are POSIX-only stdlib surface).
            # The inline ``# type: ignore[attr-defined]`` matches the
            # pattern used for the symmetric Windows-only ``os.startfile``
            # call at app.py:1141. ``except (KeyError, ImportError)`` is
            # the runtime safety net (ImportError fires on any platform
            # where the ``pwd`` module is somehow unavailable).
            pw = pwd.getpwuid(  # type: ignore[attr-defined]
                os.getuid()  # type: ignore[attr-defined]
            )
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
    # RW-7: delegate to _paths.legacy_hf_cache_dir() so the literal
    # Path.home() / ".voice-typer" lives in one canonical place (and
    # the RW-7 regression test can allow it there rather than here).
    from voice_typer.server import _paths

    return _paths.legacy_hf_cache_dir()


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
        path.name,
        read / (1024 * 1024),
        time.perf_counter() - t0,
        rate,
    )
    return read


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
