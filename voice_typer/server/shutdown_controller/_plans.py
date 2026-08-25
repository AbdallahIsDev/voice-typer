"""SequencingMixin — plan construction + plan runner for ``ShutdownController``.

Split verbatim out of the pre-split ``shutdown_controller`` module.
Holds the sequenced/parallel ``ShutdownPlan`` builders, the thin
``_run_plan`` delegate onto :func:`voice_typer.server.shutdown.plan.run_plan`,
and the late ``tray.stop`` bookend.
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
    ShutdownStep,
    run_plan,
)

from ._deadline import _shutdown_deadline_near, _shutdown_remaining

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

        Extracted from ``_do_cleanup``. The sequenced phase
        runs the dependent teardowns IN ORDER, each wrapped in
        ``_run_with_timeout`` so a stuck helper doesn't block the rest
        of cleanup:

          1. ``_teardown_timers_and_recording`` — cancel timers, pop
              the streaming session, signal cancel. SKIPPED when the
              deadline is near (non-critical).
          2. ``_teardown_recorder`` — ``recorder.stop()`` + join the
              transcription thread (3s timeout). Sets
              ``_recorder_teardown_done`` so the downstream
              ``_teardown_sounddevice`` (in the parallel batch) gets a
              happens-before guarantee on ``_recorder_force_closed``.
          3. ``_teardown_history_db`` — ``flush()`` + ``close()`` to
              drain pending writes (including the one the transcription
              thread just enqueued).
          4. ``_teardown_crash_recovery`` — ``flush()`` + ``shutdown()``
              to drain pending crash-recovery snapshots.

        ``_teardown_asr_models`` stays in the parallel batch (built by
        ``_build_parallel_plan``): the sequenced phase completes BEFORE
        the parallel batch starts, so the transcription thread is
        already joined by the time the ASR model is unloaded.

        The list-of-tuples form (rather than direct ``ShutdownStep``
        construction) is kept so source-text contract tests
        (``tests/test_shutdown_fast_path.py::TestSequentialHistoryAndCrashRecovery``
        and ``tests/test_shutdown_asr_unload.py::TestTeardownAsrModelsContract``)
        continue to find the sequenced / parallel symbols + the
        ``("teardown_<name>",`` entry pattern.

        Parameters
        ----------
        deadline:
            The overall 20s shutdown deadline (``time.monotonic() +
            20.0``), used to decide whether to skip the non-critical
            ``teardown_timers_and_recording`` step.
        skipped:
            Mutable list of skipped step names; appended to in place so
            ``_do_cleanup`` can emit a single summary WARNING at the end.
        """
        # Overall-deadline skip: when the 20s deadline is near (< 5s
        # remaining) at the start of the sequenced phase,
        # ``teardown_timers_and_recording`` is SKIPPED (non-critical).
        # ``teardown_recorder``, ``teardown_history_db``, and
        # ``teardown_crash_recovery`` ALWAYS run — they contain critical
        # flushes.
        sequenced_items: list[tuple[str, object, float, str | None, bool]] = []
        # SESSION-STATE: clear the session-active marker FIRST so a kill
        # later in teardown (watchdog ``os._exit(0)``, SIGKILL fallback)
        # still counts as a clean shutdown. Cheap + idempotent; always
        # runs regardless of deadline pressure.
        sequenced_items.append(
            ("teardown_session_marker", self._teardown_session_marker, 5.0, None, False),
        )
        if _shutdown_deadline_near(deadline):
            log.warning(
                "[SHUTDOWN] deadline near (%.1fs remaining) at sequenced "
                "phase entry — skipping teardown_timers_and_recording (non-critical)",
                _shutdown_remaining(deadline),
            )
            skipped.append("teardown_timers_and_recording")
        else:
            sequenced_items.append(
                ("teardown_timers_and_recording", self._teardown_timers_and_recording, 10.0, None, False),
            )
        sequenced_items.append(
            ("teardown_recorder", self._teardown_recorder, 15.0, None, False),
        )
        sequenced_items.append(
            ("teardown_history_db", self._teardown_history_db, 15.0, None, False),
        )
        sequenced_items.append(
            ("teardown_crash_recovery", self._teardown_crash_recovery, 10.0, None, False),
        )
        sequenced_plan = ShutdownPlan(
            phase="sequenced",
            steps=tuple(ShutdownStep(*item) for item in sequenced_items),
        )
        return sequenced_plan

    # ─── Parallel plan builder — ─────────────────────

    def _build_parallel_plan(
        self,
        deadline: float,
        timed_out: frozenset[str],
        skipped: list[str],
    ) -> ShutdownPlan | None:
        """Build the parallel-batch plan, applying deadline-near skips.

        Extracted from ``_do_cleanup``. Each helper is
        isolated — a failure in one does NOT propagate
        (``_run_parallel_with_timeout`` captures per-call exceptions).
        Shared 10s deadline: each helper is wrapped in
        ``_run_with_timeout(..., timeout=10.0)`` by
        ``_run_parallel_with_timeout``; if a helper exceeds 10s, the
        worker thread is leaked as a daemon and the orchestrator moves
        on.

        ``_teardown_asr_models`` is placed FIRST in the parallel batch
        so the (potentially slow) CUDA context teardown starts as
        early as possible. It runs AFTER the sequenced critical phase
        (which joins the transcription thread), so the ASR model is
        only unloaded once the thread's inference has completed — no
        race between ``registry.unload()`` and mid-inference torch
        state.

        Barrier: ``teardown_sounddevice`` declares
        ``depends_on="teardown_recorder"`` + ``skip_if_dep_timed_out=
        True``. When the recorder's PortAudio stream failed to close
        in time, the leaked worker is still accessing the stream and
        a concurrent ``sd.stop()`` can deadlock on WASAPI backends
        (stream lock held). The ``_run_plan`` driver skips the step
        when the dependency is in ``timed_out``.

        Overall-deadline skip: when the 20s deadline is near (< 5s
        remaining), skip NON-CRITICAL parallel helpers. The critical
        set is ``{teardown_pid_file, teardown_mutex_handle}`` — they
        release the single-instance PID file + mutex so the next
        launch isn't blocked. Everything else is non-critical under a
        tight deadline — the OS will reap those resources at process
        exit.

        Parameters
        ----------
        deadline:
            The overall 20s shutdown deadline, used to decide which
            non-critical helpers to skip.
        timed_out:
            Step names that timed out in the sequenced plan (used by
            ``_run_plan`` for the barrier — NOT used directly here but
            threaded through for the subsequent ``_run_plan`` call).
        skipped:
            Mutable list of skipped step names; appended to in place.

        Returns
        -------
        ShutdownPlan | None
            The parallel plan, or ``None`` if every helper was skipped
            (defensive — the critical set ensures at least 2 items
            always run, so ``None`` is never returned in practice).
        """
        _shutdown_critical_parallel: frozenset[str] = frozenset({"teardown_pid_file", "teardown_mutex_handle"})
        all_parallel_items: list[tuple[str, object, float, str | None, bool]] = [
            ("teardown_asr_models", self._teardown_asr_models, 10.0, None, False),
            ("teardown_restore_volume", self._teardown_restore_volume, 10.0, None, False),
            ("teardown_waveform_wiring", self._teardown_waveform_wiring, 10.0, None, False),
            ("teardown_sounddevice", self._teardown_sounddevice, 10.0, "teardown_recorder", True),
            ("teardown_pid_file", self._teardown_pid_file, 10.0, None, False),
            ("teardown_mutex_handle", self._teardown_mutex_handle, 10.0, None, False),
            ("teardown_devnull_files", self._teardown_devnull_files, 10.0, None, False),
            ("teardown_level_monitor", self._teardown_level_monitor, 10.0, None, False),
            ("teardown_hotkeys", self._teardown_hotkeys, 10.0, None, False),
            ("teardown_electron", self._teardown_electron, 10.0, None, False),
            ("teardown_event_bus", self._teardown_event_bus, 10.0, None, False),
        ]
        parallel_items: list[tuple[str, object, float, str | None, bool]] = []
        for _desc, _func, _timeout, _dep, _skip in all_parallel_items:
            if _shutdown_deadline_near(deadline) and _desc not in _shutdown_critical_parallel:
                log.warning(
                    "[SHUTDOWN] deadline near (%.1fs remaining) — skipping non-critical %s",
                    _shutdown_remaining(deadline),
                    _desc,
                )
                skipped.append(_desc)
                continue
            parallel_items.append((_desc, _func, _timeout, _dep, _skip))
        # Guard against empty parallel_items (defensive — critical set
        # ensures at least 2 items always run).
        if not parallel_items:
            return None
        parallel_plan = ShutdownPlan(
            phase="parallel",
            steps=tuple(ShutdownStep(*item) for item in parallel_items),
        )
        return parallel_plan

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
