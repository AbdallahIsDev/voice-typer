"""StageTimer — context manager for per-stage timing instrumentation.

Pre-refactor (AC-73): the orchestrator's ``run`` method inlined
9 ``_stage_t0`` / ``_<name>_ms`` pairs plus a hand-edited format
string in the consolidated log line. The dictation_pipeline
package then introduced the ``_timed_stage`` helper in
``helpers.py`` (one ``with`` per stage instead of the 3-line
pair); this module provides an alternative class-based form
(``StageTimer``) so callers that prefer an explicit object
over a context-manager function can use it.

``StageTimer`` records the elapsed wall-time for a named stage
into a ``dict[str, float]`` (keyed by stage name). It is the
moral equivalent of::

    _t0 = time.perf_counter()
    try:
        ...  # stage body
    finally:
        _timings[name] = (time.perf_counter() - _t0) * 1000

but as a one-line ``with`` so adding a new stage is a one-line
edit (not 3). Both ``StageTimer`` and ``_timed_stage`` are
interchangeable — the orchestrator uses ``_timed_stage`` because
the pipeline already migrated to it; ``StageTimer`` is provided
for new callers that prefer a class form.
"""

from __future__ import annotations

import time


class StageTimer:
    """Context manager: record a named stage's wall-time in ms.

    Args:
        timings: the dict to record into (keyed by ``name``).
        name: the stage name (e.g. ``"transcribe"``, ``"clean"``).

    On exit, sets ``timings[name]`` to the elapsed milliseconds
    (overwriting any prior value for the same name — a stage
    shouldn't run twice in one cycle).

    The dict is mutated in place so the caller can read the
    collected timings after the ``with`` block ends (e.g. for
    the consolidated ``[PIPE-PERF]`` log line at the end of
    ``run``).
    """

    def __init__(self, timings: dict[str, float], name: str) -> None:
        self._timings = timings
        self._name = name
        self._t0: float = 0.0

    def __enter__(self) -> StageTimer:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Record regardless of whether the stage raised — a stage that
        # raised mid-way still consumed wall-time, and the consolidated
        # PIPE-PERF log line is emitted from the finally block (so the
        # timing is observable even on the failure path).
        elapsed_ms = (time.perf_counter() - self._t0) * 1000
        self._timings[self._name] = elapsed_ms
        # Do not suppress — propagate the exception if one was raised.
        return None
