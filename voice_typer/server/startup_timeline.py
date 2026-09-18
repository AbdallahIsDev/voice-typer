"""One-line launch-timeline attribution for the startup log.

The host process that spawns the backend stamps two epoch markers into
the backend's environment:

- ``VOICE_TYPER_BOOT_EPOCH_MS``: set at host process start. predecessor
  stamps it at main-bundle eval time (≈ predecessor process boot); the
  Tauri host records it as the first statement of ``main``
  (``src-tauri/src/startup_timeline.rs``).
- ``VOICE_TYPER_SPAWN_EPOCH_MS``: set immediately before the Python
  backend process is spawned (re-stamped fresh on EVERY spawn,
  including supervisor respawns).

:func:`log_launch_timeline` turns those markers into a single INFO line
at the backend's first log moment, e.g.::

    [STARTUP] Launch timeline: host boot 1.8s (host start to first log),
    backend init 6.2s (backend spawn to first log)

so the previously-invisible gap between the host spawning the backend
and the backend's first log line is attributed on every hosted launch —
host-side boot vs Python-side interpreter + import time, without any
extra tooling. Markers are absent on standalone (non-host) launches
such as manual ``python -m`` runs, in which case the line is skipped.
"""

from __future__ import annotations

import logging
import os
import time

from voice_typer.server.duration import format_duration

BOOT_EPOCH_ENV = "VOICE_TYPER_BOOT_EPOCH_MS"
SPAWN_EPOCH_ENV = "VOICE_TYPER_SPAWN_EPOCH_MS"


def _epoch_delta_s(env_name: str, now_s: float) -> float | None:
    """Return ``now_s - epoch`` in seconds, or ``None`` if unset/garbage."""
    raw = os.environ.get(env_name)
    if not raw:
        return None
    try:
        return max(0.0, now_s - float(raw) / 1000.0)
    except ValueError:
        return None


def log_launch_timeline(logger: logging.Logger) -> None:
    """Emit the merged launch-timeline line (no-op without markers)."""
    now_s = time.time()
    parts: list[str] = []
    boot = _epoch_delta_s(BOOT_EPOCH_ENV, now_s)
    if boot is not None:
        parts.append(f"host boot{format_duration(boot)} (host start to backend first log)")
    spawn = _epoch_delta_s(SPAWN_EPOCH_ENV, now_s)
    if spawn is not None:
        parts.append(f"backend init{format_duration(spawn)} (backend spawn to first log)")
    if parts:
        logger.info("[STARTUP] Launch timeline: %s", ", ".join(parts))


def backend_spawn_monotonic() -> float | None:
    """Monotonic-clock value of the backend-spawn epoch marker.

    Converts the ``VOICE_TYPER_SPAWN_EPOCH_MS`` wall-clock marker (stamped
    by the host immediately before spawning us) onto this process's
    monotonic clock, so durations measured with ``time.perf_counter()``
    can be anchored at process spawn instead of at an arbitrary later
    point. Returns ``None`` when the marker is absent or garbage
    (standalone ``python -m`` runs, tests), callers fall back to
    anchoring at their own start.
    """
    delta = _epoch_delta_s(SPAWN_EPOCH_ENV, time.time())
    if delta is None:
        return None
    return time.monotonic() - delta


__all__ = [
    "BOOT_EPOCH_ENV",
    "SPAWN_EPOCH_ENV",
    "backend_spawn_monotonic",
    "log_launch_timeline",
]
