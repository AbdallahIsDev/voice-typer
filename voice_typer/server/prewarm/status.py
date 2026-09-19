# RESTORED 2026-08-14: the prewarm status surface was removed by the
"""Prewarm status probe feeding the Settings About-page Cache Status card."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from voice_typer.server.prewarm.cache_probe import (
    _active_model_cache_dirs,
    _cache_ratio,
    _model_weight_files,
)

log = logging.getLogger("voice_typer.server.prewarm")

# Name of the worker-written status file inside the app config dir.
_STATUS_FILE_NAME = "prewarm-status.json"

# Legacy pre-O4 filename (underscore). Renamed once to the canonical
_LEGACY_STATUS_FILE_NAME = "prewarm_status.json"

# Label thresholds (ADR-0009 Issue 3, unchanged from the original
_HOT_THRESHOLD = 0.9
_PARTIAL_THRESHOLD = 0.1


def _status_file_path() -> Path:
    """Resolve the worker status file inside the app config dir."""
    from voice_typer.server.config import _config_dir

    path = Path(_config_dir()) / _STATUS_FILE_NAME
    # O4: one-time migration of the pre-O4 underscore name. Best-effort —
    legacy = Path(_config_dir()) / _LEGACY_STATUS_FILE_NAME
    if legacy.exists() and not path.exists():
        try:
            legacy.rename(path)
            log.debug("[PREWARM] migrated legacy %s -> %s", _LEGACY_STATUS_FILE_NAME, _STATUS_FILE_NAME)
        except OSError as exc:
            log.debug("[PREWARM] legacy status-file migration failed: %s", exc)
    return path


def write_prewarm_status_file(*, last_run: str | None, elapsed_s: float | None) -> None:
    """Persist the worker's warm-run timing for the status IPC to read."""
    try:
        payload = {
            "last_run": last_run,
            "elapsed_s": elapsed_s,
        }
        _status_file_path().write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        log.debug("[PREWARM] write_prewarm_status_file failed", exc_info=True)


def _read_status_file() -> dict:
    """Best-effort read of the worker's last warm-run timing."""
    try:
        data = json.loads(_status_file_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    except Exception:
        log.debug("[PREWARM] _read_status_file failed", exc_info=True)
    return {}


def run_prewarm_now() -> bool:
    """Trigger the warm phase now, in a background thread.

    Returns ``True`` immediately (the run happens in the background);
    """
    import threading
    from datetime import datetime

    def _warm() -> None:
        t0 = time.perf_counter()
        try:
            from voice_typer.server.prewarm import warm_imports_for_worker

            warm_imports_for_worker()
        except Exception:
            # Best-effort, a failed manual warm run only costs the
            log.debug("[PREWARM] manual run_prewarm warm phase failed", exc_info=True)
        elapsed = time.perf_counter() - t0
        write_prewarm_status_file(
            last_run=datetime.now().isoformat(timespec="seconds"),
            elapsed_s=round(elapsed, 1),
        )

    threading.Thread(target=_warm, name="prewarm-manual", daemon=True).start()
    return True


def _config_fast_startup() -> bool | None:
    """Read ``fast_startup`` from the on-disk config; ``None`` on failure."""
    try:
        from voice_typer.server.config import Config

        return bool(getattr(Config.load(), "fast_startup", True))
    except Exception:
        log.debug("[PREWARM] fast_startup config read failed, defaulting through", exc_info=True)
        return None


# Restored verbatim from 5a319872 process_tracker.py:827-935.

_cache_probe_cache: dict = {}
_CACHE_PROBE_TTL_S: float = 30.0

# Hard cap on ``_cache_probe_cache`` entries. The 30 s TTL at
_CACHE_PROBE_MAX_ENTRIES: int = 256


def _probe_cache_status(active_dirs: list[Path]) -> tuple[float, int, int]:
    """Return ``(cache_ratio, cached_bytes, total_bytes)`` for *active_dirs*."""
    if not active_dirs:
        return (0.0, 0, 0)

    fingerprint_parts: list[tuple[str, int, int]] = []
    for d in active_dirs:
        try:
            st = d.stat()
            fingerprint_parts.append((str(d), st.st_mtime_ns, st.st_size))
        except OSError:
            fingerprint_parts.append((str(d), 0, 0))
    fingerprint = tuple(fingerprint_parts)

    now = time.monotonic()
    cached = _cache_probe_cache.get(fingerprint)
    if cached is not None:
        ts, result = cached
        if now - ts < _CACHE_PROBE_TTL_S:
            return result

    sizes: list[int] = []
    ratios: list[float] = []
    total_bytes = 0
    # Probe the ACTIVE payload files via the shared weight-file finder
    for weights in _model_weight_files(active_dirs):
        try:
            size = weights.stat().st_size
        except OSError:
            continue
        sizes.append(size)
        ratios.append(_cache_ratio(weights))
        total_bytes += size
    if sizes and total_bytes > 0:
        cached_bytes = sum(int(s * r) for s, r in zip(sizes, ratios, strict=True))
        cache_ratio = cached_bytes / total_bytes
    else:
        cached_bytes = 0
        cache_ratio = 0.0

    result = (cache_ratio, cached_bytes, total_bytes)
    _cache_probe_cache[fingerprint] = (now, result)
    # Enforce the hard cap on every write (mirrors
    _prune_stale_cache_probe_entries()
    return result


def _prune_stale_cache_probe_entries() -> None:
    """Enforce the ``_cache_probe_cache`` hard cap."""
    if len(_cache_probe_cache) > _CACHE_PROBE_MAX_ENTRIES:
        log.warning(
            "[PREWARM] _cache_probe_cache exceeded cap (%d entries), clearing",
            _CACHE_PROBE_MAX_ENTRIES,
        )
        _cache_probe_cache.clear()


def _invalidate_cache_probe_cache() -> None:
    """Clear the ``_probe_cache_status`` TTL cache (restored from 5a319872)."""
    _cache_probe_cache.clear()


def get_prewarm_status(enabled: bool | None = None) -> dict:
    """Return a snapshot of the prewarm cache state for the UI.

    Returns::
    """
    if enabled is None:
        enabled = _config_fast_startup() is not False

    active_dirs: list[Path] = []
    try:
        active_dirs = _active_model_cache_dirs()
    except Exception:
        log.debug("[PREWARM] get_prewarm_status active_dirs lookup failed", exc_info=True)
    try:
        cache_ratio, cached_bytes, total_bytes = _probe_cache_status(active_dirs)
    except Exception:
        log.debug("[PREWARM] get_prewarm_status cache probe failed", exc_info=True)
        cache_ratio, cached_bytes, total_bytes = 0.0, 0, 0

    # Adapted from the original: "sentinel_exists" was dropped with the
    status_file = _read_status_file()
    worker_ran = bool(status_file)
    active_dirs_any = bool(active_dirs)
    if not worker_ran and not active_dirs_any:
        label = "unknown"
    elif cache_ratio >= 0.9:
        label = "hot"
    elif cache_ratio >= 0.1:
        label = "partial"
    else:
        label = "cold"

    last_run = status_file.get("last_run")
    elapsed_s = status_file.get("elapsed_s")
    if last_run is not None and not isinstance(last_run, str):
        last_run = None
    if elapsed_s is not None and not isinstance(elapsed_s, (int, float)):
        elapsed_s = None

    return {
        "enabled": enabled,
        "cache_ratio": round(cache_ratio, 2),
        "cache_label": label,
        "cached_bytes": cached_bytes,
        "total_bytes": total_bytes,
        "last_run": last_run,
        "elapsed_s": elapsed_s,
    }
