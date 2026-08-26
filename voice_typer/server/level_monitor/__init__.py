"""Continuous microphone level monitoring + ad-hoc test recording.

Opens a single sounddevice InputStream that serves TWO purposes:
  1. Continuous level monitoring — computes RMS/peak on every chunk so
     the frontend can show a live level bar at all times.
  2. Microphone test recording — when a test is active, the same callback
     also appends chunks to a test buffer.  When the test ends, the
     accumulated audio is encoded as WAV and returned.

By using ONE stream for both roles, we eliminate the PortAudio device
conflict that occurred on Windows when two separate sd.InputStream
instances tried to open the same device simultaneously (MME host API
only allows one open stream per device).

Thread safety: uses a threading.Lock to protect shared state; the audio
callback writes under the lock, and get_level() / stop_test_recording()
read under the lock.

Resource usage: 512-sample blocks at device native rate.  Test audio is
stored as a list of numpy arrays in memory (max ~30 s of float32 mono).

(c-review PERF-03): the PortAudio callback previously ran
the FULL filter chain (may include RNNoise, 5–50 ms per chunk on CPU),
allocated squared + abs arrays for RMS/peak, and appended
``indata.copy()`` to two test lists — all under ``_monitor_lock``.
That violated the ~32 ms PortAudio deadline whenever the level monitor
was active. The callback now does ONLY ``deque.append((indata.copy(),
status))`` + ``Event.set()`` (~10 µs). All heavy work runs on a
dedicated worker thread (``_level_worker_loop``) that drains the ring
buffer under ``_monitor_lock`` — the same pattern used by
``recording.py``'s audio callback since

=====================================================================
package split
=====================================================================
This file was previously a 1586-line god-module
(``voice_typer/server/level_monitor.py``); it has been split into a
package with one module per concern:

- Shared mutable state (singleton ``_state`` instance holding the 27
  module-level globals from the pre-refactor module) — :mod:`._state`
- Continuous level-monitoring public API (``start_monitoring``,
  ``stop_monitoring``, ``is_monitoring``, ``get_level``,
  ``get_level_diagnostics``, ``update_level_processor``) plus the
  ``device_lost`` / ``mic_level`` push-event helpers — :mod:`.monitoring`
- Ad-hoc microphone test recording public API
  (``start_test_recording``, ``stop_test_recording``,
  ``cancel_test_recording``, ``is_test_active``,
  ``update_test_filters``) plus the auto-stop / cancel helpers —
  :mod:`.test_recording`
- Level worker thread (``_level_worker_loop``, ``_process_level_chunk``,
  ``_ensure_level_worker_running``, ``_stop_level_worker``) —
  :mod:`.worker`

This ``__init__.py`` re-exports every public + private name that the
original module exposed so existing imports of the form
``from voice_typer.server.level_monitor import X`` keep working without
modification.

---------------------------------------------------------------------
Test-patch compatibility (custom module class)
---------------------------------------------------------------------
The test suite accesses ~27 module-level globals directly via
``lm._test_mode``, ``lm._test_chunks``, ``lm._monitor_active``,
``lm._level_processor``, ``lm._monitor_lock``, etc. — and EXPECTS
writes (``lm._test_mode = False``) to propagate to the production code
that reads them (the level worker thread, the monitoring public API,
etc.).

Without indirection, those reads/writes would land on the package's
own ``__dict__`` (a stale snapshot of the state captured at import
time), and the submodules that actually own + read the state (via
``_state._X``) would never see the test's write — the test would
silently no-op.

The fix mirrors :mod:`voice_typer.server.recording`'s pattern: install
a custom module subclass (``_LevelMonitorModule``, defined at the
bottom of this file) whose ``__getattr__`` and ``__setattr__`` route
``_``-prefixed attribute access through to the singleton ``_state``
instance in :mod:`._state`. So:

    lm._test_mode              →  _state._test_mode
    lm._test_mode = False      →  _state._test_mode = False
    lm._test_chunks.clear()    →  _state._test_chunks.clear()
    lm._test_chunks.append(x)  →  _state._test_chunks.append(x)
    lm._monitor_lock.acquire() →  _state._monitor_lock.acquire()
    lm._level_worker_stop_event.set() → _state._level_worker_stop_event.set()

This preserves every test access pattern documented in
"""

from __future__ import annotations

# ─── Top-of-module imports ──────────────────────────────────────────────
# Re-exported for backward compatibility — the original module bound these
# names at module top, and tests / production code that does
# ``from voice_typer.server import level_monitor; level_monitor.np``
# expects them to keep working.
import base64  # noqa: F401 — re-exported
import collections  # noqa: F401 — re-exported
import contextlib  # noqa: F401 — re-exported
import io  # noqa: F401 — re-exported
import logging
import threading  # noqa: F401 — re-exported
import time  # noqa: F401 — re-exported
import types  # noqa: F401 — re-exported
from typing import Any  # noqa: F401 — re-exported

import numpy as np  # noqa: F401 — re-exported

log = logging.getLogger(__name__)

# ─── State singleton + class re-export ──────────────────────────────────
from ._state import _State, _state  # noqa: E402, F401

# ─── Public + private API re-exports ────────────────────────────────────
# Each name below is genuinely defined in a sibling submodule.  We import
# it here so ``from voice_typer.server.level_monitor import X`` keeps
# working for both production code and the test suite.
from .monitoring import (  # noqa: E402
    _emit_device_lost,  # noqa: F401
    _ensure_mic_level_worker_running,  # noqa: F401
    _idle_timeout_auto_stop,  # noqa: F401
    _level_stream_finished,  # noqa: F401
    _mic_level_worker_loop,  # noqa: F401
    _push_mic_level,  # noqa: F401
    _stop_mic_level_worker,  # noqa: F401
    get_level,
    get_level_diagnostics,
    is_monitoring,
    start_monitoring,
    stop_monitoring,
    update_level_processor,
)
from .test_recording import (  # noqa: E402
    _cancel_test_locked,  # noqa: F401
    _do_auto_stop_test,  # noqa: F401
    _reset_test_chunks,  # noqa: F401
    _secure_clear_test_chunks,  # noqa: F401
    cancel_test_recording,
    is_test_active,
    read_test_recording_slice,
    start_test_recording,
    stop_test_recording,
    update_test_filters,
)
from .worker import (  # noqa: E402
    _ensure_level_worker_running,  # noqa: F401
    _level_worker_loop,  # noqa: F401
    _process_level_chunk,  # noqa: F401
    _stop_level_worker,  # noqa: F401
)

__all__ = [
    # Public monitoring API
    "is_monitoring",
    "get_level",
    "get_level_diagnostics",
    "update_level_processor",
    "start_monitoring",
    "stop_monitoring",
    # Public test-recording API
    "is_test_active",
    "start_test_recording",
    "stop_test_recording",
    "update_test_filters",
    "cancel_test_recording",
    "read_test_recording_slice",
    # Re-exported module proxies / loggers (backward-compat)
    "log",
    "np",
    "threading",
    "time",
    "collections",
    "contextlib",
    "io",
    "base64",
    "types",
    "logging",
    "Any",
]


def _reset_state_for_tests() -> None:
    """Test-only helper: reset all mutable state to post-``__init__`` defaults.

    Equivalent to the inline ``lm._test_mode = False; lm._test_chunks.clear();
    lm._monitor_active = False; ...`` blocks at the top of
    ``tests/test_level_monitor.py`` / ``tests/test_level_monitor_disconnect.py``
    / ``tests/test_mic_level_push_event.py``. Provided as a convenience —
    tests can call ``lm._reset_state_for_tests()`` for a one-line reset
    instead of enumerating every global.

    NOTE: this function does NOT stop the worker threads (call
    ``lm._stop_level_worker()`` and ``lm._stop_mic_level_worker()``
    separately if needed). It also does NOT close the PortAudio stream
    (call ``lm.stop_monitoring()`` for that).
    """
    _state.reset_for_tests()


# ─── Custom module class for mutable-state routing ──────────────────────
# TECH-DEBT (mirrors the pattern in recording/__init__.py).
# Tests access state via ``lm._test_mode`` (read) / ``lm._test_mode = False``
# (write) — i.e. via the package namespace, NOT via ``_state`` directly.
# Without this routing, those reads/writes would land on the package's
# own ``__dict__`` (a stale snapshot taken at import time), and the
# submodules that actually own + read the state (via ``_state._X``)
# would never see the test's write — the test would silently no-op.
#
# The custom ``_LevelMonitorModule`` class below installs ``__getattr__``
# and ``__setattr__`` overrides that route ``_``-prefixed attribute
# access through to the singleton ``_state`` instance in :mod:`._state`.
# Reads of non-routed names fall back to the normal ``AttributeError``
# (which Python resolves via the package's ``__dict__`` before
# ``__getattr__`` is even called).
import sys  # noqa: E402


class _LevelMonitorModule(sys.modules[__name__].__class__):
    """Module subclass routing mutable-state reads/writes to ``_state``.

    See the comment block above for the rationale.  The class is
    installed as the package's ``__class__`` at the bottom of this file
    via ``sys.modules[__name__].__class__ = _LevelMonitorModule``.

    Routing rule: any attribute name starting with ``_`` that exists on
    ``_state`` is routed. Non-``_``-prefixed names (e.g. ``np``,
    ``log``, ``time``) and ``_``-prefixed names NOT on ``_state``
    (e.g. ``__name__``, ``__file__``, ``_LevelMonitorModule`` itself)
    fall back to normal module attribute lookup.
    """

    def __getattr__(self, name: str) -> object:
        # ``__getattr__`` is only called when normal attribute lookup
        # (via ``__dict__``) fails — so this is a fallback for names
        # NOT bound at import time.  All ``_``-prefixed mutable state
        # lives on ``_state`` and is deliberately NOT imported into the
        # package's ``__dict__`` (see above), so reads route through
        # here.
        if name.startswith("_"):
            try:
                return getattr(_state, name)
            except AttributeError:
                raise AttributeError(
                    f"module {__name__!r} has no attribute {name!r}",
                ) from None
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: object) -> None:
        # Route writes for ``_``-prefixed names that exist on ``_state``.
        # This propagates test writes (``lm._test_mode = False``) to the
        # singleton so production code reading ``_state._test_mode`` sees
        # the new value.
        if name.startswith("_") and hasattr(_state, name):
            setattr(_state, name, value)
            return
        # Default: set on the package's __dict__ (normal module behaviour).
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _LevelMonitorModule
