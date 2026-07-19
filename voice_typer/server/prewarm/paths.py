# ARCH-045 / SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""Config-root, sentinel, PID-file, and boot-session dedup helpers.

Phase 4.5 / ARCH-045 — this module holds the path-resolution and
boot-session dedup helpers:

- :func:`_config_root` — return the voice-typer config directory (parent
  of the HF cache).
- :func:`_sentinel_path` — return the path to the prewarm sentinel file
  (records successful prewarm for this boot session).
- :func:`_pid_file_path` — return the path to the prewarm PID file
  (read by ``is_prewarm_running`` to check if prewarm is active).
- :func:`_boot_time` — return system boot time as Unix timestamp.
- :func:`_already_warmed` — return True if prewarm already succeeded in
  this boot session (sentinel check).
- :func:`_mark_warmed` — record successful prewarm for this boot session.
- :func:`active_dirs_exist` — return True if any HF cache model dir exists.

Patch-path compatibility
------------------------
Tests patch ``_boot_time``, ``_sentinel_path``, and
``_active_model_cache_dirs`` on the package namespace, so
:func:`_already_warmed`, :func:`_mark_warmed`, and
:func:`active_dirs_exist` must look those up via ``_pkg.X()`` at call
time so the patches take effect.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

# Patch-path bridge: route lookups of cross-submodule helpers through
# the package namespace so test patches of the form
# ``monkeypatch.setattr(prewarm, "_boot_time", ...)`` /
# ``monkeypatch.setattr(prewarm, "_sentinel_path", ...)`` /
# ``monkeypatch.setattr(prewarm, "_active_model_cache_dirs", ...)``
# keep affecting production code defined here.
from voice_typer.server import prewarm as _pkg
from voice_typer.server.platform_utils import is_windows

# _resolve_hf_cache_dir is NOT patched by any test, so we can bind it
# directly at import time.
from .cache_probe import _resolve_hf_cache_dir

log = logging.getLogger("voice_typer.server.prewarm")


def _config_root() -> Path:
    """Return the voice-typer config directory (parent of the HF cache).

    ADR-0009 Issue 1 (review fix C1): the sentinel and PID file paths
    are derived from this function so they use the SAME resolution chain
    as the HF cache dir. This ensures prewarm (BootTrigger, env vars
    possibly missing) and the app (post-logon, env vars set) agree on
    the path — critical for the PID-file handshake.
    """
    return _resolve_hf_cache_dir().parent


def _sentinel_path() -> Path:
    """Return the path to the prewarm sentinel file.

    ADR-0009 Issue 1 (review fix C1): resolved lazily via
    ``_config_root()`` so it benefits from the BootTrigger fallback chain.
    The old module-level constant ``Path.home() / ".voice-typer" /
    ".prewarm-sentinel"`` was evaluated at import time and could produce
    a relative "~\\..." path on Windows or a root "/..." path on POSIX
    when env vars were unset.
    """
    return _config_root() / ".prewarm-sentinel"


def _pid_file_path() -> Path:
    """Return the path to the prewarm PID file.

    ADR-0009 Issue 1 (review fix C1): same lazy resolution as
    ``_sentinel_path()``. Critical for the app↔prewarm handshake — both
    sides must agree on the path.
    """
    return _config_root() / ".prewarm.pid"


# ─── Boot-session dedup (prevent LogonTrigger re-fire) ────────────────────


def _boot_time() -> int | None:
    """Return system boot time as Unix timestamp, or None."""
    try:
        import psutil

        return int(psutil.boot_time())
    except Exception:
        pass
    if is_windows():
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # GetTickCount64 -> ms since boot, subtract from now.
            ms = kernel32.GetTickCount64()
            return int(time.time() - ms / 1000)
        except Exception:
            pass
    return None


def _already_warmed() -> bool:
    """Return True if prewarm already succeeded in this boot session.

    ADR-0009 Issue 3: the sentinel file may now contain TWO lines
    (boot_ts + elapsed_s). We read only the first line so old
    single-line sentinels written by previous builds still work.
    """
    try:
        bt = _pkg._boot_time()
        sentinel = _pkg._sentinel_path()
        if bt is None or not sentinel.exists():
            return False
        content = sentinel.read_text()
        first_line = content.split("\n", 1)[0].strip()
        if not first_line:
            return False
        stored = int(first_line)
        return stored == bt
    except (ValueError, OSError):
        return False
    except Exception:
        return False


def _mark_warmed(elapsed_s: float) -> None:
    """Record successful prewarm for this boot session.

    ADR-0009 Issue 3: stores ``boot_ts\nelapsed_s`` so the
    ``get_prewarm_status`` IPC endpoint can show "Last run: 20.4s" in the
    About page without re-probing the cache. ``_already_warmed()`` reads
    only line 1, so the format change is backward-compatible.
    """
    try:
        bt = _pkg._boot_time()
        if bt is None:
            return
        sentinel = _pkg._sentinel_path()
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        # ADR-0009 Issue 3 (review fix H2): store THREE lines:
        #   line 1: boot timestamp (dedup key)
        #   line 2: elapsed seconds (for the About page)
        #   line 3: wall-clock completion time (ISO 8601) so the UI can
        #           show "Last run: 3 hours ago". Line 1 is the BOOT
        #           time, not the completion time.
        import datetime as _dt

        from voice_typer.server.config import _secure_atomic_write

        now_iso = _dt.datetime.now().isoformat(timespec="seconds")
        _secure_atomic_write(sentinel, f"{bt}\n{elapsed_s:.1f}\n{now_iso}")
    except Exception:
        # Review fix H5: log the failure (was silently swallowed).
        # A failed sentinel write means the next prewarm trigger will
        # re-warm (wasted work) and the About page will show "Last run:
        # None" — both are user-visible and need a diagnostic.
        log.warning(
            "[PREWARM] could not write sentinel file %s",
            _pkg._sentinel_path(),
            exc_info=True,
        )


def active_dirs_exist() -> bool:
    """Return True if any HF cache model dir exists.

    ADR-0009 Issue 3: helper for ``get_prewarm_status()`` so the label
    can distinguish "never ran + no model" (``unknown``) from "ran but
    cache evicted" (``cold``). Kept tiny so it can be monkey-patched in
    tests without spinning up the full HF cache walk.
    """
    try:
        return bool(_pkg._active_model_cache_dirs())
    except Exception:
        return False
