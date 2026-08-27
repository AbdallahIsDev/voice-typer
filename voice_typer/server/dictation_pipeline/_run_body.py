"""Extracted ``_run_pipeline_body`` — the try-block body of ``run``.

Pre-refactor: the orchestrator's ``run`` method was 285
lines, with the try-block body alone accounting for ~117 of them
(stage loop + per-stage timing instrumentation + transcribe
post-stage audio zero + consolidated PIPE-PERF log line).

This module extracts the try-block body into a standalone
function :func:`_run_pipeline_body` that takes the pipeline
instance (``self``) plus the hoisted ``text`` local and runs
the 11-stage dictation loop. The orchestrator's ``run`` now
delegates to this function — the loop body's complexity
(stage iteration, post-transcribe audio zero, timing log) is
moved off the orchestrator's hot path so ``run`` shrinks to a
thin try/except/finally dispatcher (≤ 60 lines).

Behavior is byte-identical to the pre-refactor inline body —
the only change is the location (function vs inline). The
pipeline instance is passed in (not constructed here) so the
function can access pipeline state (``self._audio``,
``self._cycle_id``, ``self._app``, ``self._stages``) via the
same attribute reads the inline body used.

Imports that were at the top of ``orchestrator.py`` (numpy,
``_timed_stage``, ``PipelineContext``, ``build_default_stages``,
``format_duration``, the ``log`` logger) are imported here
because the body uses them — no cross-module circular import
(the dictation_pipeline package's ``__init__`` composes the
mixins AFTER all submodules are loaded).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from voice_typer.server.dictation_pipeline.helpers import _timed_stage
from voice_typer.server.dictation_stages import (
    PipelineContext,
    build_default_stages,
)
from voice_typer.server.duration import format_duration

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Avoid a runtime circular import: the orchestrator mixin imports
    # this module at top-level (so ``run`` can delegate to
    # ``_run_pipeline_body``), and this module type-hints the pipeline
    # for editor completion only. The actual pipeline type is the
    # composed ``DictationPipeline`` class — duck-typed at runtime.
    from voice_typer.server.dictation_pipeline import DictationPipeline  # noqa: F401


def _run_pipeline_body(
    pipeline: Any,
    text: str,
) -> str:
    """Run the 11-stage dictation pipeline loop.

    Args:
        pipeline: the ``DictationPipeline`` instance (typed ``Any``
            because the composed class lives in the package ``__init__``
            and importing it here would create a circular import).
        text: the hoisted ``text`` local (always ``""`` on entry —
            kept as a parameter so the caller's contract is explicit).

    Returns:
        The final transcribed text (after all 11 stages).

    Raises:
        ``_PipelineAbortEmpty`` — when ``EmptyCheckStage`` already
            handled an empty transcription and wants to abort cleanly.
        ``_PipelineAbortCancelled`` — when ``CancellationGuard``
            wrapping ``PasteStage`` detected a late cancel.
        ``Exception`` — any unhandled exception from a stage.
        All three are caught by the caller's ``try/except`` block.
    """
    log.info("[TRANSCRIBE] Starting transcription... (cycle=%s)", pipeline._cycle_id)

    # PRE-FLIGHT: resource health check (throttled to once per 60s).
    pipeline._check_resources_throttled()

    _t0 = time.perf_counter()
    _timings: dict[str, float] = {}

    # Lazily rebuild the stage list if a test bypassed ``__init__``.
    stages = getattr(pipeline, "_stages", None) or build_default_stages()
    ctx = PipelineContext(
        cycle_id=pipeline._cycle_id,
        audio=pipeline._audio,
        app=pipeline._app,
        pipeline=pipeline,
    )

    # Mirror the in-flight partial transcription onto the pipeline after
    # each completed stage so the generic ``except Exception`` handler
    # (:func:`_handle_cancelled_cycle`) can still save it when a later
    # stage raises. On that path the ``return`` statement below (and thus
    # the caller's ``text = _run_pipeline_body(...)`` assignment) never
    # executes, leaving the caller's local empty.
    pipeline._partial_transcript = ""
    for stage in stages:
        text = _run_one_stage(pipeline, stage, text, ctx, _timings, _t0)
        pipeline._partial_transcript = text

    _log_consolidated_timings(pipeline, _timings, _t0)
    return text


def _run_one_stage(
    pipeline: Any,
    stage: Any,
    text: str,
    ctx: PipelineContext,
    timings: dict[str, float],
    t0: float,
) -> str:
    """Run a single stage + its post-stage side-effects (timing, audio zero)."""
    if getattr(stage, "timed", True):
        with _timed_stage(timings, stage.name):
            text = stage.run(text, ctx)
    else:
        text = stage.run(text, ctx)

    # Step 1's post-stage logging is unique (it reports the total
    # elapsed time since run-entry, not just the stage's own duration).
    # Kept inline here to preserve the exact log format and timing
    # reference. Also zero-and-release the audio buffer immediately
    # (stages 3-11 operate on text only — the finally-block audio
    # zero becomes a no-op here).
    if stage.name == "transcribe":
        _elapsed = time.perf_counter() - t0
        log.info(
            "[TRANSCRIBE] Transcription complete (len=%d, cycle=%s)%s",
            len(text) if text else 0,
            pipeline._cycle_id,
            format_duration(_elapsed),
        )
        log.debug(
            "[PIPE-PERF] transcribe: %.0f ms (cycle=%s)",
            timings.get("transcribe", 0.0),
            pipeline._cycle_id,
        )
        try:
            if pipeline._audio is not None and isinstance(pipeline._audio, np.ndarray):
                pipeline._audio.fill(0)
        except Exception:
            log.debug(
                "[PIPELINE] post-transcribe audio zero failed",
                exc_info=True,
            )
        pipeline._audio = None
        ctx.audio = None

    return text


def _log_consolidated_timings(
    pipeline: Any,
    timings: dict[str, float],
    t0: float,
) -> None:
    """Emit the consolidated ``[PIPE-PERF]`` log line at end of run."""
    _total_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "[PIPE-PERF] total=%.0fms, stages: transcribe=%.0f, clean=%.0f, "
        "vocab=%.0f, templates=%.0f, punct=%.0f, store=%.0f, "
        "paste=%.0f (cycle=%s)",
        _total_ms,
        timings.get("transcribe", 0.0),
        timings.get("clean", 0.0),
        timings.get("vocab", 0.0),
        timings.get("templates", 0.0),
        timings.get("punct", 0.0),
        timings.get("store", 0.0),
        timings.get("paste", 0.0),
        pipeline._cycle_id,
    )
    if timings.get("llm", 0.0) > 1:
        log.info(
            "[PIPE-PERF] llm_polish=%.0fms, ai_enhance=%.0fms, vocab_auto=%.0fms (cycle=%s)",
            timings.get("llm", 0.0),
            timings.get("ai", 0.0),
            timings.get("vocab_auto", 0.0),
            pipeline._cycle_id,
        )
