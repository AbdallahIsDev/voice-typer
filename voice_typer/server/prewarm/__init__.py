# ARCH-045 / SPLIT-4: extracted from the original ``prewarm.py`` god-module.
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
 2.  **Import warmup.**  Read the installed ``torch`` + ``transformers``
     package files (``.pyc`` / ``.dll`` / ``.pyd`` / ``.py``) into the OS
     page cache **without importing them**.  This pages in ~4.5 GB of bytes
     — the same set the old ``import torch`` did — but skips the ~5 s of CPU
     cost of *executing* torch (the live modules would be thrown away on
     exit anyway).  The app's later ``import torch`` reads those bytes from
     RAM and only pays the CPU-execution cost once, in its own process.
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

Phase 4.5 / ARCH-045 — this file was previously a 2,162-line god-module
(``voice_typer/server/prewarm.py``); it has been split into a package
with one module per concern:

- :func:`_setup_logging` / :func:`_fast_startup_enabled` /
  :func:`_free_ram_mb` / :func:`_lower_io_priority` (logging + guards) —
  :mod:`.logging_setup`
- :func:`_resolve_hf_cache_dir` / :func:`_find_parakeet_weights` /
  :func:`_active_model_cache_dirs` / :func:`_cache_ratio` /
  :func:`_warm_file` / :func:`_warm_package_files` /
  :func:`_warm_imports` (HF cache probing + file warming) —
  :mod:`.cache_probe`
- :func:`_config_root` / :func:`_sentinel_path` / :func:`_pid_file_path`
  / :func:`_boot_time` / :func:`_already_warmed` / :func:`_mark_warmed`
  / :func:`active_dirs_exist` (paths + boot-session dedup) —
  :mod:`.paths`
- :func:`_write_pid_file` / :func:`_remove_pid_file` /
  :func:`_process_alive` / :func:`_read_process_cmdline_windows` /
  :func:`_read_process_cmdline_windows_wmi` / :func:`_process_is_prewarm`
  / :func:`is_prewarm_running` / :func:`wait_for_prewarm` /
  :func:`spawn_background_prewarm` / :func:`get_prewarm_status` /
  :func:`_read_prewarm_pid` (PID file + process liveness + status) —
  :mod:`.process_tracker`
- :func:`_completion_event_name` / :func:`_create_completion_event` /
  :func:`_signal_completion_event` / :func:`_close_completion_event` /
  :func:`_wait_for_completion_event` / :func:`_wait_completion_windows`
  / :func:`_wait_completion_linux` / :func:`_wait_completion_macos`
  (CPU-04 event-based completion) —
  :mod:`.completion_events`
- :func:`run` / :func:`_run_warming_pipeline` (pipeline orchestration) —
  :mod:`.pipeline`
- :func:`_parse_args` / :func:`_print_status` / :func:`main` (CLI) —
  :mod:`.cli`

This ``__init__.py`` re-exports every public name that the original
module exposed so existing imports of the form
``from voice_typer.server.prewarm import X`` keep working without
modification.

CR-67 / TECH-DEBT: ``_pkg.X`` indirection for test-patch compatibility
-------------------------------------------------------------------------
This package uses an **indirection pattern** (rather than a custom
module subclass like :mod:`voice_typer.server.recording` does) to
make test patches on the package namespace propagate to submodules:

- Each submodule does ``from voice_typer.server import prewarm as _pkg``
  at the top of its file.
- Functions inside the submodules look up patched names via
  ``_pkg.X()`` at call time (rather than capturing the function
  object at import time).
- This ``__init__.py`` re-exports ``X`` from the appropriate
  submodule so ``_pkg.X`` resolves correctly without eager binding
  at import time.

WHY this hack exists: the test suite uses
``monkeypatch.setattr(prewarm, "X", ...)`` and
``monkeypatch.setattr("voice_typer.server.prewarm.X", ...)`` to
inject fakes / failure modes, and then expects the production code
in the submodules to see the new value.  Without the ``_pkg.X``
indirection, the submodule's binding (captured at import time)
would be unchanged — the test would silently no-op.

The same pattern exists in :mod:`voice_typer.server.recording` (which
additionally installs a custom ``_RecordingModule`` class for the
mutable-state routing case) and :mod:`voice_typer.server.server_platform`.
All three packages together account for ~500 LOC of ``__init__.py``
boilerplate that exists purely for test-patch compatibility.

TODO (2026-07-25, CR-67 / TECH-DEBT — OPEN, awaiting migration):
This ``__init__.py`` boilerplate exists for test-patch compatibility
during the package reorganization.  Once CR-67 is complete, this
file will be simplified.  Migrate tests to patch submodules directly,
then remove the ``_pkg.X`` indirection.  Concretely: replace
``monkeypatch.setattr("voice_typer.server.prewarm.X", ...)`` with
``monkeypatch.setattr("voice_typer.server.prewarm.<submodule>.X", ...)``
and have the submodules do ``from .<submodule> import X`` at the top
of their file (so the binding is captured at import time and the
patch takes effect via the submodule's ``__dict__``).  Once every
test site has been migrated, the ``_pkg.X`` references and the
``from voice_typer.server import prewarm as _pkg`` lines in the
submodules can be deleted.  Estimated scope: 30-50 test files per
package (so 90-150 test files total across the three packages).
Tracked as CR-67 / TECH-DEBT.

Patch-path compatibility
------------------------
Tests heavily patch names on this package namespace via
``monkeypatch.setattr(prewarm, "X", ...)`` (and the string-based form
``monkeypatch.setattr("voice_typer.server.prewarm.X", ...)``).  For a
patch on ``X`` to affect production code that calls ``X``, the call
must route through the package binding at call time — hence
``from voice_typer.server import prewarm as _pkg`` and the ``_pkg.X()``
references inside each submodule's functions.  This ``__init__.py``
re-exports ``X`` from the appropriate submodule so ``_pkg.X`` resolves
correctly without eager binding at import time.

Stdlib modules that tests patch via ``monkeypatch.setattr(prewarm.X,
"attr", ...)`` — e.g. ``prewarm.os.nice``, ``prewarm.importlib.util``
— are bound on this package via plain ``import`` statements.  Production
code in the submodules does ``import os`` / ``import importlib.util``
directly (same module objects), so the patches propagate.

``inspect.getsource`` compatibility
-----------------------------------
Every function is genuinely defined in its submodule (not aliased here),
so ``inspect.getsource(prewarm._lower_io_priority)`` etc. keep working
— they read from the submodule file.  ``inspect.getsource(prewarm)``
(module-level) reads this ``__init__.py``'s source.
"""

from __future__ import annotations

# ─── Top-of-module imports ──────────────────────────────────────────────
# Stdlib modules bound on the package so tests that do
# ``monkeypatch.setattr(prewarm.os, "nice", ...)`` /
# ``monkeypatch.setattr(prewarm.importlib.util, "find_spec", ...)``
# propagate to production code in the submodules (which does
# ``import os`` / ``import importlib.util`` directly — same module
# objects, so the patches take effect on the real module's attributes).
import argparse  # noqa: F401
import importlib  # noqa: F401
import importlib.util  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import random  # noqa: F401
import select  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401

# Platform helpers — bound on the package so tests that do
# ``monkeypatch.setattr(prewarm, "is_windows", ...)`` propagate to
# production code that looks up ``is_windows`` via ``_pkg.is_windows()``
# (e.g. ``spawn_background_prewarm`` in :mod:`.process_tracker`).
from voice_typer.server.platform_utils import (  # noqa: F401
    is_linux,
    is_macos,
    is_windows,
)

log = logging.getLogger("voice_typer.server.prewarm")

# ─── Public API re-exports ──────────────────────────────────────────────
# Each name below is genuinely defined in a sibling submodule.  We import
# it here so ``from voice_typer.server.prewarm import X`` keeps working.
from .cache_probe import (  # noqa: E402
    _CACHE_RATIO_HIT_THRESHOLD_US,
    _CACHE_RATIO_PAGE_BYTES,
    _CACHE_RATIO_SAMPLES,
    _READ_CHUNK_BYTES,
    _WHISPER_FALLBACK_MODEL_SIZE,
    _active_model_cache_dirs,
    _cache_ratio,
    _find_parakeet_weights,
    _resolve_hf_cache_dir,
    _warm_file,
    _warm_imports,
    _warm_package_files,
)
from .cli import (  # noqa: E402
    _parse_args,
    _print_status,
    main,
)
from .completion_events import (  # noqa: E402
    _close_completion_event,
    _completion_event_name,
    _create_completion_event,
    _signal_completion_event,
    _wait_completion_linux,
    _wait_completion_macos,
    _wait_completion_windows,
    _wait_for_completion_event,
)
from .logging_setup import (  # noqa: E402
    _fast_startup_enabled,
    _free_ram_mb,
    _lower_io_priority,
    _setup_logging,
)
from .paths import (  # noqa: E402
    _already_warmed,
    _boot_time,
    _config_root,
    _mark_warmed,
    _pid_file_path,
    _sentinel_path,
    active_dirs_exist,
)
from .pipeline import (  # noqa: E402
    DEFAULT_MIN_FREE_RAM_MB,
    EXIT_DISABLED,
    EXIT_IMPORT_FAILED,
    EXIT_LOW_RAM,
    EXIT_NO_MODEL,
    EXIT_OK,
    _run_warming_pipeline,
    run,
)
from .process_tracker import (  # noqa: E402
    _process_alive,
    _process_is_prewarm,
    _read_prewarm_pid,
    _read_process_cmdline_windows,
    _read_process_cmdline_windows_wmi,
    _remove_pid_file,
    _write_pid_file,
    get_prewarm_status,
    is_prewarm_running,
    spawn_background_prewarm,
    wait_for_prewarm,
)

__all__ = [
    # logging_setup
    "_setup_logging",
    "_fast_startup_enabled",
    "_free_ram_mb",
    "_lower_io_priority",
    # cache_probe
    "_resolve_hf_cache_dir",
    "_find_parakeet_weights",
    "_active_model_cache_dirs",
    "_cache_ratio",
    "_warm_file",
    "_warm_package_files",
    "_warm_imports",
    "_READ_CHUNK_BYTES",
    "_CACHE_RATIO_SAMPLES",
    "_CACHE_RATIO_PAGE_BYTES",
    "_CACHE_RATIO_HIT_THRESHOLD_US",
    "_WHISPER_FALLBACK_MODEL_SIZE",
    # paths
    "_config_root",
    "_sentinel_path",
    "_pid_file_path",
    "_boot_time",
    "_already_warmed",
    "_mark_warmed",
    "active_dirs_exist",
    # process_tracker
    "_write_pid_file",
    "_remove_pid_file",
    "_process_alive",
    "_read_process_cmdline_windows",
    "_read_process_cmdline_windows_wmi",
    "_process_is_prewarm",
    "is_prewarm_running",
    "wait_for_prewarm",
    "spawn_background_prewarm",
    "get_prewarm_status",
    "_read_prewarm_pid",
    # completion_events
    "_completion_event_name",
    "_create_completion_event",
    "_signal_completion_event",
    "_close_completion_event",
    "_wait_for_completion_event",
    "_wait_completion_windows",
    "_wait_completion_linux",
    "_wait_completion_macos",
    # pipeline
    "run",
    "_run_warming_pipeline",
    "DEFAULT_MIN_FREE_RAM_MB",
    "EXIT_OK",
    "EXIT_DISABLED",
    "EXIT_LOW_RAM",
    "EXIT_NO_MODEL",
    "EXIT_IMPORT_FAILED",
    # cli
    "_parse_args",
    "_print_status",
    "main",
    # stdlib modules bound on the package (for
    # `monkeypatch.setattr(prewarm.X, "attr", ...)`)
    "log",
    "os",
    "sys",
    "time",
    "json",
    "random",
    "select",
    "subprocess",
    "argparse",
    "logging",
    "importlib",
    "Path",
    # platform helpers (for `monkeypatch.setattr(prewarm, "is_windows", ...)`)
    "is_windows",
    "is_linux",
    "is_macos",
]
