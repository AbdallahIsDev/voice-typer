# RESTORED 2026-08-14: the prewarm status surface was removed by the
# runtime-pack-split session as part of master plan §6.2 P-1, but P-1
# only covers the prewarm MACHINERY (separate binary, OS schedulers,
# resolver). The user-facing status/control surface is a product
# feature and was restored — see the addendum at plan §6.3 and the
# worklog "prewarm restoration" section.
#
# Provenance: ``get_prewarm_status``, ``_probe_cache_status`` and the
# TTL cache helpers below are restored VERBATIM from commit 5a319872,
# ``voice_typer/server/prewarm/process_tracker.py`` (lines 827-1012).
# Two machinery couplings were adapted, each marked inline:
#   * the deleted sentinel file (``_pkg._sentinel_path()``) is replaced
#     by the worker-written status file (same fields, same semantics —
#     "when did the last warm run complete + how long did it take");
#   * ``prewarm_running`` (``_pkg.is_prewarm_running()``) is dropped —
#     it reported a tracked background subprocess, and that machinery
#     is gone by design (P-1): warming now runs inside the worker's
#     startup phase, gated by the ``fast_startup`` toggle.
"""Prewarm status probe feeding the Settings About-page Cache Status card.

The card shows: whether prewarm is enabled (the ``fast_startup``
config toggle), the OS-cache health of the active model files
(Hot/Partial/Cold/Unknown badge + cached/total bytes), and the
worker's last warm-run timing (``last_run`` / ``elapsed_s``).

Data sources
------------
- ``fast_startup`` comes from the app config (the toggle in
  Settings → General). The worker skips its warm phase when disabled,
  so the toggle is the user-controlled start/stop switch.
- The cache ratio/bytes come from the same weighted sample probe the
  prewarm machinery has always used (:func:`cache_probe._cache_ratio`
  over :func:`cache_probe._active_model_cache_dirs`), memoized via
  ``_probe_cache_status`` (30 s TTL keyed on directory mtime) so
  frequent IPC polls don't re-walk the HF cache each call.
- ``last_run`` / ``elapsed_s`` are written by the worker after its
  startup warm phase (:func:`write_prewarm_status_file`) — this
  replaces the deleted sentinel file, which historically carried the
  same fields. If the worker never ran, both are ``None``.

Everything is best-effort: a missing model cache, an unreadable
status file, or a config load failure degrades the fields to
``None`` / ``0.0`` / ``"unknown"`` rather than raising.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from voice_typer.server.prewarm.cache_probe import _active_model_cache_dirs, _cache_ratio

log = logging.getLogger("voice_typer.server.prewarm")

# Name of the worker-written status file inside the app config dir.
# Mirrors the deleted ``prewarm/status.json`` sentinel's role: a
# durability point for "when did the last warm run complete + how long
# did it take", now owned by the worker exe (which does the warming).
#
# O4: the prewarm state was consolidated into a single JSON
# (``prewarm-status.json``) — the legacy 3-line sentinel
# (``.prewarm-sentinel``) was deleted with the standalone-prewarm
# machinery (P-1), and the pre-migration ``prewarm_status.json`` name
# (underscore) is migrated to the hyphenated canonical name on first
# read/write (see :func:`_migrate_legacy_status_file`).
_STATUS_FILE_NAME = "prewarm-status.json"

# Legacy pre-O4 filename (underscore). Renamed once to the canonical
# hyphenated name on first access.
_LEGACY_STATUS_FILE_NAME = "prewarm_status.json"

# Label thresholds (ADR-0009 Issue 3, unchanged from the original
# implementation): >= 0.9 resident → "hot", >= 0.1 → "partial",
# below → "cold". No model dirs at all → "unknown".
_HOT_THRESHOLD = 0.9
_PARTIAL_THRESHOLD = 0.1


def _status_file_path() -> Path:
    """Resolve the worker status file inside the app config dir."""
    from voice_typer.server.config import _config_dir

    path = Path(_config_dir()) / _STATUS_FILE_NAME
    # O4: one-time migration of the pre-O4 underscore name. Best-effort —
    # a failed rename (e.g. the file is momentarily locked) falls back to
    # the canonical name on the next write, so the legacy file is never
    # silently clobbered and the migration is idempotent.
    legacy = Path(_config_dir()) / _LEGACY_STATUS_FILE_NAME
    if legacy.exists() and not path.exists():
        try:
            legacy.rename(path)
            log.debug("[PREWARM] migrated legacy %s -> %s", _LEGACY_STATUS_FILE_NAME, _STATUS_FILE_NAME)
        except OSError as exc:
            log.debug("[PREWARM] legacy status-file migration failed: %s", exc)
    return path


def write_prewarm_status_file(*, last_run: str | None, elapsed_s: float | None) -> None:
    """Persist the worker's warm-run timing for the status IPC to read.

    Called by ``voice_typer/worker/_ws_server.py._run_prewarm_phase``
    after its warm phase completes (also when prewarm is disabled —
    then ``last_run`` is ``None`` and ``elapsed_s`` is ``0.0``, which
    the About-page card renders as "not run"). Best-effort: a write
    failure only costs the "last run" row, never correctness.
    """
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

    RESTORED 2026-08-14 (plan §6.3 addendum, second half): the
    user-facing "Run Prewarm Now" control is a product feature, not
    prewarm machinery, so the ``run_prewarm`` IPC is back. This is a
    RE-IMPLEMENTATION for the post-P-1 architecture: the old handler
    spawned a detached ``pythonw -m voice_typer.server.prewarm
    --force`` subprocess, and that machinery is gone by design (P-1).
    The warm phase now lives in :func:`warm_imports_for_worker` (a
    pure file-paging pass over the runtime-pack libraries — safe to
    call from any process), so this runs it on a daemon thread and
    refreshes the status file. Same observable effect — warm the OS
    standby cache on demand — with none of the deleted subprocess
    machinery.

    Returns ``True`` immediately (the run happens in the background);
    the renderer polls ``get_prewarm_status`` for the updated
    ``last_run`` / ``elapsed_s``.
    """
    import threading
    from datetime import datetime

    def _warm() -> None:
        t0 = time.perf_counter()
        try:
            from voice_typer.server.prewarm import warm_imports_for_worker

            warm_imports_for_worker()
        except Exception:
            # Best-effort — a failed manual warm run only costs the
            # cold-start benefit, never correctness (mirrors the
            # worker's own ``_run_prewarm_phase`` handling).
            log.debug("[PREWARM] manual run_prewarm warm phase failed", exc_info=True)
        elapsed = time.perf_counter() - t0
        write_prewarm_status_file(
            last_run=datetime.now().isoformat(timespec="seconds"),
            elapsed_s=round(elapsed, 1),
        )

    threading.Thread(target=_warm, name="prewarm-manual", daemon=True).start()
    return True


def _config_fast_startup() -> bool | None:
    """Read ``fast_startup`` from the on-disk config; ``None`` on failure.

    Falls back to a fresh ``Config.load()`` (the pattern the prewarm
    cache probe has always used) when the caller does not pass the
    live app config. Returns ``None`` if the config cannot be read so
    the caller can decide the default — production code treats
    ``None`` as enabled (the historical default).
    """
    try:
        from voice_typer.server.config import Config

        return bool(getattr(Config.load(), "fast_startup", True))
    except Exception:
        log.debug("[PREWARM] fast_startup config read failed — defaulting through", exc_info=True)
        return None


# ─── Status query (ADR-0009 Issue 3) ──────────────────────────────────────
# Restored verbatim from 5a319872 process_tracker.py:827-935.

_cache_probe_cache: dict = {}
_CACHE_PROBE_TTL_S: float = 30.0

# SU-35: hard cap on ``_cache_probe_cache`` entries. The 30 s TTL at
# the read site governs whether a cached *result* is reused, but the
# dict entry itself is never evicted by the TTL — a process that swaps
# models thousands of times would otherwise leak one fingerprint entry
# per swap. Mirrors the ``streaming.py:385`` pattern
# (``_seen_timestamps`` 50 k cap).
_CACHE_PROBE_MAX_ENTRIES: int = 256


def _probe_cache_status(active_dirs: list[Path]) -> tuple[float, int, int]:
    """Return ``(cache_ratio, cached_bytes, total_bytes)`` for *active_dirs*.

    results are memoized for ``_CACHE_PROBE_TTL_S`` seconds,
        keyed on a fingerprint of each active dir's ``(path, mtime_ns,
        size)``. The fingerprint detects new snapshot downloads (HF hub
        bumps the model dir's mtime when it writes a new symlink) so a
        freshly-downloaded model invalidates the cache immediately. Empty
        ``active_dirs`` returns ``(0.0, 0, 0)`` without polluting the
        cache (so a transient "no model" state doesn't shadow a
        subsequent "model present" probe).
    """
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
        cached_bytes = sum(int(s * r) for s, r in zip(sizes, ratios, strict=True))
        cache_ratio = cached_bytes / total_bytes
    else:
        cached_bytes = 0
        cache_ratio = 0.0

    result = (cache_ratio, cached_bytes, total_bytes)
    _cache_probe_cache[fingerprint] = (now, result)
    # SU-35: enforce the hard cap on every write (mirrors
    # streaming.py:385) so the cap fires on the write path, not just
    # on read.
    _prune_stale_cache_probe_entries()
    return result


def _prune_stale_cache_probe_entries() -> None:
    """SU-35: enforce the ``_cache_probe_cache`` hard cap.

    When ``len(_cache_probe_cache) > _CACHE_PROBE_MAX_ENTRIES``, clear
    the dict wholesale (mirrors the ``streaming.py:385`` pattern).
    Idempotent — no-op when at or below the cap, so no warning fires
    on the common path.
    """
    if len(_cache_probe_cache) > _CACHE_PROBE_MAX_ENTRIES:
        log.warning(
            "[PREWARM] _cache_probe_cache exceeded cap (%d entries) — clearing",
            _CACHE_PROBE_MAX_ENTRIES,
        )
        _cache_probe_cache.clear()


def _invalidate_cache_probe_cache() -> None:
    """Clear the ``_probe_cache_status`` TTL cache (restored from 5a319872).

    Tests call this between assertions to force a re-probe. Production
    code (``get_prewarm_status``) does NOT need to call this — the TTL
    + mtime fingerprint handles invalidation automatically.
    """
    _cache_probe_cache.clear()


def get_prewarm_status(enabled: bool | None = None) -> dict:
    """Return a snapshot of the prewarm cache state for the UI.

    ADR-0009 Issue 3: called by the ``get_prewarm_status`` IPC handler
    to populate the "Cache Status" card in the About page.

    ``enabled`` is read from the app config (Settings → General →
    Fast Startup) — pass the live value from the IPC handler, or leave
    ``None`` to fall back to a fresh on-disk config read.

    the cache-ratio probe is memoized via
        ``_probe_cache_status`` (30 s TTL keyed on directory mtime) so
        frequent IPC polls don't re-walk the HF cache and re-probe every
        weights file each call.

    Returns::

        {
          "enabled": True,                            # fast_startup config toggle
          "cache_ratio": 0.73,                        # 0.0-1.0 (weighted probe)
          "cache_label": "hot" | "partial" | "cold" | "unknown",
          "cached_bytes": 1750000000,                 # estimated bytes resident
          "total_bytes": 2400000000,                  # active model file size
          "last_run": "2026-08-14T09:12:00" | None,   # worker warm-run completion
          "elapsed_s": 20.4 | None,                   # last warm-run seconds
        }

    Best-effort: a missing model cache, an unreadable status file, or
    a config load failure degrades the fields to ``None`` / ``0.0`` /
    ``"unknown"`` rather than raising. Safe to call from the IPC
    handler thread (small random 4K reads, ~1 ms total — see
    :func:`cache_probe._cache_ratio`).
    """
    if enabled is None:
        enabled = _config_fast_startup() is not False

    # ── Cache ratio probe (TTL-memoized via _probe_cache_status) ──
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

    # ── Label: hot / partial / cold / unknown ───────────────────────
    # Adapted from the original: "sentinel_exists" was dropped with the
    # sentinel-file machinery; the worker status file replaces it.
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

    # ── Worker warm-run timing (replaces the deleted sentinel) ──────
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
