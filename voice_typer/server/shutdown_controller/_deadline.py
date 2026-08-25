"""Shutdown-deadline budget helpers (module-level).

Extracted verbatim from the pre-split ``shutdown_controller`` module so
the extracted plan-building helpers
(``_build_sequenced_plan`` / ``_build_parallel_plan``) and ``_run_plan``
(inter-step deadline check) can share the same deadline-budget logic
without re-defining the closures or passing them as parameters. Reading
*deadline* as a parameter (instead of capturing a local) keeps the
helpers pure functions of the deadline.
"""

from __future__ import annotations

import time


def _shutdown_remaining(deadline: float) -> float:
    """Return the remaining seconds until *deadline* (clamped to 0).

    The clamp to 0 ensures downstream ``< 5.0`` (near) and
    ``min(step_timeout, remaining)`` (cap) computations never go
    negative when the deadline has already passed.
    """
    return max(0.0, deadline - time.monotonic())


def _shutdown_deadline_near(deadline: float) -> bool:
    """True when less than 5s remain before *deadline*.

    The 5s threshold gates the skip of non-critical teardowns so the
    remaining budget can be spent on critical flushes (history_db,
    crash_recovery, recorder.stop, mutex, PID file) + the late
    ``tray.stop`` bookend.
    """
    return _shutdown_remaining(deadline) < 5.0
