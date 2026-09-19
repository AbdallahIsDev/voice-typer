"""SequencingMixin, plan construction + plan runner for ``ShutdownController``."""

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

    def _build_sequenced_plan(
        self,
        deadline: float,
        skipped: list[str],
    ) -> ShutdownPlan:
        """Build the sequenced critical-teardown plan."""
        return build_sequenced_plan(self, deadline, skipped)

    def _build_parallel_plan(
        self,
        deadline: float,
        timed_out: frozenset[str],
        skipped: list[str],
    ) -> ShutdownPlan | None:
        """Build the parallel-batch plan, applying deadline-near skips."""
        return build_parallel_plan(self, deadline, timed_out, skipped)

    def _late_bookend_tray_stop(self, app) -> None:
        """Late bookend: ``tray.stop()``: MUST be the LAST step in cleanup."""
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
        """Execute a :class:`ShutdownPlan` and return the set of step"""
        return run_plan(self, plan, prior_timed_out)
