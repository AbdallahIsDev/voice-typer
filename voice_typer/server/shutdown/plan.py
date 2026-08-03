"""Declarative shutdown plan + driver (extracted from ``shutdown_controller``).

Houses the :class:`ShutdownStep` / :class:`ShutdownPlan` dataclasses and the
:func:`run_plan` driver that used to live inline on
:class:`voice_typer.server.shutdown_controller.ShutdownController`.

The dataclasses express the teardown ordering contract — sequenced critical
flushes (recorder → history_db → crash_recovery) run before the parallel
batch of independent subsystem teardowns. The driver wraps each step in a
per-step timeout (via :func:`voice_typer.server._timeout_utils._run_with_timeout`
or :func:`_run_parallel_with_timeout`) and applies the cross-step barrier
(``skip_if_dep_timed_out``) so a downstream call that touches the same OS
resource as a leaked upstream worker is skipped (notably ``sd.stop()`` after
a timed-out ``recorder.stop()`` on WASAPI PortAudio backends).

The controller keeps a thin ``_run_plan`` delegate so existing call sites
(``controller._run_plan(plan, prior_timed_out)``) and tests
(``tests/test_shutdown_plan_zr17.py``) continue to work unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from voice_typer.server._timeout_utils import TIMEOUT

if TYPE_CHECKING:
    from voice_typer.server.shutdown_controller import ShutdownController

log = logging.getLogger(__name__)


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
        items: list[tuple[str, object, float]] = []
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


__all__ = [
    "ShutdownPlan",
    "ShutdownStep",
    "run_plan",
]
