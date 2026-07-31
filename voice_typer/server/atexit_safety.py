"""atexit safety net ( extraction).

Extracted out of :mod:`voice_typer.server.shutdown_controller` so the
shutdown controller can focus on cleanup orchestration. The two
functions here are the atexit safety net that runs ``_do_cleanup`` if
the process is killed externally (console close, task manager, OOM
kill, etc.) without ``quit()`` / ``restart_app()`` having run.

* :func:`atexit_log` — logs a warning when the process exits without
  ``quit()`` having been called (i.e. ``_shutting_down_event`` is not
  set), so operators can see "the process likely died externally"
  in the log rather than a clean "[SHUTDOWN] Shutting down" line.
* :func:`atexit_cleanup` — runs the shared ``_do_cleanup`` body when
  the process exits without ``quit()`` / ``restart_app()`` having run.
  Idempotent via the ``_shutting_down`` short-circuit and the
  ``_cleanup_done`` flag inside ``_do_cleanup`` itself; never raises
().

Each function takes the owning :class:`ShutdownController` instance as
its ``controller`` argument so it can read ``controller._app._shutting_down``
/ ``_shutting_down_event`` and delegate to ``controller._app._do_cleanup()``
(the delegate on :class:`VoiceTyperApp`) — preserving the existing
test-spy contract (``monkeypatch.setattr(app, "_do_cleanup", spy)``
still intercepts the call from the atexit path; see
``tests/test_app_cleanup.py::TestAtexitCleanupSafetyNet``).

:meth:`ShutdownController._atexit_log` /
:meth:`ShutdownController._atexit_cleanup` become thin delegates that
call these module functions, preserving the existing instance-method
API used by tests (``controller._atexit_cleanup()``) and the
``VoiceTyperApp`` wiring (``app.start()`` registers
``atexit.register(self._atexit_log)``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.shutdown_controller import ShutdownController

log = logging.getLogger(__name__)


def atexit_log(controller: ShutdownController) -> None:
    """Log when the process exits, even if quit() was not called."""
    app = controller._app
    if not app._shutting_down_event.is_set():
        log.warning(
            "[ATEXIT] Process exiting without quit() -- likely killed externally (console close, task manager, etc.)"
        )


def atexit_cleanup(controller: ShutdownController) -> None:
    """RACE-016: atexit handler for critical cleanup paths.

        Daemon threads can be killed by the interpreter without running
        their finally blocks.  This method is a safety net that ensures
        critical cleanup (volume restore, hotkey release, crash recovery
        flush, history DB flush, recorder stop, PID file + mutex
        release) happens even if the daemon thread's finally block
        didn't run.  It is idempotent — calling it after ``quit()`` or
        ``restart_app()`` is a no-op because both set
        ``_shutting_down = True`` before delegating to ``_do_cleanup()``,
        and ``_do_cleanup()`` itself guards against double-execution
        via the ``_cleanup_done`` flag.

    previously this method ran an ad-hoc subset of cleanup
        (volume restore + hotkey stop + crash recovery flush) that
        DIVERGED from ``quit()``'s path.  When the process was killed
        externally (no ``quit()`` / ``restart_app()``), the safety net
        skipped history DB flush, recorder stop, mic watcher shutdown,
        bubble level worker stop, PID file clear, and mutex handle
        close — leaking the same resources that the OLD
        ``restart_app()`` leaked.  It now delegates to
        ``_do_cleanup()`` so the safety net runs the SAME audited
        shutdown path as the regular flow.
    """
    app = controller._app
    try:
        if app._shutting_down:
            # quit() or restart_app() already ran (or is running)
            # _do_cleanup(); the _cleanup_done flag inside
            # _do_cleanup() makes a second call a no-op, but we
            # short-circuit here too to avoid the spurious
            # "[ATEXIT] Running emergency cleanup" log line on
            # every intentional shutdown.
            return
        log.info("[ATEXIT] Running emergency cleanup")
        # NOTE: we call ``app._do_cleanup()`` (the delegate on
        # VoiceTyperApp) rather than ``controller._do_cleanup()`` (the
        # body on this controller) so test spies that
        # ``monkeypatch.setattr(app, "_do_cleanup", spy)`` still
        # intercept the call — see
        # tests/test_app_cleanup.py::test_atexit_cleanup_never_raises.
        app._do_cleanup()
    except Exception:
        # previously this was a bare ``except Exception: pass``
        # which silently swallowed cleanup failures and left no trace
        # in the log — making post-mortem debugging of crash-loop
        # exits effectively impossible. We still never re-raise out
        # of an atexit handler (that would mask the original exit
        # cause and produce confusing tracebacks), but we now log
        # the exception with traceback so operators can see what
        # broke in the emergency cleanup path.
        log.exception("[ATEXIT] _do_cleanup() raised — emergency cleanup incomplete")


def register_atexit_hooks(controller: ShutdownController) -> None:
    """Register the atexit safety-net hooks for this controller.

    Convenience wrapper: registers both :func:`atexit_log` and
    :func:`atexit_cleanup` (in that order) with the :mod:`atexit`
    module so they fire at interpreter shutdown. Mirrors what
    ``VoiceTyperApp.start()`` previously did inline via
    ``atexit.register(self._atexit_log)`` / ``atexit.register(self._atexit_cleanup)``.
    """
    import atexit

    atexit.register(atexit_log, controller)
    atexit.register(atexit_cleanup, controller)


__all__ = [
    "atexit_log",
    "atexit_cleanup",
    "register_atexit_hooks",
]
