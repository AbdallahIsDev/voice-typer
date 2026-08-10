"""Shared polling helper using ``time.monotonic()``.

Replaces bare ``time.sleep(N)`` patterns in tests with a deterministic
poller that bails as soon as the predicate becomes truthy.

This module is the canonical polling helper (the older
``wait_until`` in :mod:`tests.conftest` was DELETED because it had zero
importers — see the note in tests/conftest.py). ``wait_for`` is already
imported by tests/test_microphone_watcher.py,
tests/test_hotkeys_win32.py and tests/hotkeys/test_polling_strategy.py.

``wait_for`` is the canonical replacement for the ~40 bare
``time.sleep(N)`` call sites flagged in review.md. It uses
``time.monotonic()`` (NOT ``time.time()``) so the deadline is immune
to wall-clock adjustments (NTP slew, manual date changes, daylight
saving transitions) — ``time.time()`` can jump backward and cause a
poll loop to terminate early or spin forever.

The helper deliberately returns a ``bool`` instead of raising:

  - ``True``  — predicate became truthy before ``timeout`` elapsed.
  - ``False`` — timeout elapsed without the predicate ever returning
                truthy. The caller decides whether to ``assert``, skip,
                or proceed.

For asserting-style waits (fail the test on timeout with a message),
wrap the call, e.g.::

    if not wait_for(predicate, timeout=2.0):
        raise AssertionError(f"predicate never became truthy")

For thread-synchronization tests, prefer ``threading.Event.wait(timeout)``
over either helper — ``Event.wait`` is non-busy and deterministic.
``wait_for`` is appropriate when no ``Event``/``Condition`` is
available (e.g. waiting for a side effect on a MagicMock, a file on
disk, or a thread state the test can't directly observe).
"""

from __future__ import annotations

import time
from collections.abc import Callable


def wait_for(
    predicate: Callable[[], bool],
    timeout: float = 2.0,
    interval: float = 0.005,
) -> bool:
    """Poll ``predicate`` until truthy or ``timeout`` elapses.

    Parameters
    ----------
    predicate:
        Zero-argument callable returning a truthy/falsy value. Called
        repeatedly until it returns truthy (success) or ``timeout``
        elapses (failure).
    timeout:
        Maximum wall-clock seconds to spend polling. Measured with
        ``time.monotonic()`` so the deadline is immune to wall-clock
        adjustments. Default ``2.0`` is generous enough for most
        thread-scheduling latency on a loaded CI runner.
    interval:
        Sleep duration between predicate evaluations. Default
        ``0.005`` (5 ms) keeps the poll loop responsive without
        burning CPU. For sub-millisecond synchronization, use
        ``threading.Event.wait`` instead.

    Returns
    -------
    bool
        ``True`` if ``predicate()`` returned truthy before the
        deadline, ``False`` otherwise. The caller decides whether a
        ``False`` return is a test failure (``assert``), a skip, or
        expected behaviour.

    Notes
    -----
    Migrating ``time.sleep`` call sites: replace ::

        time.sleep(0.5)
        assert obj.ready

    with ::

        assert wait_for(lambda: obj.ready, timeout=2.0)

    The migration is incremental — call sites that need the asserting
    variant (with a synthesised message on timeout) should wrap the
    call and raise ``AssertionError`` themselves (see the module
    docstring; the old ``tests.conftest.wait_until`` helper was removed).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    # One final check after the loop so we don't return False when the
    # predicate became truthy during the last ``time.sleep(interval)``
    # window — the deadline check fires first and we'd miss it.
    return bool(predicate())
