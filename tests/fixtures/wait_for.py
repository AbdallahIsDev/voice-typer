"""Shared polling helper using ``time.monotonic()``."""

from __future__ import annotations

import time
from collections.abc import Callable


def wait_for(
    predicate: Callable[[], bool],
    timeout: float = 2.0,
    interval: float = 0.005,
) -> bool:
    """Poll ``predicate`` until truthy or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())
