"""Shared condition-wait helpers for tests, replacing fixed ``time.sleep`` calls.

This module is the TEST-2 migration entry point (see
``docs/adr/test-2-time-sleep-migration.md``). It exposes two canonical
helpers that test code SHOULD import in preference to bare
``time.sleep(N)`` synchronization:

- :func:`wait_until` — poll a zero-argument predicate until it returns
  truthy or the timeout elapses. Returns ``True`` on success, ``False``
  on timeout (caller decides whether to ``assert`` / ``pytest.fail`` /
  treat as expected).
- :func:`wait_for_event` — bounded wrapper around
  :meth:`threading.Event.wait`. Returns ``True`` if the event was set
  before the timeout, ``False`` otherwise.

DRY policy (E7 / P2)
-------------------
The canonical polling implementation already exists as
:func:`tests.fixtures.wait_for.wait_for` (a previous wave extracted it
from the deleted ``tests.conftest.wait_until`` helper — see the module
docstring of ``tests/fixtures/wait_for.py`` for the history).
``wait_helpers.wait_until`` is a *thin alias* that re-uses
``wait_for.wait_for`` rather than re-implementing the poll loop. This
keeps a single source of truth for the polling semantics (``time.monotonic``
deadline, default 5 ms interval, final post-loop check) so a future
fix to the poller only has to land in one place.

The alias exists so TEST-2 migrations can adopt a single, descriptive
import name (``wait_until``) without forcing churn on the existing
``wait_for`` importers (``tests/test_microphone_watcher.py``,
``tests/test_hotkeys_win32.py``, ``tests/hotkeys/test_polling_strategy.py``).
New code SHOULD import from this module; existing code MAY continue to
import ``wait_for`` directly.

W2 (prefer existing libraries): ``polling2`` is not installed in this
project's ``.venv`` (verified at migration time). Falling back to the
in-repo minimal poller per W2's "else build minimal ``wait_until``"
clause.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

# Re-export the canonical poller so callers can import either name from
# this module without paying a second copy of the implementation.
from tests.fixtures.wait_for import wait_for


def wait_until(
    predicate: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.005,
) -> bool:
    """Poll ``predicate`` until truthy or ``timeout`` elapses.

    Thin alias for :func:`tests.fixtures.wait_for.wait_for` — see that
    function's docstring for the full semantics (``time.monotonic``
    deadline, 5 ms default interval, final post-loop check so a
    truthy predicate observed during the last ``time.sleep(interval)``
    window is not missed).

    Parameters
    ----------
    predicate:
        Zero-argument callable returning a truthy/falsy value.
    timeout:
        Maximum wall-clock seconds to spend polling. Defaults to 5.0 s
        — generous for CI runner scheduling latency. For sub-100 ms
        synchronization prefer :func:`wait_for_event` with a
        ``threading.Event``.
    interval:
        Sleep duration between predicate evaluations. Default 5 ms
        keeps the poll loop responsive without burning CPU.

    Returns
    -------
    bool
        ``True`` if ``predicate()`` returned truthy before the
        deadline, ``False`` otherwise. Callers SHOULD ``assert`` the
        return value when the predicate is expected to succeed (so a
        timeout surfaces as a test failure with a useful traceback
        rather than a silent ``False`` propagating into a downstream
        assertion).
    """
    return wait_for(predicate, timeout=timeout, interval=interval)


def wait_for_event(event: threading.Event, timeout: float = 5.0) -> bool:
    """Wait for a ``threading.Event`` to be set.

    Thin wrapper around :meth:`threading.Event.wait`. Prefer this over
    :func:`wait_until` whenever the synchronization primitive is
    already a ``threading.Event`` — ``Event.wait`` is non-busy (the OS
    parks the calling thread) and deterministic, where ``wait_until``
    polls at a fixed interval.

    Parameters
    ----------
    event:
        The ``threading.Event`` to wait on.
    timeout:
        Maximum wall-clock seconds to wait. Default 5.0 s.

    Returns
    -------
    bool
        ``True`` if the event was set before the timeout, ``False``
        otherwise. ``threading.Event.wait`` already returns this
        boolean — the wrapper exists so test code can import a single
        canonical ``wait_for_event`` name alongside ``wait_until``.
    """
    return event.wait(timeout)


__all__ = ["wait_until", "wait_for_event", "wait_for"]
