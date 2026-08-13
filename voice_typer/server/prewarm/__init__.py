# SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""Prewarm — worker startup phase (master plan §6.2 P-1).

Phase 2 / master plan §6.2 P-1: prewarm is NO LONGER a separate
Nuitka-frozen binary launched by OS-level schedulers (Windows
LogonTrigger / macOS LaunchAgent / Linux systemd). Instead, prewarm
is a **startup phase of the worker exe** (``voice_typer/worker/__main__.py``).

The worker calls :func:`warm_imports_for_worker` once, in its own
process, BEFORE accepting the first transcription request. This
eliminates:

- ``prewarm-<triple>[.exe]`` binary (3 build scripts deleted — owned by
  Sub-agent 5 in their build-script slice).
- ``src-tauri/src/sidecar/spawn/prewarm.rs`` (owned by Sub-agent 10).
- ``voice_typer/server/prewarm_resolver.py`` (deleted in this slice).
- ``voice_typer/server/task_scheduler.py`` (deleted in this slice).
- ``voice_typer/server/prewarm_scheduler_posix.py`` (deleted in this slice).
- The PID-file + sentinel + completion-event machinery that used to live
  in ``prewarm/{paths,process_tracker,completion_events}.py``
  (deleted in this slice).
- The CLI entry point + logging setup + pipeline orchestration that
  used to live in ``prewarm/{cli,logging_setup,pipeline,__main__}.py``
  (deleted in this slice — replaced by the worker entry point).
- 24 OS-scheduler tests (``tests/tauri/mig{15,16,17}/test_prewarm_*``
  + ``tests/test_prewarm_spawn_resolver.py`` +
  ``tests/test_uninstall_prewarm_cleanup.py`` — deleted in this slice).

What REMAINS in this package is the **cache-probe logic** that pages the
runtime-pack libraries' files into the OS standby cache without importing
them (``_warm_package_files``, ``_warm_imports``, and the new public
entry point ``warm_imports_for_worker``). Everything else moved or was
deleted.

Patch-path compatibility
------------------------
``_warm_package_files`` looks up ``_warm_file`` via the package
namespace (``_pkg._warm_file()``) at call time so test patches of the
form ``monkeypatch.setattr(prewarm, "_warm_file", ...)`` keep working
(both functions live in :mod:`.cache_probe`, but the indirection is
preserved so the existing test fixtures don't break).

Stdlib modules that tests patch via ``monkeypatch.setattr(prewarm.X,
"attr", ...)`` — e.g. ``prewarm.os``, ``prewarm.importlib.util`` — are
bound on this package via plain ``import`` statements. Production code
in :mod:`.cache_probe` does ``import os`` / ``import importlib.util``
directly (same module objects), so the patches propagate.

``inspect.getsource`` compatibility
-----------------------------------
Every remaining function is genuinely defined in :mod:`.cache_probe`,
so ``inspect.getsource(prewarm._warm_file)`` etc. keep working — they
read from the submodule file. ``inspect.getsource(prewarm)``
(module-level) reads this ``__init__.py``'s source.
"""

from __future__ import annotations

# ─── Top-of-module imports ──────────────────────────────────────────────
# Stdlib modules bound on the package so tests that do
# ``monkeypatch.setattr(prewarm.os, "nice", ...)`` /
# ``monkeypatch.setattr(prewarm.importlib.util, "find_spec", ...)``
# propagate to production code in :mod:`.cache_probe` (which does
# ``import os`` / ``import importlib.util`` directly — same module
# objects, so the patches take effect on the real module's attributes).
import importlib  # noqa: F401
import importlib.util  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import sys  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401

# Platform helpers — bound on the package so tests that do
# ``monkeypatch.setattr(prewarm, "is_windows", ...)`` propagate to
# production code that looks up ``is_windows`` via ``_pkg.is_windows()``.
from voice_typer.server.platform_utils import (  # noqa: F401
    is_linux,
    is_macos,
    is_windows,
)

log = logging.getLogger("voice_typer.server.prewarm")

# ─── Public API re-exports ──────────────────────────────────────────────
# Each name below is genuinely defined in :mod:`.cache_probe`. We import
# it here so ``from voice_typer.server.prewarm import X`` keeps working.
from .cache_probe import (  # noqa: E402
    _CACHE_RATIO_HIT_THRESHOLD_US,
    _CACHE_RATIO_PAGE_BYTES,
    _CACHE_RATIO_SAMPLES,
    _READ_CHUNK_BYTES,
    _WHISPER_FALLBACK_MODEL_SIZE,
    _WORKER_WARM_PACKAGES,
    _active_model_cache_dirs,
    _cache_ratio,
    _find_parakeet_weights,
    _resolve_hf_cache_dir,
    _warm_file,
    _warm_imports,
    _warm_package_files,
    warm_imports_for_worker,
)

__all__ = [
    # cache_probe
    "_resolve_hf_cache_dir",
    "_find_parakeet_weights",
    "_active_model_cache_dirs",
    "_cache_ratio",
    "_warm_file",
    "_warm_package_files",
    "_warm_imports",
    "warm_imports_for_worker",
    "_WORKER_WARM_PACKAGES",
    "_READ_CHUNK_BYTES",
    "_CACHE_RATIO_SAMPLES",
    "_CACHE_RATIO_PAGE_BYTES",
    "_CACHE_RATIO_HIT_THRESHOLD_US",
    "_WHISPER_FALLBACK_MODEL_SIZE",
    # stdlib modules bound on the package (for
    # `monkeypatch.setattr(prewarm.X, "attr", ...)`)
    "log",
    "os",
    "sys",
    "time",
    "json",
    "importlib",
    "Path",
    # platform helpers (for `monkeypatch.setattr(prewarm, "is_windows", ...)`)
    "is_windows",
    "is_linux",
    "is_macos",
]
