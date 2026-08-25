"""god-class decomposition: ShutdownController — extracted from VoiceTyperApp.

Owns the entire shutdown / cleanup lifecycle of ``VoiceTyperApp``:

    - ``_do_cleanup`` — the shared, idempotent cleanup body invoked by
      ``quit()``, ``restart_app()``, and ``_atexit_cleanup()``. 30+
      try/except blocks release every subsystem (recorder, hotkeys,
      history DB, crash recovery, bubble level worker, Win32 mutex,
      Electron subprocess, devnull FDs, etc.).
    - ``quit`` — sets ``_shutting_down``, calls
      ``thread_registry.shutdown_all()``, delegates to ``_do_cleanup``,
      then ``sys.exit(0)`` (only when called from the main thread).
    - ``_atexit_log`` / ``_atexit_cleanup`` — atexit safety net that
      runs ``_do_cleanup`` if the process is killed externally without
      ``quit()`` / ``restart_app()`` having run.
    - ``_install_signal_handlers`` — POSIX SIGINT/SIGTERM handlers that
      trigger ``quit()`` on a separate thread.
    - ``_install_win32_console_handler`` / ``_win32_console_handler`` —
      Windows console control handler that keeps the tray app alive
      when the console window closes, and triggers ``quit()`` on
      Ctrl+C / logoff / shutdown.

Previously all of this lived on ``VoiceTyperApp`` as ~480 LOC across 7
methods. The behaviour is preserved verbatim — only the class boundary
moved. ``VoiceTyperApp`` keeps thin delegate methods (``app.quit()``,
``app._do_cleanup()``, ``app._atexit_cleanup()``, etc.) for back-compat
with callers (``app.start()`` registers the atexit handlers, tray menu
callbacks invoke ``quit_app`` which calls ``quit``, tests call
``app._do_cleanup()`` directly) and — crucially — so test spies that
``monkeypatch.setattr(app, "_do_cleanup", spy)`` still intercept the
cleanup call from ``quit`` / ``restart_app`` / ``_atexit_cleanup``.

A note on monkeypatching (mirrors the convention in
``settings_controller.py`` and ``startup_tasks.py``): tests like the
``app`` fixture in ``tests/test_app.py`` and the regression tests in
``tests/test_app_cleanup.py`` patch
``voice_typer.server.app._clear_backend_pid_file`` (and
``voice_typer.server.platform_utils.is_windows`` in other suites) at call time.
To keep those patches effective, the helpers are looked up DYNAMICALLY
from the ``voice_typer.server.app`` module inside each method rather
than being captured at import time.

A note on the delegate indirection for ``_do_cleanup``: ``quit`` and
``_atexit_cleanup`` deliberately call ``self._app._do_cleanup()``
(the delegate on ``VoiceTyperApp``) rather than ``self._do_cleanup()``
(the body on ``ShutdownController`` itself). This is so test spies
that ``monkeypatch.setattr(app, "_do_cleanup", spy)`` still intercept
the call — see ``tests/test_app_cleanup.py::TestQuitAppUsesSharedCleanup::
test_quit_calls_do_cleanup`` and
``TestAtexitCleanupSafetyNet::test_atexit_cleanup_never_raises``.
``restart_app`` (which stays on ``VoiceTyperApp``) also calls
``self._do_cleanup()`` (the delegate) for the same reason.

Package layout (this module is the compatibility facade):

- :mod:`._deadline`          — module-level deadline-budget helpers.
- :mod:`._plans`             — ``SequencingMixin`` (thin plan-builder
                              delegates; bodies in ``shutdown/plan.py``).
- :mod:`._cleanup`           — ``CleanupMixin`` (thin delegates; bodies
                              in ``shutdown/cleanup.py`` +
                              ``shutdown/ws_drain.py``).
- :mod:`._teardowns`         — ``TeardownsMixin`` (thin teardown delegates).
- :mod:`._lifecycle_signals` — ``SignalsMixin`` (quit / watchdog /
                              atexit / signal-handler delegates).
- :mod:`.controller`         — the ``ShutdownController`` class itself.

The stdlib modules below are re-imported here (with ``F401``) because
tests reach through this namespace to patch them — e.g.
``monkeypatch.setattr("voice_typer.server.shutdown_controller.os._exit",
...)`` resolves ``os`` on THIS module object before setting ``_exit``
on the (shared, singleton) stdlib module.
"""

from __future__ import annotations

import contextlib  # noqa: F401  # re-exported / patch surface parity with the pre-split module
import logging
import os  # noqa: F401  # tests do setattr("...shutdown_controller.os._exit", ...)
import sys  # noqa: F401  # re-exported / monkeypatch target for tests
import threading  # noqa: F401  # re-exported / patch surface parity
import time  # noqa: F401  # tests do monkeypatch.setattr(_sc.time, "monotonic"/"sleep", ...)

# Single source of truth for the declarative shutdown plan + driver lives
# in :mod:`voice_typer.server.shutdown.plan` (extracted out of this module
# to keep the controller wiring-focused). Re-imported here so existing
# callers — tests do
# ``from voice_typer.server.shutdown_controller import ShutdownPlan,
# ShutdownStep`` — keep resolving, and so the dataclass constructors used
# in ``_do_cleanup`` below remain in scope without duplication.
from voice_typer.server._timeout_utils import (  # noqa: F401  # SHUTDOWN_WATCHDOG_TIMEOUT_S + join_leaked_workers re-exported for tests
    SHUTDOWN_WATCHDOG_TIMEOUT_S,
    TIMEOUT,
    _run_parallel_with_timeout,
    _run_with_timeout,
    join_leaked_workers,
)
from voice_typer.server.duration import format_duration  # noqa: F401  # re-exported for callers
from voice_typer.server.platform_utils import is_windows  # noqa: F401  # re-exported for callers
from voice_typer.server.shutdown.plan import (  # noqa: F401  # re-exported for tests + call sites
    ShutdownPlan,
    ShutdownStep,
    run_plan,
)

log = logging.getLogger(__name__)

# ─── Leaf imports (facade re-exports) ───────────────────────────────
#
# Import order matters: the mixins must be imported BEFORE
# ``.controller`` so the class definition can resolve its bases.
from ._cleanup import CleanupMixin  # noqa: E402,F401
from ._deadline import (  # noqa: E402,F401  # re-exported for tests that import the helpers from this package
    _shutdown_deadline_near,
    _shutdown_remaining,
)
from ._lifecycle_signals import SignalsMixin  # noqa: E402,F401
from ._plans import SequencingMixin  # noqa: E402,F401
from ._teardowns import TeardownsMixin  # noqa: E402,F401
from .controller import ShutdownController  # noqa: E402,F401

__all__ = [
    "SHUTDOWN_WATCHDOG_TIMEOUT_S",
    "TIMEOUT",
    "CleanupMixin",
    "SequencingMixin",
    "SignalsMixin",
    "ShutdownController",
    "ShutdownPlan",
    "ShutdownStep",
    "TeardownsMixin",
    "_shutdown_deadline_near",
    "_shutdown_remaining",
    "format_duration",
    "is_windows",
    "join_leaked_workers",
    "run_plan",
]
