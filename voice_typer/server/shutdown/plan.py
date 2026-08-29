"""Declarative shutdown plan + driver (extracted from ``shutdown_controller``).

Houses the :class:`ShutdownStep` / :class:`ShutdownPlan` dataclasses, the
:func:`run_plan` driver, and the plan-builder functions
(:func:`build_sequenced_plan` / :func:`build_parallel_plan`) that used to
live inline on :class:`voice_typer.server.shutdown_controller.ShutdownController`.

The dataclasses express the teardown ordering contract — sequenced critical
flushes (recorder → history_db → crash_recovery) run before the parallel
batch of independent subsystem teardowns. The driver wraps each step in a
per-step timeout (via :func:`voice_typer.server._timeout_utils._run_with_timeout`
or :func:`_run_parallel_with_timeout`) and applies the cross-step barrier
(``skip_if_dep_timed_out``) so a downstream call that touches the same OS
resource as a leaked upstream worker is skipped (notably ``sd.stop()`` after
a timed-out ``recorder.stop()`` on WASAPI PortAudio backends).

The controller keeps thin ``_run_plan`` / ``_build_sequenced_plan`` /
``_build_parallel_plan`` delegates so existing call sites
(``controller._run_plan(plan, prior_timed_out)`` etc.) and tests
(``tests/test_shutdown_plan_zr17.py``) continue to work unchanged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from voice_typer.server._timeout_utils import TIMEOUT

if TYPE_CHECKING:
    from voice_typer.server.shutdown_controller import ShutdownController

log = logging.getLogger(__name__)

# Logger binding for the plan-builder bodies extracted from
# ``shutdown_controller/_plans.py``: those bodies emitted their deadline-skip
# warnings on the ``voice_typer.server.shutdown_controller`` logger, and
# tests filter caplog records by that logger name
# (tests/test_shutdown_deadline.py) — keep the record logger name identical.
_sc_log = logging.getLogger("voice_typer.server.shutdown_controller")

# Sequenced steps that contain data-loss-critical flushes. These MUST
# run even when the 20s shutdown deadline is nearly exhausted, so the
# inter-step deadline check in :func:`run_plan` exempts them from the
# skip-non-critical-step branch. ``teardown_recorder`` joins the
# transcription thread (whose final ``add_transcription()`` write must
# reach the history DB); ``teardown_history_db`` flushes + closes the
# DB; ``teardown_crash_recovery`` flushes + shuts down the crash-
# recovery writer. Skipping any of these under a tight deadline would
# silently lose user data.
CRITICAL_STEPS: frozenset[str] = frozenset(
    {
        "teardown_recorder",
        "teardown_history_db",
        "teardown_crash_recovery",
    }
)
"""Sequenced shutdown steps that MUST run regardless of deadline pressure.

The inter-step deadline check in :func:`run_plan` skips non-critical
steps when the remaining shutdown budget drops below 5s, but steps in
this set are always executed because they contain data-loss-critical
flushes.
"""

# The threshold (in seconds) below which the inter-step deadline check
# starts skipping non-critical steps. Mirrors the
# ``_shutdown_deadline_near`` helper in ``shutdown_controller``.
_DEADLINE_NEAR_THRESHOLD: float = 5.0


@dataclass(frozen=True)
class ShutdownStep:
    """One declarative teardown step.

    Parameters
    ----------
    name:
        Unique identifier within the owning :class:`ShutdownPlan`. Used as
        the ``depends_on`` target by other steps.
    func:
        The callable to invoke (typically a bound ``_teardown_*`` method on
        :class:`ShutdownController`).
    timeout:
        Per-step hard timeout in seconds. When the step does not finish in
        time, ``_run_with_timeout`` returns :data:`TIMEOUT` and the worker
        thread is leaked as a daemon (registered for best-effort join via
        ``join_leaked_workers``).
    depends_on:
        Name of another step in the SAME plan or in a previously-run plan
        whose completion this step logically depends on. Used together with
        ``skip_if_dep_timed_out`` to express the barrier pattern. ``None``
        (default) means no dependency.
    skip_if_dep_timed_out:
        When True, :func:`run_plan` skips this step if the named
        ``depends_on`` step timed out. This is the barrier: a downstream
        call that touches the same OS resource as an upstream call (e.g.
        ``sd.stop()`` after a leaked ``recorder.stop()``) MUST be skipped
        because the upstream worker is still accessing the resource and a
        concurrent downstream call can deadlock (notably on WASAPI
        PortAudio backends where the stream lock is held).
    """

    name: str
    func: Callable[[], object]
    timeout: float
    depends_on: str | None = None
    skip_if_dep_timed_out: bool = False


@dataclass(frozen=True)
class ShutdownPlan:
    """An ordered collection of :class:`ShutdownStep` instances.

    The ``phase`` field selects the execution strategy:

    * ``"sequenced"`` — steps run one at a time, each wrapped in
      :func:`_run_with_timeout`. A slow step does not block subsequent
      steps past its own timeout.
    * ``"parallel"`` — steps run concurrently via
      :func:`_run_parallel_with_timeout` (a bounded ``ThreadPoolExecutor``
      with max_workers=8). Used for teardowns that touch disjoint resources
      and can race safely.

    The :func:`run_plan` driver returns the set of step names that timed
    out (so a subsequent plan can apply ``skip_if_dep_timed_out`` barriers
    against them).
    """

    phase: Literal["sequenced", "parallel"]
    steps: tuple[ShutdownStep, ...] = field(default_factory=tuple)


def run_plan(
    controller: ShutdownController,
    plan: ShutdownPlan,
    prior_timed_out: frozenset[str],
) -> frozenset[str]:
    """Execute a :class:`ShutdownPlan` and return the set of step names
    that timed out.

    Behaviour preserved verbatim from the original
    :meth:`ShutdownController._run_plan` body:

    * Sequenced phase: each step is wrapped in ``_run_with_timeout``;
      per-step failures (BaseException) are logged at DEBUG and captured
      into the ``degraded`` list; TIMEOUT results are logged at WARNING.
      A summary WARNING fires after the loop if any step degraded.
    * Parallel phase: steps are handed to ``_run_parallel_with_timeout``
      (bounded ThreadPoolExecutor, max_workers=8); per-step results are
      inspected for BaseException (logged at DEBUG) or TIMEOUT (logged at
      WARNING); a summary WARNING fires if any step degraded.

    Barrier: for each step, if ``depends_on`` is set and the named
    dependency is in ``prior_timed_out`` (the union of upstream-plan
    timed-out steps and any same-plan timed-out steps observed so far) and
    ``skip_if_dep_timed_out`` is True, the step is SKIPPED (not invoked).
    The skip is logged at WARNING so operators see the barrier fire.

    Parameters
    ----------
    controller:
        The owning :class:`ShutdownController` (unused inside the driver
        but kept for API symmetry with the original method signature).
    plan:
        The :class:`ShutdownPlan` to execute.
    prior_timed_out:
        Step names that timed out in a previously-run plan. Pass this to
        the next ``run_plan`` call so cross-plan barriers work.

    Returns
    -------
    frozenset[str]
        The union of ``prior_timed_out`` and any step names in THIS plan
        that timed out.
    """
    if not plan.steps:
        return prior_timed_out

    # Lazy import so tests that patch
    # ``voice_typer.server.shutdown_controller._run_with_timeout`` (and
    # ``_run_parallel_with_timeout``) still take effect — the module-level
    # name is bound at call time, not at module import time (mirrors the
    # convention used by ``shutdown/teardowns/recorder.py`` and
    # ``shutdown/teardowns/asr_models.py``). The lazy import also breaks
    # the would-be circular import (``shutdown_controller`` imports
    # ``shutdown.plan`` at module load time).
    from voice_typer.server.shutdown_controller import (
        _run_parallel_with_timeout,
        _run_with_timeout,
    )

    timed_out: set[str] = set(prior_timed_out)
    degraded: list[str] = []

    if plan.phase == "sequenced":
        for step in plan.steps:
            # Inter-step deadline check: when the 20s shutdown budget
            # is nearly exhausted (< 5s remaining), skip NON-CRITICAL
            # sequenced steps so the remaining budget goes to the
            # flush-bearing critical steps (``teardown_recorder`` /
            # ``teardown_history_db`` / ``teardown_crash_recovery``).
            # The skip is logged at WARNING and recorded in
            # ``controller._shutdown_skipped`` (when the controller
            # published one) so the final summary WARNING in
            # ``_do_cleanup`` lists every skipped step. The deadline
            # is published on the controller by ``_do_cleanup`` (set
            # to ``time.monotonic() + 20.0`` at entry, ``None`` outside
            # an active cleanup). Direct ``run_plan`` invocations from
            # tests use a fresh controller where the attribute is
            # ``None`` — the check is skipped, preserving test
            # behaviour.
            deadline = getattr(controller, "_shutdown_deadline", None)
            if deadline is not None and step.name not in CRITICAL_STEPS:
                remaining = deadline - time.monotonic()
                if remaining < _DEADLINE_NEAR_THRESHOLD:
                    log.warning(
                        "[SHUTDOWN] skipping non-critical step %s — shutdown deadline approaching (%.1fs remaining)",
                        step.name,
                        max(0.0, remaining),
                    )
                    skipped_list = getattr(controller, "_shutdown_skipped", None)
                    if skipped_list is not None:
                        skipped_list.append(step.name)
                    degraded.append(f"{step.name} (skipped: deadline near)")
                    continue
            if step.depends_on is not None and step.skip_if_dep_timed_out and step.depends_on in timed_out:
                log.warning(
                    "[SHUTDOWN] skipping %s because dependency %s "
                    "timed out (barrier — downstream call "
                    "touches the same OS resource as the leaked "
                    "upstream worker)",
                    step.name,
                    step.depends_on,
                )
                degraded.append(f"{step.name} (skipped: dep {step.depends_on} timed out)")
                continue
            try:
                result = _run_with_timeout(step.name, step.func, timeout=step.timeout)
                if result is TIMEOUT:
                    log.warning(
                        "[SHUTDOWN] %s timed out — worker thread leaked as daemon",
                        step.name,
                    )
                    timed_out.add(step.name)
                    degraded.append(f"{step.name} (timeout)")
            except BaseException as exc:  # noqa: BLE001 — per-step isolation
                log.debug("[SHUTDOWN] %s raised: %r", step.name, exc)
                degraded.append(f"{step.name} (raised: {exc!r})")
    elif plan.phase == "parallel":
        # Barrier (pre-flight skip): for each step, if its declared
        # ``depends_on`` is in ``timed_out`` and the step opted in to
        # ``skip_if_dep_timed_out``, SKIP the step (do not submit it to
        # the pool). The pre-flight skip is the canonical barrier location
        # for cross-plan dependencies. Per-step in-body barriers (e.g. the
        # ``_recorder_teardown_done`` Event inside ``teardown_sounddevice``)
        # remain as defense-in-depth.
        items: list[tuple[str, Callable[[], object], float]] = []
        for step in plan.steps:
            if step.depends_on is not None and step.skip_if_dep_timed_out and step.depends_on in timed_out:
                log.warning(
                    "[SHUTDOWN] skipping %s because dependency %s "
                    "timed out (barrier — downstream call "
                    "touches the same OS resource as the leaked "
                    "upstream worker)",
                    step.name,
                    step.depends_on,
                )
                degraded.append(f"{step.name} (skipped: dep {step.depends_on} timed out)")
                continue
            items.append((step.name, step.func, step.timeout))
        results = _run_parallel_with_timeout(items)
        for desc, result in results:
            if isinstance(result, BaseException):
                log.debug("[SHUTDOWN] %s raised: %r", desc, result)
                degraded.append(f"{desc} (raised: {result!r})")
            elif result is TIMEOUT:
                log.warning(
                    "[SHUTDOWN] %s timed out — worker thread leaked as daemon",
                    desc,
                )
                timed_out.add(desc)
                degraded.append(f"{desc} (timeout)")
    else:  # pragma: no cover — defensive; Literal type guards this
        log.error("[SHUTDOWN] unknown plan phase: %r", plan.phase)

    if degraded:
        log.warning(
            "[SHUTDOWN] %d/%d %s teardown helpers degraded: %s",
            len(degraded),
            len(plan.steps),
            plan.phase,
            ", ".join(degraded),
        )

    return frozenset(timed_out)


def build_sequenced_plan(
    controller: ShutdownController,
    deadline: float,
    skipped: list[str],
) -> ShutdownPlan:
    """Build the sequenced critical-teardown plan.

    Extracted from ``ShutdownController._build_sequenced_plan`` (the
    mixin method is now a thin delegate — the delegate stays so tests
    that monkeypatch or spy on ``controller._build_sequenced_plan``
    keep intercepting the call). The sequenced phase
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
    ``build_parallel_plan``): the sequenced phase completes BEFORE
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
    controller:
        The owning :class:`ShutdownController` — the ``_teardown_*``
        callables are resolved through the controller instance so
        test spies that patch them by name still take effect.
    deadline:
        The overall 20s shutdown deadline (``time.monotonic() +
        20.0``), used to decide whether to skip the non-critical
        ``teardown_timers_and_recording`` step.
    skipped:
        Mutable list of skipped step names; appended to in place so
        ``_do_cleanup`` can emit a single summary WARNING at the end.
    """
    # Lazy import (mirrors ``run_plan`` below): breaks the would-be
    # circular import — ``shutdown_controller`` imports this module at
    # package-init time (``__init__.py``), and tests import
    # ``voice_typer.server.shutdown.plan`` directly — AND keeps the
    # lookup dynamic so tests that patch the package-level helpers
    # still take effect.
    from voice_typer.server.shutdown_controller import (
        _shutdown_deadline_near,
        _shutdown_remaining,
    )

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
        ("teardown_session_marker", controller._teardown_session_marker, 5.0, None, False),
    )
    if _shutdown_deadline_near(deadline):
        _sc_log.warning(
            "[SHUTDOWN] deadline near (%.1fs remaining) at sequenced "
            "phase entry — skipping teardown_timers_and_recording (non-critical)",
            _shutdown_remaining(deadline),
        )
        skipped.append("teardown_timers_and_recording")
    else:
        sequenced_items.append(
            ("teardown_timers_and_recording", controller._teardown_timers_and_recording, 10.0, None, False),
        )
    sequenced_items.append(
        ("teardown_recorder", controller._teardown_recorder, 15.0, None, False),
    )
    sequenced_items.append(
        ("teardown_history_db", controller._teardown_history_db, 15.0, None, False),
    )
    sequenced_items.append(
        ("teardown_crash_recovery", controller._teardown_crash_recovery, 10.0, None, False),
    )
    sequenced_plan = ShutdownPlan(
        phase="sequenced",
        steps=tuple(ShutdownStep(*item) for item in sequenced_items),
    )
    return sequenced_plan


def build_parallel_plan(
    controller: ShutdownController,
    deadline: float,
    timed_out: frozenset[str],
    skipped: list[str],
) -> ShutdownPlan | None:
    """Build the parallel-batch plan, applying deadline-near skips.

    Extracted from ``ShutdownController._build_parallel_plan`` (the
    mixin method is now a thin delegate — the delegate stays so tests
    that monkeypatch or spy on ``controller._build_parallel_plan``
    keep intercepting the call). Each helper is
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
    controller:
        The owning :class:`ShutdownController` — the ``_teardown_*``
        callables are resolved through the controller instance so
        test spies that patch them by name still take effect.
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
    # Lazy import (mirrors ``run_plan`` below) — see
    # ``build_sequenced_plan`` for the cycle-breaking rationale.
    from voice_typer.server.shutdown_controller import (
        _shutdown_deadline_near,
        _shutdown_remaining,
    )

    _shutdown_critical_parallel: frozenset[str] = frozenset({"teardown_pid_file", "teardown_mutex_handle"})
    all_parallel_items: list[tuple[str, object, float, str | None, bool]] = [
        ("teardown_asr_models", controller._teardown_asr_models, 10.0, None, False),
        ("teardown_restore_volume", controller._teardown_restore_volume, 10.0, None, False),
        ("teardown_waveform_wiring", controller._teardown_waveform_wiring, 10.0, None, False),
        ("teardown_sounddevice", controller._teardown_sounddevice, 10.0, "teardown_recorder", True),
        ("teardown_pid_file", controller._teardown_pid_file, 10.0, None, False),
        ("teardown_mutex_handle", controller._teardown_mutex_handle, 10.0, None, False),
        ("teardown_devnull_files", controller._teardown_devnull_files, 10.0, None, False),
        ("teardown_level_monitor", controller._teardown_level_monitor, 10.0, None, False),
        ("teardown_hotkeys", controller._teardown_hotkeys, 10.0, None, False),
        ("teardown_electron", controller._teardown_electron, 10.0, None, False),
        ("teardown_event_bus", controller._teardown_event_bus, 10.0, None, False),
    ]
    parallel_items: list[tuple[str, object, float, str | None, bool]] = []
    for _desc, _func, _timeout, _dep, _skip in all_parallel_items:
        if _shutdown_deadline_near(deadline) and _desc not in _shutdown_critical_parallel:
            _sc_log.warning(
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


__all__ = [
    "CRITICAL_STEPS",
    "ShutdownPlan",
    "ShutdownStep",
    "build_parallel_plan",
    "build_sequenced_plan",
    "run_plan",
]
