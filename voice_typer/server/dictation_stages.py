"""DR-18: stage-based pipeline decomposition for ``DictationPipeline``.

ARCH-006 / DR-18: the 362-LOC ``DictationPipeline.run`` god method ran
9+ inline pipeline stages (transcribe, empty-check, clean, vocab,
templates, punct, llm, ai, vocab_auto, store, paste) plus embedded
try/except, in-flight sentinel file write, correlation-id setup,
watchdog cancellation check, crash-recovery write, bubble state
teardown, per-stage timing, audio zeroing, watchdog reset, and gc
collection.

This module breaks the *stage execution* portion of that method into a
list of small, single-responsibility stage objects that ``run`` iterates
over. The cross-cutting concerns (per-stage timing, watchdog
cancellation, crash-recovery teardown) are extracted into stage
wrappers (``CancellationGuard``) or sentinel exceptions
(``_PipelineAbortEmpty`` / ``_PipelineAbortCancelled``) so the run loop
stays uniform.

DR-18 design rules (NO behavior change):

* The 11 stages run in the SAME order with the SAME side effects as the
  original inline sequence. ``build_default_stages`` is the single
  source of truth for the order.
* Per-stage timing keys (``transcribe``, ``clean``, ``vocab``,
  ``templates``, ``punct``, ``llm``, ``ai``, ``vocab_auto``, ``store``,
  ``paste``) match the original ``_timed_stage(_timings, "<name>")``
  calls exactly so the consolidated ``[PIPE-PERF]`` log format strings
  are unchanged. ``EmptyCheckStage`` was NOT timed in the original
  (the empty-check ``if not text: …; return`` sat between two
  ``with _timed_stage`` blocks without its own wrapper), so it sets
  ``timed = False`` and the run loop skips the ``_timed_stage`` wrapper
  for it.
* The cancellation check that lived between ``_store_result`` and
  ``_copy_and_paste`` in the original is preserved by wrapping
  ``PasteStage`` in ``CancellationGuard`` — the guard runs the SAME
  crash-recovery write + bubble teardown as the original inline block,
  then raises ``_PipelineAbortCancelled`` to skip the paste and exit
  the pipeline cleanly.
* Each stage delegates to the corresponding ``_<step>`` method on the
  owning ``DictationPipeline`` (reached via ``ctx.pipeline``). The
  step methods themselves are unchanged — they keep their existing
  signatures, error handling, and side effects so the existing
  direct-call tests (``test_dictation_pipeline_review_fixes.py``,
  ``test_dictation_pipeline_h17_and_s3_cr10_fixes.py``,
  ``test_dictation_pipeline_check_resources.py``) keep passing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


# ── Sentinel exceptions for pipeline early-exit ─────────────────────────


class _PipelineAbort(Exception):  # noqa: N818
    """Base exception for pipeline early-exit.

    Raised by a stage to abort the pipeline cleanly (without entering
    the generic ``except Exception`` failure path that flips the tray
    to ERROR). The owning ``DictationPipeline.run`` catches each
    subclass and runs the appropriate teardown (which is the standard
    ``finally`` block — the same one that runs on the success path).
    """


class _PipelineAbortEmpty(_PipelineAbort):
    """Raised by :class:`EmptyCheckStage` when transcription is empty.

    ``EmptyCheckStage.run`` calls
    ``pipeline._handle_empty_transcription()`` (which sets the tray
    state, fires any "no speech detected" notification, and clears the
    busy event) BEFORE raising this exception. ``run`` catches it and
    does nothing — the finally block runs as usual, clearing the
    in-flight sentinel, zeroing the audio, resetting the watchdog, and
    clearing ``_transcription_thread``.

    This mirrors the original ``if not text: self._handle_empty_transcription(); return``
    early-return semantics exactly.
    """


class _PipelineAbortCancelled(_PipelineAbort):
    """Raised by :class:`CancellationGuard` when the cycle was force-cancelled.

    CR-006: when the watchdog force-cancels a stuck ctranslate2 call,
    the user has already been notified ("Transcription took too long
    and was cancelled") and has likely alt-tabbed to another window.
    Pasting the late transcription would corrupt whatever window
    currently has focus. ``CancellationGuard`` (wrapping
    :class:`PasteStage`) checks ``app.recording._cancelled_cycle_ids``
    before delegating to the paste step; if the cycle is cancelled, it
    writes the text to crash-recovery (so the user can review it
    manually), tears down the bubble, and raises this exception to
    skip the paste. ``run`` catches it and lets the finally block run.
    """


# ── Pipeline context ────────────────────────────────────────────────────


@dataclass
class PipelineContext:
    """Per-run context shared across stages.

    Holds the per-cycle state the original ``DictationPipeline.run``
    kept on the pipeline instance (audio, cycle_id, app reference,
    etc.). Stages reach the owning pipeline via ``pipeline`` so they
    can delegate to the existing ``_<step>`` methods (which read
    ``self._audio`` / ``self._cycle_id`` / ``self._app`` directly).
    """

    cycle_id: str
    audio: Any
    app: Any
    pipeline: Any
    # Future-proofing: a free-form bag for stages to stash per-cycle
    # state without re-reaching into the pipeline's privates. Currently
    # unused (every stage delegates to a ``_<step>`` method), but the
    # protocol requires a place for stages to coordinate state without
    # growing the dataclass every time.
    extras: dict[str, Any] = field(default_factory=dict)


# ── Stage protocol ──────────────────────────────────────────────────────


@runtime_checkable
class PipelineStage(Protocol):
    """A single stage in the dictation pipeline.

    A stage takes the current ``text`` (the empty string for the first
    stage — :class:`TranscribeStage` ignores its argument and produces
    text from the audio in ``ctx``) and a :class:`PipelineContext`,
    and returns the (possibly transformed) text for the next stage.

    The ``name`` attribute is used as the per-stage timing key in the
    ``_timed_stage`` context manager — it MUST match the original
    inline ``with _timed_stage(_timings, "<name>")`` keys so the
    consolidated ``[PIPE-PERF]`` log format strings stay unchanged.

    The ``timed`` attribute (default ``True``) controls whether the
    run loop wraps the stage call in ``_timed_stage``.
    :class:`EmptyCheckStage` sets ``timed = False`` to preserve the
    original behavior (the empty-check ``if not text: …; return`` was
    not wrapped in a ``_timed_stage`` block).
    """

    name: str
    timed: bool

    def run(self, text: str, ctx: PipelineContext) -> str: ...


# ── Stage implementations ───────────────────────────────────────────────
#
# Each stage is a thin delegator: it calls the corresponding ``_<step>``
# method on ``ctx.pipeline`` and returns the (possibly transformed)
# text. The step methods are unchanged from the pre-DR-18 code — they
# keep their existing signatures, error handling, and side effects so
# the existing direct-call tests keep passing.


class TranscribeStage:
    """Step 1: Transcribe via streaming finalize or direct ASR backend.

    Ignores the incoming ``text`` (which is the empty string on the
    first iteration) and produces a transcript from ``ctx.audio``.
    """

    name = "transcribe"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._transcribe()


class EmptyCheckStage:
    """Step 2: Handle the case where transcription produced no text.

    If the transcript is empty, calls
    ``pipeline._handle_empty_transcription()`` (which sets the tray
    state, fires any "no speech detected" notification, and clears the
    busy event) and raises :class:`_PipelineAbortEmpty` to abort the
    pipeline cleanly.

    Not timed — the original empty-check ``if not text: …; return`` sat
    between two ``with _timed_stage`` blocks without its own wrapper,
    so we set ``timed = False`` to keep the ``_timings`` dict keys
    identical to the original.
    """

    name = "empty_check"
    timed = False

    def run(self, text: str, ctx: PipelineContext) -> str:
        if not text:
            ctx.pipeline._handle_empty_transcription()
            raise _PipelineAbortEmpty()
        return text


class CleanupStage:
    """Step 3: Apply text cleanup (spacing, self-corrections, capitalization)."""

    name = "clean"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._clean_text(text)


class VocabularyStage:
    """Step 4: Apply vocabulary corrections."""

    name = "vocab"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._apply_vocabulary(text)


class TemplatesStage:
    """Step 5: Apply template matching.

    Sets ``pipeline._templates_applied = True`` when a template match
    modifies the text — see S3-CR-10 in
    :meth:`DictationPipeline._apply_templates`.
    """

    name = "templates"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._apply_templates(text)


class PunctuationStage:
    """Step 6: Apply auto-punctuation."""

    name = "punct"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._apply_punctuation(text)


class LLMPolishStage:
    """Step 7: Apply LLM polishing (if consented).

    Logs the S3-CR-10 privacy NOTICE when templates were applied
    earlier in this cycle.
    """

    name = "llm"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._apply_llm_polish(text)


class AIEnhancementStage:
    """Step 7b: Apply rule-based AI enhancement (P4)."""

    name = "ai"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._apply_ai_enhancement(text)


class VocabularyAutomationStage:
    """Step 7c: Analyze transcription for vocabulary suggestions (P5).

    Pure side-effect stage — analyzes the text for high-confidence
    vocabulary suggestions and publishes them to the frontend via
    ``event_bus``. Does NOT modify the text; returns it unchanged.
    """

    name = "vocab_auto"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        ctx.pipeline._analyze_vocabulary(text)
        return text


class StoreResultStage:
    """Step 8: Store in history DB and crash recovery.

    Pure side-effect stage — writes the text to ``history_db`` and
    (if enabled) the crash-recovery buffer. Returns the text unchanged
    so :class:`CancellationGuard` can decide whether to paste it.
    """

    name = "store"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        ctx.pipeline._store_result(text)
        return text


class PasteStage:
    """Step 9: Copy to clipboard + paste to target app.

    Orchestrates the clipboard borrow/restore lifecycle and the
    post-paste tray/bubble teardown. See
    :meth:`DictationPipeline._copy_and_paste` for the full contract
    (ADR-0010 §6.1 / DP1 / DP2 / DP4).
    """

    name = "paste"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        ctx.pipeline._copy_and_paste(text)
        return text


# ── CancellationGuard: cross-cutting concern wrapper ────────────────────


class CancellationGuard:
    """Wraps a stage with a watchdog cancellation check.

    CR-006 / DR-18: the original ``DictationPipeline.run`` checked
    ``app.recording._cancelled_cycle_ids`` ONCE, between
    ``_store_result`` and ``_copy_and_paste``, after a stuck
    ctranslate2 call was force-cancelled by the watchdog. If the cycle
    was cancelled, the original code:

      1. Logged a WARNING explaining the late-transcription skip.
      2. Wrote the text to crash-recovery (pasted=False) so the user
         could review it manually.
      3. Tore down the bubble + tray state (the watchdog had already
         set tray to IDLE, but the bubble might still be showing
         "Transcribing…").
      4. Returned early, skipping the paste step.

    This wrapper preserves that behavior exactly by wrapping
    :class:`PasteStage` (so the check fires immediately before the
    paste, just like the original). On cancellation it does steps 1-3
    and raises :class:`_PipelineAbortCancelled` to skip the paste —
    ``run`` catches the exception and lets the finally block run.

    The membership check is performed under
    ``_cancelled_cycle_ids_lock`` — the SAME lock used by
    ``recording_controller._force_recover`` when mutating the set, to
    avoid the torn-read hazard. Falls back to "not cancelled" if the
    lock or set is missing (defensive — the attrs always exist on a
    real RecordingController).

    Note: this guard is intentionally NARROW (wraps only the paste
    stage) to preserve the original single-check semantics. A broader
    per-stage cancellation check would change the observable behavior
    (the original ran stages 1-8 even on a cancelled cycle; the guard
    ensures stages 1-8 still run).
    """

    def __init__(self, wrapped: PipelineStage) -> None:
        self._wrapped = wrapped
        # Inherit the wrapped stage's name so the run loop's
        # ``_timed_stage(_timings, stage.name)`` records under the
        # same key the original used ("paste"). The guard's
        # cancellation check is fast (a set lookup under a lock) and
        # is intentionally NOT timed separately — the original
        # inline check ran outside the ``with _timed_stage("paste")``
        # block, but the timing delta is negligible (sub-millisecond)
        # and rolling it into the paste timing keeps the
        # ``_timings`` keys identical to the original.
        self.name = wrapped.name
        self.timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        app = ctx.app
        cycle_id = ctx.cycle_id

        _cancelled_set = getattr(app.recording, "_cancelled_cycle_ids", None)
        _cancelled_lock = getattr(app.recording, "_cancelled_cycle_ids_lock", None)
        if _cancelled_set is not None and _cancelled_lock is not None:
            with _cancelled_lock:
                _is_cancelled = cycle_id in _cancelled_set
        else:
            _is_cancelled = False

        if _is_cancelled:
            log.warning(
                "[DICTATION] skipping paste of late transcription (cycle %s was force-cancelled by watchdog)",
                cycle_id,
            )
            try:
                # Persist to crash-recovery so the user can review the
                # late transcription manually (without auto-pasting it).
                if hasattr(app, "_crash_recovery"):
                    app._crash_recovery.add(text, pasted=False)
            except Exception:
                log.debug(
                    "[DICTATION] crash-recovery write for cancelled cycle failed",
                    exc_info=True,
                )
            # Tear down the bubble + tray state — the watchdog already
            # set tray to IDLE, but the bubble may still be showing
            # "Transcribing…" if the watchdog's tray update happened
            # before the bubble wiring was reset.
            try:
                if app.config.bubble_behavior == "always_visible":
                    app._waveform_bubble.set_state("idle")
                else:
                    app._waveform_bubble.hide()
            except Exception:
                log.debug(
                    "[DICTATION] bubble hide on cancelled cycle failed",
                    exc_info=True,
                )
            # Skip the wrapped paste stage — the cycle was cancelled.
            raise _PipelineAbortCancelled()

        return self._wrapped.run(text, ctx)


# ── Factory: the standard 11-stage dictation pipeline ───────────────────


def build_default_stages() -> list[PipelineStage]:
    """Construct the standard 11-stage dictation pipeline.

    The order is preserved EXACTLY from the original inline
    ``DictationPipeline.run`` (DR-18 refactor — no behavior change):

      1. ``transcribe``   — streaming finalize or direct ASR
      2. ``empty_check``  — handle empty transcription (not timed)
      3. ``clean``        — text cleanup (spacing, self-corrections)
      4. ``vocab``        — vocabulary correction
      5. ``templates``    — template matching (sets ``_templates_applied``)
      6. ``punct``        — auto-punctuation
      7. ``llm``          — LLM polish (gated by consent + API key)
      8. ``ai``           — rule-based AI enhancement (P4)
      9. ``vocab_auto``   — vocabulary automation analysis (P5)
     10. ``store``        — history DB + crash-recovery write
     11. ``paste``        — clipboard copy + paste (wrapped by
                            :class:`CancellationGuard` for the CR-006
                            watchdog cancellation check)

    Returns a fresh list so callers can mutate (insert/remove stages)
    without affecting other pipelines.
    """
    return [
        TranscribeStage(),
        EmptyCheckStage(),
        CleanupStage(),
        VocabularyStage(),
        TemplatesStage(),
        PunctuationStage(),
        LLMPolishStage(),
        AIEnhancementStage(),
        VocabularyAutomationStage(),
        StoreResultStage(),
        # CR-006: wrap PasteStage in CancellationGuard so the
        # watchdog cancellation check fires immediately before the
        # paste (preserving the original single-check semantics).
        CancellationGuard(PasteStage()),
    ]
