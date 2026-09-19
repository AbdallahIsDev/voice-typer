"""Prewarm, cache-probe package (pure re-export shim)."""

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
    _model_weight_files,
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
    "_model_weight_files",
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
