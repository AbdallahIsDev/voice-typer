"""Extracted ``_finalize_cycle`` — the ``finally`` block of ``run``.

Pre-refactor: the orchestrator's ``run`` method's
``finally`` block was 197 lines. The dictation_pipeline package split then extracted
the 7 cleanup steps into ``_cleanup_*`` methods on the
orchestrator mixin (each with its own try/except + log.debug so
a stuck-busy state is diagnosable from the log). The finally
block itself shrank to a sequence of 7 method calls + a
correlation-id reset — but those 7 method bodies lived on
the orchestrator mixin (bloating it) and the call sequence
itself was still inline in ``run``.

This module extracts the SEQUENCE (the call ordering) into a
standalone function :func:`_finalize_cycle`, plus the four
named sub-helpers from the task spec:

  * :func:`_zero_audio` — zero + clear the audio buffer
    (SEC-audit-008 defense-in-depth — the immediate post-
    transcribe zero already happened inside the loop; this
    is a second no-op-if-already-None pass in the finally).
  * :func:`_reset_watchdog_and_cancelled_set` — reset the
    persistent watchdog thread + discard this cycle from
    the cancelled set (RACE-013 / RACE-016).
  * :func:`_teardown_session_and_thread` — cancel any active
    streaming session ( pop_streaming_session) + clear
    ``_transcription_thread`` under ``_watchdog_lock``.
  * :func:`_reset_correlation_id` — clear the per-thread
    correlation id published at the top of ``run`` so a
    finished cycle can't leak its id into a later log line.

The orchestrator's ``run`` now calls ``_finalize_cycle(self,
_corr_token)`` from its ``finally`` block — a single line
instead of the 7 inline calls + correlation reset.

Behavior is byte-identical to the pre-refactor inline finally
block (the existing ``_cleanup_*`` methods on the orchestrator
mixin are kept as the implementation; this module just wraps
the call sequence + correlation reset).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Avoid runtime circular import (orchestrator imports this module).
    from voice_typer.server.dictation_pipeline import DictationPipeline  # noqa: F401

log = logging.getLogger(__name__)


def _finalize_cycle(pipeline: Any, corr_token: object | None) -> None:
    """Run the 7-step finally-block cleanup + reset the correlation id.

    Args:
        pipeline: the ``DictationPipeline`` instance (typed ``Any`` —
            see :mod:`_run_body` for the circular-import rationale).
        corr_token: the correlation-id reset token returned by
            ``set_correlation_id`` at the top of ``run`` (``None`` if
            ``cycle_id`` was empty and no token was published).

    Each cleanup step below is delegated to a ``_cleanup_*`` method
    on the orchestrator mixin (each carries its own try/except +
    log.debug so a stuck-busy state is diagnosable). The original
    exception from the try block above is preserved — the finally
    block must NOT raise.

    The cleanup sequence is intentionally a sequence (NOT parallel)
    because each step has a different failure mode and we want
    observability into WHICH step failed if a stuck-busy state is
    reported. A failure in step N is logged and swallowed so step
    N+1 still runs.
    """
    # Steps 1-7: each owned by a ``_cleanup_*`` method on the
    # orchestrator mixin (preserved as-is for back-compat with
    # any tests that call them directly).
    pipeline._cleanup_sentinel_unlink()
    _zero_audio(pipeline)
    _reset_watchdog_and_cancelled_set(pipeline)
    _teardown_session_and_thread(pipeline)
    pipeline._cleanup_busy_event_clear()
    pipeline._cleanup_transcription_thread_clear()
    pipeline._cleanup_gc_collect()
    log.debug("[TRANSCRIBE] busy reset to False (cycle=%s)", pipeline._cycle_id)
    _reset_correlation_id(corr_token)


def _zero_audio(pipeline: Any) -> None:
    """Finally-block step 2: zero + clear the audio buffer.

    Delegates to the orchestrator's ``_cleanup_audio_zero`` method
    (which carries the SEC-audit-008 rationale + try/except + log.debug).
    Kept as a thin wrapper here so the task-spec naming
    (``_zero_audio``) is honored for callers that prefer it.
    """
    pipeline._cleanup_audio_zero()


def _reset_watchdog_and_cancelled_set(pipeline: Any) -> None:
    """Finally-block step 3: reset the watchdog + discard this cycle from the cancelled set.

    Delegates to the orchestrator's ``_cleanup_watchdog_reset`` method
    (RACE-013 / RACE-016 rationale + try/except + log.debug).
    """
    pipeline._cleanup_watchdog_reset()


def _teardown_session_and_thread(pipeline: Any) -> None:
    """Finally-block step 4: cancel any active streaming session.

    Delegates to the orchestrator's
    ``_cleanup_streaming_session_cancel`` method ( rationale +
    try/except + log.debug).
    """
    pipeline._cleanup_streaming_session_cancel()


def _reset_correlation_id(corr_token: object | None) -> None:
    """Finally-block step 8: clear the per-thread correlation id.

    Runs in the finally block so it executes on both the success and
    the handled-exception paths. A finished transcription cycle can't
    leak its id into a later, unrelated log line (e.g. the next cycle,
    or a background prewarm thread sharing this process).

    ``corr_token`` is the value returned by ``set_correlation_id`` at
    the top of ``run`` (``None`` if no token was published — in which
    case this function is a no-op).
    """
    if corr_token is not None:
        from voice_typer.server.log import reset_correlation_id

        reset_correlation_id(corr_token)
