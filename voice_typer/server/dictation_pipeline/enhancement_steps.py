"""Post-transcription enhancement stages (vocabulary, LLM polish hooks)."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
from typing import TYPE_CHECKING, Any

from voice_typer.server._secrets import redact_secret
from voice_typer.server.branding import APP_NAME
from voice_typer.server.dictation_pipeline.helpers import (
    _EMPTY_SEGMENTS,
    _NO_TRANSCRIPT_CONFIDENCE,
)

if TYPE_CHECKING:
    # Annotation-only import: types the shared polish executor without
    import concurrent.futures

log = logging.getLogger(__name__)


# module-level singleton ``ThreadPoolExecutor`` for LLM polish.
_SHARED_POLISH_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_SHARED_POLISH_EXECUTOR_LOCK = threading.Lock()


def _get_shared_polish_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the module-level singleton ``ThreadPoolExecutor``."""
    global _SHARED_POLISH_EXECUTOR
    import concurrent.futures

    if _SHARED_POLISH_EXECUTOR is None:
        with _SHARED_POLISH_EXECUTOR_LOCK:
            if _SHARED_POLISH_EXECUTOR is None:
                _SHARED_POLISH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="llm-polish-shared",
                )
    return _SHARED_POLISH_EXECUTOR


def _reset_shared_polish_executor() -> None:
    """Drop the singleton executor (TEST-ONLY, used by tests to assert"""
    global _SHARED_POLISH_EXECUTOR
    with _SHARED_POLISH_EXECUTOR_LOCK:
        if _SHARED_POLISH_EXECUTOR is not None:
            with contextlib.suppress(Exception):
                _SHARED_POLISH_EXECUTOR.shutdown(wait=False)
        _SHARED_POLISH_EXECUTOR = None


class _EnhancementStepsMixin:
    """Mixin: LLM polish, AI enhancement, vocabulary-automation steps."""

    # Provided by ``_OrchestratorMixin`` in the composed
    _LLM_POLISH_PIPELINE_TIMEOUT_S: float

    # Set by ``_OrchestratorMixin.__init__`` (``app: Any``). Declared on
    _app: Any
    _cycle_id: str
    _templates_applied: bool

    # transcripts above this word count skip LLM polish
    _LLM_POLISH_WORD_LIMIT: int = 1500

    def _call_polish_with_timeout(self, polisher: Any, text: str) -> str:
        """Run ``polisher.polish(text)`` in a side-thread with a hard timeout.

        Returns
        """
        import concurrent.futures

        timeout_s = self._LLM_POLISH_PIPELINE_TIMEOUT_S
        # reuse the shared singleton executor across cycles.
        executor = _get_shared_polish_executor()
        # ``polisher`` is duck-typed (``Any``) with the documented
        future: concurrent.futures.Future[str] = executor.submit(polisher.polish, text)
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            log.warning(
                "[LLM_POLISH] Polish timed out after %.1fs, returning unpolished text "
                "(the polish thread continues in the background and will exit when the "
                "inner 10s socket timeout fires or the LLM responds). (cycle=%s)",
                timeout_s,
                self._cycle_id,
            )
            return text
        # NO ``executor.shutdown(wait=False)`` here, the

    def _apply_llm_polish(self, text: str) -> str:
        """Step 7: Apply LLM polishing (if consented)."""
        effective_llm_key = self._app.config.llm_api_key or getattr(self._app.config, "openai_api_key", "")
        if self._app.config.llm_polish and effective_llm_key and getattr(self._app.config, "llm_polish_consent", False):
            # privacy NOTICE when templates were applied
            if self._templates_applied:
                log.info(
                    "[LLM_POLISH] Templates were applied before LLM polish this cycle, "
                    "text MAY contain substituted content (e.g. {clipboard}). The "
                    "redact_pii gate in llm_polish._call_api will strip common PII "
                    "patterns (cards/SSNs/emails/phones/API keys) before the API send. "
                    "(cycle=%s)",
                    self._cycle_id,
                )
                # Defense-in-depth sanity check: verify redact_pii is
                try:
                    from voice_typer.server.security import redact_pii as _redact_pii_sanity_check  # noqa: F401
                except ImportError:
                    log.warning(
                        "[LLM_POLISH] redact_pii not importable (security module broken) "
                        "AND templates were applied this cycle, skipping LLM polish to "
                        "prevent potential clipboard-content exfiltration (fail-closed). "
                        "(cycle=%s)",
                        self._cycle_id,
                    )
                    return text
            try:
                if self._app._llm_polisher is None:
                    from voice_typer.server.llm_polish import LLMPolisher

                    self._app._llm_polisher = LLMPolisher(
                        api_key=effective_llm_key,
                        api_url=self._app.config.llm_api_url or None,
                        model=self._app.config.llm_model or None,
                        preset=self._app.config.llm_preset,
                        enabled=True,
                    )
                # skip polish for long transcripts. The 4 s
                _word_count = len(text.split())
                if _word_count > self._LLM_POLISH_WORD_LIMIT:
                    log.info(
                        "[LLM_POLISH] Skipping polish for long transcript "
                        "(word_count=%d > limit=%d), preserves 4s pipeline "
                        "budget for short utterances; raw transcript returned. "
                        "(cycle=%s)",
                        _word_count,
                        self._LLM_POLISH_WORD_LIMIT,
                        self._cycle_id,
                    )
                else:
                    text = self._call_polish_with_timeout(self._app._llm_polisher, text)
            except Exception as exc:
                # redact the exception message before
                log.warning("[LLM_POLISH] Polish failed: %s", redact_secret(str(exc)))
                # previously this except block only logged a
                if not getattr(self._app, "_llm_polish_fail_notified", False):
                    self._app._llm_polish_fail_notified = True
                    with contextlib.suppress(Exception):
                        self._app.tray.notify(
                            APP_NAME,
                            "LLM polish failed. Transcription shown raw; check the log file for details.",
                        )
                with contextlib.suppress(Exception):
                    from voice_typer.server import event_bus

                    event_bus.publish({"type": "llm_polish_failed"})
        elif (
            self._app.config.llm_polish
            and effective_llm_key
            and not getattr(self._app.config, "llm_polish_consent", False)
        ) and not getattr(self._app, "_llm_consent_warned", False):
            log.info("[LLM_POLISH] llm_polish is enabled but llm_polish_consent is False, skipping polish.")
            self._app._llm_consent_warned = True
            # Surface the silent skip: publish a ``consent_required``
            with contextlib.suppress(Exception):
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "consent_required",
                        "data": {"consent_field": "llm_polish_consent"},
                    }
                )
        return text

    def _apply_ai_enhancement(self, text: str) -> str:
        """Step 7b: Apply rule-based AI enhancement (P4)."""
        try:
            from voice_typer.server.ai_enhancement import enhance_transcription

            return enhance_transcription(text, self._app.config)
        except Exception:
            log.warning("[AI_ENHANCE] Enhancement failed", exc_info=True)
            # This failure path previously published
            with contextlib.suppress(Exception):
                from voice_typer.server import event_bus

                event_bus.publish({"type": "text_enhancement_failed"})
            return text

    def _analyze_vocabulary(self, text: str) -> None:
        """Step 7c: Analyze transcription for vocabulary suggestions (P5)."""
        if not getattr(self._app.config, "vocabulary_automation_enabled", False):
            return
        try:
            automation = getattr(self._app, "_vocabulary_automation", None)
            if automation is None:
                # Lazy-init on first use. The VocabularyAutomation
                from voice_typer.server.vocabulary_automation import VocabularyAutomation

                vm = self._app._vocabulary_manager
                if vm is None:
                    from voice_typer.server.vocabulary import VocabularyManager

                    vm = VocabularyManager()
                    self._app._vocabulary_manager = vm
                automation = VocabularyAutomation(vm, self._app.config)
                self._app._vocabulary_automation = automation

            # Faster-whisper exposes segment-level avg_logprob, not
            segments: tuple = _EMPTY_SEGMENTS
            confidence: float = _NO_TRANSCRIPT_CONFIDENCE
            suggestions = automation.analyze_transcription(
                text,
                segments,
                confidence,
            )
            if not suggestions:
                return

            # Auto-apply high-confidence suggestions.
            auto_threshold = getattr(
                self._app.config,
                "vocabulary_auto_apply_threshold",
                0.95,
            )
            applied = automation.auto_apply_high_confidence_suggestions(auto_threshold)
            if applied > 0:
                log.info("[VOCAB_AUTO] Auto-applied %d high-confidence suggestions", applied)

            # Push any remaining (pending) suggestions to the frontend.
            pending = automation.get_pending_suggestions()
            if pending:
                # only re-publish when the pending list actually
                _current_sig = (
                    len(pending),
                    hashlib.sha256(
                        "\x1f".join(f"{s.original}\x1e{s.corrected}\x1e{s.confidence}" for s in pending).encode(
                            "utf-8", errors="replace"
                        )
                    ).hexdigest(),
                )
                _last_sig = getattr(self._app, "_last_vocab_sig", None)
                if _last_sig == _current_sig:
                    # No change since last publish, skip the redundant
                    log.debug(
                        "[VOCAB_AUTO] pending list unchanged (count=%d, sig=%s…), "
                        "skipping redundant vocabulary_suggestion publish. (cycle=%s)",
                        _current_sig[0],
                        _current_sig[1][:8],
                        self._cycle_id,
                    )
                else:
                    self._app._last_vocab_sig = _current_sig
                    try:
                        from voice_typer.server import event_bus

                        event_bus.publish(
                            {
                                "type": "vocabulary_suggestion",
                                "data": {
                                    "suggestions": [
                                        {
                                            "original": s.original,
                                            "corrected": s.corrected,
                                            "confidence": s.confidence,
                                            "context": s.context,
                                            "timestamp": s.timestamp,
                                        }
                                        for s in pending
                                    ],
                                },
                            }
                        )
                    except Exception:
                        log.debug(
                            "[VOCAB_AUTO] could not push vocabulary_suggestion event",
                            exc_info=True,
                        )
        except Exception:
            log.warning("[VOCAB_AUTO] Analysis failed", exc_info=True)
