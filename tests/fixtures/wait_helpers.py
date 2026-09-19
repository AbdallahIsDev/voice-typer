"""Shared condition-wait helpers for tests, replacing fixed ``time.sleep`` calls."""

from __future__ import annotations

import threading
from collections.abc import Callable

from tests.fixtures.wait_for import wait_for


def wait_until(
    predicate: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.005,
) -> bool:
    """Poll ``predicate`` until truthy or ``timeout`` elapses."""
    return wait_for(predicate, timeout=timeout, interval=interval)


def wait_for_event(event: threading.Event, timeout: float = 5.0) -> bool:
    """Wait for a ``threading.Event`` to be set."""
    return event.wait(timeout)


__all__ = ["wait_until", "wait_for_event", "wait_for"]
