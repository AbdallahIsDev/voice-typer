"""SequencingMixin — plan construction + plan runner for ``ShutdownController``.

The plan-builder bodies live in :mod:`voice_typer.server.shutdown.plan`
(:func:`build_sequenced_plan` / :func:`build_parallel_plan`, beside the
``ShutdownStep`` / ``ShutdownPlan`` dataclasses and the :func:`run_plan`
driver); this module keeps the thin ``_build_sequenced_plan`` /
``_build_parallel_plan`` / ``_run_plan`` delegates on the mixin (the
delegates are load-bearing test surface — tests monkeypatch or spy on
the controller methods by name, and ``do_cleanup`` resolves the
``_teardown_*`` callables through the controller instance so per-name
patches keep taking effect).

The late ``tray.stop`` bookend (``_late_bookend_tray_stop``) stays here:
it is a small, self-contained body whose ``_run_with_timeout`` binding
is patched by name on THIS module
(``monkeypatch.setattr(_sc_plans, "_run_with_timeout", ...)`` in
``tests/test_shutdown_parallel.py``).
"""

from __future__ import annotations

import logging
import os
import threading

from voice_typer.server._timeout_utils import (
    TIMEOUT,
    _run_with_timeout,
)
from voice_typer.server.shutdown.plan import (
    ShutdownPlan,
    build_parallel_plan,
    build_sequenced_plan,
    run_plan,
)

log = logging.getLogger("voice_typer.server.shutdown_controller")


class SequencingMixin:
    """Plan-building + plan-running mixin for :class:`ShutdownController`."""

    # ─── Sequenced plan builder — ────────────────────

    def _build_sequenced_plan(
        self,
        deadline: float,
        skipped: list[str],
    ) -> ShutdownPlan:
        """Build the sequenced critical-teardown plan.

        Body lives in
        :func:`voice_typer.server.shutdown.plan.build_sequenced_plan`.
        This delegate preserves the instance-method API used by
        ``do_cleanup`` and by tests that monkeypatch or spy on the
        builder by name.
        """
        return build_sequenced_plan(self, deadline, skipped)

    # ─── Parallel plan builder — ─────────────────────

    def _build_parallel_plan(
        self,
        deadline: float,
        timed_out: frozenset[str],
        skipped: list[str],
    ) -> ShutdownPlan | None:
        """Build the parallel-batch plan, applying deadline-near skips.

        Body lives in
        :func:`voice_typer.server.shutdown.plan.build_parallel_plan`.
        This delegate preserves the instance-method API used by
        ``do_cleanup`` and by tests that monkeypatch or spy on the
        builder by name.
        """
        return build_parallel_plan(self, deadline, timed_out, skipped)

    # ─── Late bookend helper — ───────────────────────

    def _late_bookend_tray_stop(self, app) -> None:
        """Late bookend: ``tray.stop()`` — MUST be the LAST step in cleanup.

        Extracted from ``_do_cleanup``. ``tray.stop()`` MUST
        be the LAST step. Previously it was step 13 of 19, which broke
        the pystray loop on the main thread (blocked in ``tray.run()``
        via ``ipc_server.main()``) before the remaining cleanups could
        finish. Moving ``tray.stop()`` to the end ensures the main
        thread stays alive (blocked in ``tray.run()``) until every
        other cleanup has completed. Idempotent — wrapped in
        try-except so a second call after the tray is already stopped
        doesn't propagate. 5s timeout.

        If ``tray.stop()`` times out AND we're on a non-main thread,
        call ``os._exit(0)`` immediately. The main thread is parked in
        pystray's ``tray.run()`` event loop and relies on
        ``tray.stop()`` breaking that loop to return. If
        ``tray.stop()`` hangs, the main thread never returns and the
        process is unkillable via the normal path — ``sys.exit(0)`` in
        ``quit()`` only raises ``SystemExit`` in THIS worker thread.
        ``os._exit(0)`` bypasses Python's orderly shutdown but is safe
        here because every other subsystem has already been torn down
        by the cleanup steps above. On the main thread, we just log
        and continue — ``quit()``'s ``sys.exit(0)`` will handle exit.

        When ``tray.stop()`` RAISES (not times out), the failure is
        logged at ERROR (was DEBUG pre-fix) so operators can see why
        the main thread stayed parked in ``tray.run()``.
        """
        try:
            _tray_stop_result = _run_with_timeout(
                "tray.stop",
                app.tray.stop,
                timeout=5.0,
            )
            if _tray_stop_result is TIMEOUT and (threading.current_thread() is not threading.main_thread()):
                log.warning(
                    "[SHUTDOWN] tray.stop() timed out on non-main thread "
                    "— calling os._exit(0) to unblock the main thread parked in "
                    "tray.run() (all subsystem cleanup already completed)"
                )
                os._exit(0)
        except Exception:
            log.error("[CLEANUP] tray.stop() failed", exc_info=True)

    def _run_plan(
        self,
        plan: ShutdownPlan,
        prior_timed_out: frozenset[str],
    ) -> frozenset[str]:
        """Execute a :class:`ShutdownPlan` and return the set of step
        names that timed out.

        Thin delegate to :func:`voice_typer.server.shutdown.plan.run_plan`.
        The driver body (per-step timeout wrapping, pre-flight barrier
        skip, degraded-step summary) is owned by the extracted
        :mod:`shutdown.plan` module so it can be unit-tested in
        isolation — see ``tests/test_shutdown_plan_zr17.py``. The
        delegate keeps this method on ``ShutdownController`` so the
        existing ``self._run_plan(plan, prior_timed_out)`` call sites
        in ``_do_cleanup`` (and the source-inspection tests in
        ``tests/test_shutdown_recording_fixes.py``) continue to work
        unchanged.

        The extracted driver performs a lazy import of
        ``_run_with_timeout`` / ``_run_parallel_with_timeout`` from
        the ``voice_typer.server.shutdown_controller`` package at call
        time, so tests that monkeypatch
        ``shutdown_controller._run_with_timeout`` (or
        ``shutdown_controller._run_parallel_with_timeout``) still
        take effect through the delegate.
        """
        return run_plan(self, plan, prior_timed_out)
