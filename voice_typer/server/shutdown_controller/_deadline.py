"""Shutdown-deadline budget helpers (module-level)."""

from __future__ import annotations

import time


def _shutdown_remaining(deadline: float) -> float:
    """Return the remaining seconds until *deadline* (clamped to 0)."""
    return max(0.0, deadline - time.monotonic())


def _shutdown_deadline_near(deadline: float) -> bool:
    """True when less than 5s remain before *deadline*."""
    return _shutdown_remaining(deadline) < 5.0
