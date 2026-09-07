"""One-line launch-timeline attribution for the startup log.

The host process that spawns the backend stamps two epoch markers into
the backend's environment:

- ``VOICE_TYPER_BOOT_EPOCH_MS`` — set at host process start. Electron
  stamps it at main-bundle eval time (≈ Electron process boot); the
  Tauri host records it as the first statement of ``main``
  (``src-tauri/src/startup_timeline.rs``).
- ``VOICE_TYPER_SPAWN_EPOCH_MS`` — set immediately before the Python
  backend process is spawned (re-stamped fresh on EVERY spawn,
  including supervisor respawns).

:func:`log_launch_timeline` turns those markers into a single INFO line
at the backend's first log moment, e.g.::

    [STARTUP] Launch timeline: host boot 1.8s, backend init 6.2s

so the previously-invisible gap between the host spawning the backend
and the backend's first log line is attributed on every hosted launch —
host-side boot vs Python-side interpreter + import time — without any
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
        parts.append(f"host boot{format_duration(boot)}")
    spawn = _epoch_delta_s(SPAWN_EPOCH_ENV, now_s)
    if spawn is not None:
        parts.append(f"backend init{format_duration(spawn)}")
    if parts:
        logger.info("[STARTUP] Launch timeline: %s", ", ".join(parts))


__all__ = [
    "BOOT_EPOCH_ENV",
    "SPAWN_EPOCH_ENV",
    "log_launch_timeline",
]
