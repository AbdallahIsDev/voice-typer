"""Prewarm — cache-probe package (pure re-export shim).

This package owns two modules:

- :mod:`.cache_probe` — HF-cache probing + file-warming primitives that
  page the runtime-pack libraries' files into the OS standby cache
  without importing them (:func:`cache_probe.warm_imports_for_worker`
  is the public entry point the worker exe calls once at startup).
- :mod:`.status` — the prewarm status probe feeding the About page's
  Cache Status card, plus the worker-written status-file writer.

This ``__init__`` exists only to re-export those modules' names so
``from voice_typer.server.prewarm import X`` keeps working for existing
consumers (the worker entry point, IPC handlers, tests). All names are
genuinely defined in the submodules above — nothing is implemented here.
"""

from __future__ import annotations

from .cache_probe import (
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
from .status import (
    get_prewarm_status,
    write_prewarm_status_file,
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
    # status (user-facing About-page Cache Status card feature)
    "get_prewarm_status",
    "write_prewarm_status_file",
]
