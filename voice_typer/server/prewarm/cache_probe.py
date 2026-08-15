# SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""HF cache probing + file-warming primitives.

Phase 4.5 /  — this module holds the helpers that locate the
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
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

# Patch-path bridge: route lookups of cross-submodule helpers through
# the package namespace so test patches of the form
# ``monkeypatch.setattr(prewarm, "_warm_file", ...)`` keep affecting
# production code defined here.
from voice_typer.server import prewarm as _pkg
from voice_typer.server.duration import format_duration
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger("voice_typer.server.prewarm")

# Read weights in this-sized chunk.  Small enough to keep the process's
# own working set tiny, large enough to amortise per-read overhead.
_READ_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MB

# Only warm file types whose bytes actually contribute to import-time
# disk I/O.  ``.py`` is excluded on purpose: when a ``.pyc`` is present
# CPython never reads the ``.py`` at import time, so warming the source
# file wastes disk bandwidth and standby-cache space.
# added ``.dylib`` (macOS dynamic libraries — equivalent to
# ``.so`` on Linux and ``.dll`` on Windows; without it, a macOS
# prewarm run would skip every native extension in a package like
# ``torch`` / ``numpy`` / ``cv2``). ``.json`` / ``.txt`` are retained
# because some packages (``tokenizers``, ``transformers``) read
# tokenizer configs / vocab files at import time.
_WARM_PACKAGE_SUFFIXES: frozenset[str] = frozenset({".pyc", ".so", ".pyd", ".dll", ".dylib", ".json", ".txt"})

# directory names whose contents NEVER contribute to import-time
# disk I/O. Skipping these avoids paging in:
#   - ``tests`` / ``test``: bundled test suites (pytest discovers them
#     via ``__init__`` + ``conftest`` walks, not via package import).
#   - ``docs``: rendered documentation (Sphinx HTML, Markdown sources).
#   - ``__pycache__``: stale bytecode from a prior Python version that
#     the current interpreter will never load (it'll regenerate its
#     own ``.pyc`` with the matching magic number).
#   - ``*.dist-info`` / ``*.egg-info``: package metadata directories
#     (METADATA, RECORD, entry_points.txt) read by ``importlib.metadata``
#     on demand, not at import time.
# The skip happens during the ``rglob`` walk — when a directory's name
# matches this set, we ``rglob``'s recursive descent is pruned by
# checking the parent path of each file. This is cheaper than calling
# ``rglob`` and filtering after the fact (which would still stat every
# file under ``tests/`` / ``docs/``).
_WARM_PACKAGE_SKIP_DIRS: frozenset[str] = frozenset({"tests", "test", "docs", "__pycache__"})

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


def _iter_warmable_files(root: Path) -> Iterator[Path]:
    """Iterate warmable files under ``root`` without per-file ``stat()``.

    replaces the old ``root.rglob('*')`` + ``path.is_file()``
    pattern. ``rglob`` followed by ``is_file()`` issues a fresh
    ``stat()`` syscall per entry (~40 k stats for torch alone) even
    though ``os.scandir`` already returned the d_type for each entry.

    This implementation uses an explicit ``os.scandir`` stack-walk
    (iterative, not recursive — so deep trees don't hit the recursion
    limit) and filters by ``entry.is_file()`` (uses the cached d_type
    on filesystems that populate it: ext4/tmpfs on Linux, APFS on
    macOS, NTFS on Windows). Filesystems with DT_UNKNOWN (e.g. some
    FUSE mounts) fall back to a stat() — but that's the same
    worst-case as the old code, so no regression.

    Only yields files whose suffix is in ``_WARM_PACKAGE_SUFFIXES`` —
    callers don't have to filter. The walk follows symlinks as
    ``scandir`` does by default; the symlink-loop test
    (``test_walk_handles_symlinks_without_infinite_loop``) confirms
    the iterative stack-walk terminates on a self-referential
    symlink because each directory is pushed at most once per
    stack level.

    Yields ``pathlib.Path`` objects (one ``stat`` per yielded Path
    if the caller accesses Path metadata; the call to
    ``scandir`` + the suffix check themselves are stat-free).
    """
    seen: set[Path] = set()
    stack: list[Path] = [root]
    suffixes = _WARM_PACKAGE_SUFFIXES
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    # Follow directories (iterative, not recursive —
                    # bounded by stack depth, not Python's recursion
                    # limit). Track seen dirs to terminate on
                    # symlink loops.
                    try:
                        is_dir = entry.is_dir(follow_symlinks=True)
                    except OSError:
                        # broken symlink / permission denied on the
                        # link itself; skip.
                        continue
                    if is_dir:
                        try:
                            real = Path(entry.path).resolve()
                        except OSError:
                            continue
                        if real in seen:
                            continue
                        seen.add(real)
                        stack.append(Path(entry.path))
                        continue
                    # File: filter by suffix (the only test the
                    # production code makes; per-file stat happens
                    # later in the warm path which doesn't care about
                    # d_type).
                    if not entry.name.endswith(tuple(suffixes)):
                        continue
                    yield Path(entry.path)
        except (FileNotFoundError, PermissionError, NotADirectoryError):
            # Root may have been deleted between list + scandir; or we
            # lack permission. Skip silently — prewarm is best-effort.
            continue


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
        # iterate rglob directly — sorted() would force a full
        # directory walk into memory before the first read, doubling
        # peak RSS for large packages (torch has ~40k files).
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in _WARM_PACKAGE_SUFFIXES:
                continue
            # skip files whose path crosses a skipped directory
            # (``tests/``, ``docs/``, ``__pycache__``, ``*.dist-info``,
            # ``*.egg-info``). ``rglob`` does not prune directories, so
            # we filter on the relative path parts here. The cost is
            # one ``Path.parts`` tuple allocation per file — cheaper
            # than the ``open`` + ``read`` we'd otherwise do for files
            # that never contribute to import-time I/O.
            rel = path.relative_to(root)
            if any(
                part in _WARM_PACKAGE_SKIP_DIRS or part.endswith(".dist-info") or part.endswith(".egg-info")
                for part in rel.parts[:-1]
            ):
                continue
            try:
                total += _pkg._warm_file(path)
            except OSError as exc:
                log.debug("[PREWARM] skip %s: %s", path, exc)
    elapsed = time.perf_counter() - t0
    # Defensive: file warmup must never have imported the package.
    assert pkg_name not in sys.modules, f"{pkg_name} was imported during file warmup — must stay unimported"
    # C-LOG-2: lifecycle-completion log line carries the canonical
    # ``_<duration>`` suffix from ``format_duration()`` (not an ad-hoc
    # ``%.1fs``) so the perf marker is greppable project-wide.
    log.info(
        "[PREWARM] file-warmed %s: %.0f MB%s",
        pkg_name,
        total / (1024 * 1024),
        format_duration(elapsed),
    )
    return total


@lru_cache(maxsize=1)
def _cached_active_config():
    """Return the active ``Config`` instance, cached per prewarm run ().

    ``_warm_imports`` and ``_active_model_cache_dirs`` previously each
    called ``Config.load()`` independently, doubling the prewarm
    cold-start I/O. The config does not change during a prewarm
    process's lifetime, so caching is safe. Returns ``None`` on load
    failure so callers fall back to defaults without raising.

    Note: this is a legitimate fresh-snapshot read — the prewarm probe
    runs in a DETACHED subprocess (spawned by ``prewarm_scheduler``
    before the main app bootstraps), so there is no ``app.config`` to
    reference. A fresh disk read is the only option. Read-only — no
    mutation, no config-mutation lock required.
    """
    try:
        from voice_typer.server.config import Config

        return Config.load()
    except Exception:
        log.debug("[PREWARM] Config.load() failed — using default backend", exc_info=True)
        return None


# Phase 2 / Plan §6.2 P-1: the warm list after the torch removal
# + runtime-pack split. The OS-level schedulers (Windows LogonTrigger,
# macOS LaunchAgent, Linux systemd) are GONE; prewarm is now a startup
# phase of the worker exe (``voice_typer/worker/__main__.py``). The
# worker calls :func:`warm_imports_for_worker` once before accepting
# the first transcription request.
#
# The package list is FIXED — it no longer varies by active backend.
# Per the master plan §6.2 P-1: ``onnxruntime + ctranslate2 +
# numpy/scipy`` (≈200 MB total, far fewer files than the old
# torch+transformers stack — see plan §3.4). torch + transformers are
# DROPPED because:
#   - VAD is now Silero VAD ONNX (no torch) — see PLAN_ONNX_INTEGRATION §2.
#   - Parakeet is now ``onnx-asr`` (no transformers) — see
#     PLAN_ONNX_INTEGRATION §3.
#   - Qwen migration (Phase 1d) is deferred; if/when Qwen ships torch,
#     it warms in its own process (the worker exe is the runtime pack
#     and never contains torch).
#
# ``faster_whisper`` is kept in the list because it is still the
# Whisper backend (``ctranslate2`` is its underlying runtime, but
# ``faster_whisper``'s own ``.py`` / ``.pyc`` files are paged in here
# too — they are tiny relative to ``ctranslate2`` but skipping them
# would regress the Whisper cold-start path).
_WORKER_WARM_PACKAGES: tuple[str, ...] = (
    "onnxruntime",
    "ctranslate2",
    "numpy",
    "scipy",
    "faster_whisper",
)


def _warm_imports() -> None:
    """Page the runtime-pack libraries' files into the OS cache (no import).

    Per master plan §6.2 P-1 (worker-startup prewarm phase), the warm
    list is ``onnxruntime + ctranslate2 + numpy/scipy`` (plus
    ``faster_whisper`` for the Whisper backend's own Python files).
    ``torch`` and ``transformers`` are DROPPED — VAD is now ONNX, Parakeet
    is now ``onnx-asr``, and neither ships in the worker exe.

    This function pages the libraries' installed files into the OS
    standby cache **without importing them** (see
    :func:`_warm_package_files`). The worker still has to execute each
    library's code once, in its own process — that is unavoidable and
    unchanged — but the cold-disk read is paid once here, in the
    background, before the user clicks "transcribe".

    BACKEND-INDEPENDENT: the list is fixed. The pre-Phase-2 code varied
    the list by ``asr_backend`` (whisper vs parakeet/qwen) because
    parakeet/qwen pulled in the torch + transformers stack; with torch
    gone that variation is gone too.
    """
    t0 = time.perf_counter()
    warmed: list[str] = []
    for pkg in _WORKER_WARM_PACKAGES:
        try:
            bytes_read = _warm_package_files(pkg)
        except Exception as exc:
            # Best-effort: a missing package (e.g. ``scipy`` not
            # installed in a minimal dev env) is logged at DEBUG and
            # skipped. The worker still starts; only the cold-start
            # benefit is lost.
            log.debug("[PREWARM] %s not warmable (skipping): %s", pkg, exc)
            continue
        if bytes_read > 0:
            warmed.append(pkg)
    elapsed = time.perf_counter() - t0
    # C-LOG-2: lifecycle-completion log line carries the canonical
    # ``_<duration>`` suffix from ``format_duration()`` (not an ad-hoc
    # ``%.2fs``) so the perf marker is greppable project-wide.
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
        log.debug("[PREWARM] warm_imports_for_worker failed — continuing with cold cache", exc_info=True)


@lru_cache(maxsize=1)
def _resolve_hf_cache_dir() -> Path:
    """Resolve the HF cache directory, robust to pre-session execution.

    wrapped with ``@lru_cache(maxsize=1)`` so a single prewarm
        run resolves the directory at most once. Previously the function was
        called from both ``_find_parakeet_weights`` and
        ``_active_model_cache_dirs`` (and indirectly via ``_config_root()``
        / ``_sentinel_path()`` / ``_pid_file_path()``), each call re-running
        the env-var / registry / getpwuid fallback chain and re-stat'ing
        the filesystem. The directory does not change during a prewarm
        process's lifetime, so caching is safe. Tests clear the cache via
        ``cache_clear()`` in the autouse fixture.

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
            if cache.is_absolute():
                try:
                    if cache.exists():
                        return cache
                except OSError:
                    # An inaccessible cache path (e.g. a dangling
                    # junction / broken symlink with restrictive ACLs)
                    # must NOT abort prewarm resolution — on Windows
                    # Path.exists() can raise PermissionError for such
                    # paths instead of returning False. Fall through to
                    # the remaining fallbacks and remember the absolute
                    # candidate for the final best-effort return.
                    log.debug(
                        "[PREWARM] HF cache path %s inaccessible — skipping fallback",
                        cache,
                    )
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

            # PYREFLY-: Windows false positive. This whole block
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
    # delegate to _paths.legacy_hf_cache_dir() so the literal
    # Path.home() / ".voice-typer" lives in one canonical place (and
    # the  regression test can allow it there rather than here).
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

    ``Config.load()`` is shared with ``_warm_imports`` via
        ``_cached_active_config()`` so a single prewarm run parses the
        config file at most once.
    """
    dirs: list[Path] = []
    cfg = _cached_active_config()
    if cfg is None:
        return dirs
    try:
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
                # Previously a bare ``except Exception: pass``. Log at
                # DEBUG — the import failure is non-fatal (the prewarm
                # cache probe just won't include the Parakeet repo ID
                # in its target set, so the probe may report "no models
                # cached" even when Parakeet is cached). DEBUG is
                # appropriate because the parakeet_engine module is
                # always installed in production; this branch only
                # fires in minimal test environments that haven't
                # imported the engine yet.
                log.debug(
                    "[PREWARM] Parakeet model ID lookup failed; skipping Parakeet in cache probe",
                    exc_info=True,
                )
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
            # CPython's reference counting frees the previous bytes
            # object on the next loop iteration's read assignment, so
            # no explicit cleanup is needed here.
    rate = (read / (1024 * 1024)) / max(time.perf_counter() - t0, 1e-6)
    # per-file log demoted to DEBUG. Large packages (torch,
    # transformers) contain tens of thousands of files; an INFO line
    # per file floods ``prewarm.log`` and drowns out the per-package
    # summary. Operators who need per-file detail can enable DEBUG
    # via ``--debug``.
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
