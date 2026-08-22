"""Extracted ``_handle_cancelled_cycle`` — the ``except Exception`` handler.

Pre-refactor (AC-73): the orchestrator's ``run`` method's
``except Exception as e:`` block was 56 lines inline, bundling
four distinct concerns:

  1. log the failure (``log.exception``).
  2. surface the failure in the waveform bubble (set state
     "error", schedule a 3s bubble error->idle transition).
  3. surface the failure in the tray (set ERROR state with a
     user-friendly reason, notify, schedule a 3s tray
     ERROR->IDLE transition).
  4. save the partial transcription to crash recovery (best-effort,
     gated on ``crash_recovery_enabled`` AND ``text`` non-empty).

This module extracts that block into a standalone function
:func:`_handle_cancelled_cycle` so the orchestrator's ``run``
shrinks to a thin dispatcher. Behavior is byte-identical to
the pre-refactor inline body.

NOTE: this is the GENERIC ``except Exception`` handler — not the
``_PipelineAbortEmpty`` / ``_PipelineAbortCancelled`` sentinel
handlers (those just ``pass`` and fall through to the finally
block, where the EmptyCheckStage / CancellationGuard already did
their work before raising the sentinel).
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.dictation_pipeline.helpers import (
    _friendly_transcription_error,
)

if TYPE_CHECKING:
    # Avoid runtime circular import (orchestrator imports this module).
    from voice_typer.server.dictation_pipeline import DictationPipeline  # noqa: F401

log = logging.getLogger(__name__)


def _handle_cancelled_cycle(
    pipeline: Any,
    exc: BaseException,
    text: str,
) -> None:
    """Handle a transcription cycle that raised an unexpected exception.

    Args:
        pipeline: the ``DictationPipeline`` instance (typed ``Any`` —
            see :mod:`_run_body` for the circular-import rationale).
        exc: the exception raised by the failed stage.
        text: the partial transcription text (hoisted to before the
            ``try`` block in ``run`` so it's always in scope here).

    Side effects (all best-effort — any failure is logged + swallowed
    so the finally block still runs):

      * ``log.exception`` the failure (cycle_id tagged).
      * Bubble: set state "error", schedule a 3s error->idle
        transition (the bubble shows the failure visibly instead of
        just flipping the tray icon).
      * Tray: set ERROR state with a user-friendly reason, notify,
        schedule a 3s ERROR->IDLE transition.
      * Crash recovery: if the user opted in (``crash_recovery_enabled``)
        AND the partial text is non-empty, append it to the crash
        recovery buffer so the user can recover it later.
    """
    log.exception("[TRANSCRIBE] Transcription FAILED (cycle=%s)", pipeline._cycle_id)

    _surface_failure_in_bubble(pipeline)
    _surface_failure_in_tray(pipeline, exc)
    _save_partial_transcription(pipeline, text)


def _surface_failure_in_bubble(pipeline: Any) -> None:
    """Set the bubble to ``error`` state and schedule a 3s error->idle transition."""
    try:
        pipeline._app._waveform_bubble.set_state("error")

        def _bubble_error_to_idle() -> None:
            pipeline._hide_or_idle_bubble("bubble error->idle transition")

        pipeline._app._schedule_timer(3.0, _bubble_error_to_idle)
    except Exception:
        log.debug("[PIPELINE] bubble set_state('error') on failure failed", exc_info=True)


def _surface_failure_in_tray(pipeline: Any, exc: BaseException) -> None:
    """Set tray ERROR state + notify + schedule a 3s ERROR->IDLE transition."""
    reason = _friendly_transcription_error(exc)
    pipeline._app.tray.set_state(_resolve_app_state("ERROR"), reason)
    pipeline._app.tray.notify(APP_NAME, reason)
    pipeline._app._schedule_timer(3.0, lambda: pipeline._app.tray.set_state(_resolve_app_state("IDLE")))


def _resolve_app_state(name: str) -> Any:
    """Look up ``AppState.<name>`` lazily (avoids a top-level import cycle)."""
    # Imported lazily inside the function (rather than at module top)
    # because ``tray_types`` triggers a chain of imports that would
    # create a cycle when this module is imported by the orchestrator
    # during the dictation_pipeline package load. The lazy import
    # costs ~50us per call but only fires on the failure path.
    from voice_typer.server.tray_types import AppState

    return getattr(AppState, name)


def _save_partial_transcription(pipeline: Any, text: str) -> None:
    """Best-effort save the partial transcription to crash recovery.

    Gated on ``crash_recovery_enabled`` AND the text being non-empty
    (don't pollute the buffer with empty strings). Any exception is
    suppressed (via ``contextlib.suppress``) so a crash-recovery
    failure cannot mask the original transcription error.
    """
    # Prefer the caller's ``text``; fall back to the runner's mirrored
    # partial transcript (updated after each completed stage in
    # :func:`_run_pipeline_body`). When a stage raises mid-loop the
    # caller's ``text = _run_pipeline_body(...)`` assignment never
    # happens, so the mirror is the only record of what was transcribed.
    effective_text = text or getattr(pipeline, "_partial_transcript", "")
    if effective_text and getattr(pipeline._app.config, "crash_recovery_enabled", False):
        with contextlib.suppress(Exception):
            pipeline._app._crash_recovery.add(effective_text, pasted=False)
            pipeline._app._crash_recovery.flush(timeout=0.5)
