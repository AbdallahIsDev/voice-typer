"""CleanupMixin — thin cleanup delegates on ``ShutdownController``.

Each helper is a thin delegate that calls the standalone function in
:mod:`voice_typer.server.shutdown.cleanup` /
:mod:`voice_typer.server.shutdown.ws_drain` (extracted so the
controller class body shrinks to orchestration only — same convention
as :mod:`._teardowns`). The delegate indirection is kept so:

  * tests that ``monkeypatch.setattr(controller, "_do_cleanup", spy)``
    (or spy on the app-side ``app._do_cleanup`` delegate — see the
    package ``__init__.py`` notes) still intercept the call;
  * the sequenced / parallel plan construction inside ``do_cleanup``
    keeps calling ``controller._drain_ws_dispatch_pool`` /
    ``controller._build_sequenced_plan`` / ``controller._run_plan`` /
    ``controller._build_parallel_plan`` /
    ``controller._late_bookend_tray_stop`` through the INSTANCE, so
    per-name monkeypatch seams keep working;
  * ``signal_handlers.win32_console_handler`` keeps routing Windows
    logoff/shutdown events to ``controller._do_fast_cleanup()``.

The free functions are imported at MODULE level (not inside the
methods) — the static AST contract in
``tests/regressions/test_electron.py::TestShutdownControllerPhasesContract``
asserts ``_do_cleanup`` contains ZERO dynamic imports, and the import
is acyclic in both directions (the extracted modules depend only on
stdlib + leaf utility modules, never on ``shutdown_controller`` at
module load time).
"""

from __future__ import annotations

from voice_typer.server.shutdown.cleanup import do_cleanup, do_fast_cleanup
from voice_typer.server.shutdown.ws_drain import drain_ws_dispatch_pool


class CleanupMixin:
    """Thin cleanup-delegate mixin for :class:`ShutdownController`."""

    # ─── Shared cleanup body ───────────────────────────────────────────

    def _do_cleanup(self) -> None:
        """shared cleanup body used by ``quit()``, ``restart_app()``,
        and ``_atexit_cleanup()``.

        Body lives in :func:`voice_typer.server.shutdown.cleanup.do_cleanup`.
        This delegate preserves the instance-method API used by callers
        (``quit()`` / ``_atexit_cleanup()`` route through
        ``app._do_cleanup()``) and by test spies.
        """
        do_cleanup(self)

    # ─── Early bookend helper — ──────────────────────

    def _drain_ws_dispatch_pool(self, app) -> None:
        """Early bookend: stop the IPC server + drain the WS dispatch pool.

        Body lives in
        :func:`voice_typer.server.shutdown.ws_drain.drain_ws_dispatch_pool`
        (preserves the exact WS-pool drain logic including the
        ``if join_thread.is_alive():`` timeout branch).
        """
        drain_ws_dispatch_pool(self, app)

    def _do_fast_cleanup(self) -> None:
        """critical-only cleanup for Windows logoff/shutdown.

        Body lives in
        :func:`voice_typer.server.shutdown.cleanup.do_fast_cleanup`
        (ends with ``os._exit(0)`` — tests that invoke this method
        directly MUST monkey-patch ``os._exit``).
        """
        do_fast_cleanup(self)
