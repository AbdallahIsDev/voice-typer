"""Centralised ``functools.lru_cache`` reset helper for tests."""

from __future__ import annotations

import importlib
from collections.abc import Iterable

CACHES_TO_CLEAR: tuple[tuple[str, str], ...] = (
    (
        "voice_typer.server.native_hotkeys.binary_path",
        "get_native_binary_path",
    ),
    (
        # ``_config_dir`` is memoized with ``functools.lru_cache(maxsize=1)``
        "voice_typer.server.config_internals.paths",
        "_config_dir",
    ),
    (
        "voice_typer.server.clipboard.linux",
        "_shutil_which_cached",
    ),
    (
        "voice_typer.server.prewarm.cache_probe",
        "_resolve_hf_cache_dir",
    ),
    (
        "voice_typer.server.prewarm.cache_probe",
        "_cached_active_config",
    ),
)


def clear_caches(
    caches: Iterable[tuple[str, str]] | None = None,
) -> None:
    """Clear ``functools.lru_cache`` decorations on a fixed set of helpers."""
    entries = tuple(caches) if caches is not None else CACHES_TO_CLEAR
    for module_path, attr_name in entries:
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            continue
        cached = getattr(module, attr_name)
        cache_clear = getattr(cached, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()
