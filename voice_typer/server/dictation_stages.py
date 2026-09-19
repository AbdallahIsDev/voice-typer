"""Dictation stage enum/ids."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


class _PipelineAbort(Exception):  # noqa: N818
    """Base exception for pipeline early-exit."""


class _PipelineAbortEmpty(_PipelineAbort):
    """Raised by :class:`EmptyCheckStage` when transcription is empty."""


class _PipelineAbortCancelled(_PipelineAbort):
    """Raised by :class:`CancellationGuard` when the cycle was force-cancelled."""


@dataclass
class PipelineContext:
    """Per-run context shared across stages."""

    cycle_id: str
    audio: Any
    app: Any
    pipeline: Any
    # Future-proofing: a free-form bag for stages to stash per-cycle
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PipelineStage(Protocol):
    """A single stage in the dictation pipeline."""

    name: str
    timed: bool

    def run(self, text: str, ctx: PipelineContext) -> str: ...


# Each stage is a thin delegator: it calls the corresponding ``_<step>``


class TranscribeStage:
    """Step 1: Transcribe via streaming finalize or direct ASR backend."""

    name = "transcribe"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._transcribe()


class EmptyCheckStage:
    """Step 2: Handle the case where transcription produced no text."""

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
    """Step 5: Apply template matching."""

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
    """Step 7: Apply LLM polishing (if consented)."""

    name = "llm"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._apply_llm_polish(text)


class AIEnhancementStage:
    """Step 7b: Apply rule-based AI enhancement."""

    name = "ai"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        return ctx.pipeline._apply_ai_enhancement(text)


class VocabularyAutomationStage:
    """Step 7c: Analyze transcription for vocabulary suggestions (P5)."""

    name = "vocab_auto"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        ctx.pipeline._analyze_vocabulary(text)
        return text


class StoreResultStage:
    """Step 8: Store in history DB and crash recovery."""

    name = "store"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        ctx.pipeline._store_result(text)
        return text


class PasteStage:
    """Step 9: Copy to clipboard + paste to target app."""

    name = "paste"
    timed = True

    def run(self, text: str, ctx: PipelineContext) -> str:
        ctx.pipeline._copy_and_paste(text)
        return text


class CancellationGuard:
    """lock or set is missing (defensive, the attrs always exist on a"""

    def __init__(self, wrapped: PipelineStage) -> None:
        self._wrapped = wrapped
        # Inherit the wrapped stage's name so the run loop's
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
                if hasattr(app, "_crash_recovery") and getattr(app.config, "crash_recovery_enabled", False):
                    app._crash_recovery.add(text, pasted=False, cycle_id=cycle_id)
            except Exception:
                log.debug(
                    "[DICTATION] crash-recovery write for cancelled cycle failed",
                    exc_info=True,
                )
            # Tear down the bubble + tray state, the watchdog already
            ctx.pipeline._hide_or_idle_bubble("bubble hide on cancelled cycle")
            # Skip the wrapped paste stage, the cycle was cancelled.
            raise _PipelineAbortCancelled()

        return self._wrapped.run(text, ctx)


def build_default_stages() -> list[PipelineStage]:
    """Construct the standard 11-stage dictation pipeline.

    Returns a fresh list so callers can mutate (insert/remove stages)
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
        # wrap PasteStage in CancellationGuard so the
        CancellationGuard(PasteStage()),
    ]
