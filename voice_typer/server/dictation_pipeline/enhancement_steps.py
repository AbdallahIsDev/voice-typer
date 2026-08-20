"""LLM polish / AI enhancement / vocabulary-automation step mixin.

Holds the three enhancement-family steps that run after the text
cleanup steps and before the storage step:

  * :meth:`_call_polish_with_timeout` — helper that runs
    ``polisher.polish(text)`` in a side-thread with a hard
    ``_LLM_POLISH_PIPELINE_TIMEOUT_S`` timeout (4s by default).
  * :meth:`_apply_llm_polish` — Step 7: LLM polish via
    ``LLMPolisher``. Logs a privacy NOTICE when templates were
    applied this cycle and fail-closes if ``redact_pii`` is not
    importable (so template-substituted clipboard content cannot
    reach the third-party LLM API without the redaction gate).
  * :meth:`_apply_ai_enhancement` — Step 7b: rule-based AI
    enhancement via ``ai_enhancement.enhance_transcription``.
  * :meth:`_analyze_vocabulary` — Step 7c: vocabulary-automation
    analysis via the lazily-initialized ``VocabularyAutomation``.

All methods preserve the pre-split error handling: failures are
logged at WARNING and return the original text (or no-op for the
analyze step) so the dictation completes with the un-enhanced
transcription instead of aborting the whole cycle.

Originally inline methods on ``DictationPipeline`` in the 2077-LOC
monolith; extracted as a mixin with NO behavior change.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
from typing import Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.dictation_pipeline.helpers import (
    _EMPTY_SEGMENTS,
    _NO_TRANSCRIPT_CONFIDENCE,
)

log = logging.getLogger(__name__)


# module-level singleton ``ThreadPoolExecutor`` for LLM polish.
# Previously, ``_call_polish_with_timeout`` allocated a fresh
# ``ThreadPoolExecutor(max_workers=1)`` per dictation cycle and called
# ``executor.shutdown(wait=False)`` on the timeout path. On a stalled
# LLM endpoint, the worker thread kept running ``polisher.polish(text)``
# for up to 10 s (the inner socket timeout); rapid start/stop cycles
# accumulated up to 10 stalled daemon threads + orphaned sockets in a
# 40 s window.
#
# The singleton has ``max_workers=1`` so concurrent polish calls queue
# (the second waits for the first), bounding the stalled-thread count
# to 1 regardless of cycle frequency. The executor lives for the
# process lifetime; it is never shut down per cycle. At process exit,
# the daemon worker thread is killed by the interpreter shutdown.
#
# Lazy-init under a lock so the first polish call from any thread
# safely constructs the executor exactly once. Tests can call
# ``_reset_shared_polish_executor()`` to drop the singleton between
# test cases (the next polish call rebuilds it).
_SHARED_POLISH_EXECUTOR: Any | None = None
_SHARED_POLISH_EXECUTOR_LOCK = threading.Lock()


def _get_shared_polish_executor() -> Any:
    """Return the module-level singleton ``ThreadPoolExecutor``.

    Lazy-init under ``_SHARED_POLISH_EXECUTOR_LOCK`` so the first
    polish call from any thread safely constructs the executor exactly
    once. Reuses the same executor across all dictation cycles (see
    the rationale above).
    """
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
    """Drop the singleton executor (TEST-ONLY — used by tests to assert
    reuse across cycles without leaking the executor between test
    cases). Production code never calls this.
    """
    global _SHARED_POLISH_EXECUTOR
    with _SHARED_POLISH_EXECUTOR_LOCK:
        if _SHARED_POLISH_EXECUTOR is not None:
            with contextlib.suppress(Exception):
                _SHARED_POLISH_EXECUTOR.shutdown(wait=False)
        _SHARED_POLISH_EXECUTOR = None


class _EnhancementStepsMixin:
    """Mixin: LLM polish, AI enhancement, vocabulary-automation steps."""

    # transcripts above this word count skip LLM polish
    # entirely. A 1000-word transcript (~1.5 K tokens) typically
    # round-trips in 2-4 s on a healthy endpoint, but a 5000-word
    # transcript (~7.5 K tokens) takes 8-20 s — well past the 4 s
    # pipeline-side cap (``_LLM_POLISH_PIPELINE_TIMEOUT_S``). On
    # timeout the unpolished text is returned silently, so the user
    # pays for an LLM API call that never produces output. Skipping
    # polish for long transcripts preserves the 4 s budget for short
    # utterances where polish is most valuable. 1500 words ≈ 9-10
    # minutes of dictation at 150 wpm — a reasonable cutoff for
    # "long-form dictation" where the user expects the raw transcript
    # to be saved quickly and polish is a "nice-to-have" not a "must".
    _LLM_POLISH_WORD_LIMIT: int = 1500

    def _call_polish_with_timeout(self, polisher: Any, text: str) -> str:
        """Run ``polisher.polish(text)`` in a side-thread with a hard timeout.

        The dictation pipeline thread is the single bottleneck for the
        user's paste latency: while ``_apply_llm_polish`` is running,
        the pipeline cannot process new dictation triggers
        (start/stop/cancel from the hotkey path) and the user's text
        is not yet on the clipboard. Pre-fix, the synchronous
        ``polisher.polish(text)`` call blocked the pipeline for the
        full LLM round-trip (typically 1-5s, up to the 10s socket
        timeout in ``LLMPolisher._call_api`` on a stalled
        connection).

        This wrapper submits the polish call to the shared
        ``ThreadPoolExecutor`` (see the shared executor) and awaits the result with
        :data:`_LLM_POLISH_PIPELINE_TIMEOUT_S` (4s by default —
        intentionally shorter than the underlying 10s socket timeout
        so a stalled LLM endpoint does not occupy the pipeline thread
        for the full 10s). On timeout the original (unpolished) text
        is returned to the user; the polish thread keeps running in
        the background (Python cannot cancel a blocking
        ``urlopen`` call) and self-terminates when the inner 10s
        socket timeout fires or the LLM responds. The leaked thread
        is a daemon, so it cannot block process shutdown.

        the executor is a module-level singleton
        (``_get_shared_polish_executor``) with ``max_workers=1``, so
        concurrent polish calls queue (the second waits for the first)
        and the stalled-thread count is bounded to 1 regardless of
        cycle frequency. The executor is NEVER shut down per cycle —
        it lives for the process lifetime.

        On exception (network error, LLM API error, redact_pii
        failure inside ``_call_api``) the exception propagates to the
        caller (``_apply_llm_polish``'s ``except Exception`` block)
        so the existing notification / event-bus-publish path runs
        unchanged.

        Parameters
        ----------
        polisher : LLMPolisher
            The polisher instance (real or mock). Must expose
            ``polish(text: str) -> str``.
        text : str
            The text to polish.

        Returns
        -------
        str
            The polished text on success, or the original text on
            timeout.
        """
        import concurrent.futures

        timeout_s = self._LLM_POLISH_PIPELINE_TIMEOUT_S
        # reuse the shared singleton executor across cycles.
        # ``max_workers=1`` means a concurrent polish call queues
        # behind the first (the queue is unbounded by default — see
        # ``concurrent.futures.ThreadPoolExecutor`` docs — so the
        # submit call never blocks). The executor is never shut down
        # here; it lives for the process lifetime.
        executor = _get_shared_polish_executor()
        future = executor.submit(polisher.polish, text)
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            log.warning(
                "[LLM_POLISH] Polish timed out after %.1fs — returning unpolished text "
                "(the polish thread continues in the background and will exit when the "
                "inner 10s socket timeout fires or the LLM responds). (cycle=%s)",
                timeout_s,
                self._cycle_id,
            )
            return text
        # NO ``executor.shutdown(wait=False)`` here — the
        # executor is shared across cycles and must not be torn down
        # on the timeout path. The daemon worker thread exits on its
        # own when ``polish`` returns (or the inner socket timeout
        # fires); the executor itself lives until process teardown.

    def _apply_llm_polish(self, text: str) -> str:
        """Step 7: Apply LLM polishing (if consented).

        (defense-in-depth observability + fail-closed): if
        templates were applied earlier in this cycle
        (``self._templates_applied``), the text MAY contain
        clipboard-substituted content (passwords, 2FA codes, private
        messages from ``{clipboard}``). When LLM polish is enabled,
        that content would flow to a third-party LLM API. The
        fix in ``llm_polish._call_api`` applies ``redact_pii`` to the
        user-content before the API send — this method does NOT
        duplicate that redaction (it would change the final pasted
        text on polish-failure paths). Instead, it:

          1. Logs a privacy NOTICE so operators can audit when
             template-substituted content is flowing toward the
             redaction gate.
          2. Performs a sanity check that ``redact_pii`` is importable
             BEFORE calling ``polish()``. If the import fails AND
             templates were applied this cycle, polish is SKIPPED
             entirely (fail-closed) — without ``redact_pii``, the
             gate inside ``_call_api`` would also fail open
             (its try/except falls through to sending the original
             text). Skipping polish preserves the original text on
             the paste path (the user sees their transcription, not a
             leaked LLM payload). When templates were NOT applied,
             the sanity check is skipped — the text is the user's own
             dictation, not substituted content, so the privacy risk
             is much lower and the  fail-open is acceptable.
        """
        effective_llm_key = self._app.config.llm_api_key or getattr(self._app.config, "openai_api_key", "")
        if self._app.config.llm_polish and effective_llm_key and getattr(self._app.config, "llm_polish_consent", False):
            # privacy NOTICE when templates were applied
            # before LLM polish. The  redaction gate inside
            # ``llm_polish._call_api`` strips common PII patterns
            # (credit cards, SSNs, emails, phone numbers, API keys)
            # before the API send — but operators should be able to
            # audit when template-substituted content is flowing
            # toward that gate. Logged at INFO so it's visible at the
            # default log level without being alarmist (the redaction
            # is in place; this is observability, not a warning).
            if self._templates_applied:
                log.info(
                    "[LLM_POLISH] Templates were applied before LLM polish this cycle — "
                    "text MAY contain substituted content (e.g. {clipboard}). CR-10 "
                    "redact_pii gate in llm_polish._call_api will strip common PII "
                    "patterns (cards/SSNs/emails/phones/API keys) before the API send. "
                    "(cycle=%s)",
                    self._cycle_id,
                )
                # Defense-in-depth sanity check: verify redact_pii is
                # importable BEFORE calling polish(). If the import
                # fails, the gate inside _call_api would also
                # fail open (its try/except falls through to sending
                # the original text). Skip polish entirely
                # (fail-closed) so the un-redacted clipboard-
                # substituted text does NOT reach the LLM API.
                try:
                    from voice_typer.server.security import redact_pii as _redact_pii_sanity_check  # noqa: F401
                except ImportError:
                    log.warning(
                        "[LLM_POLISH] redact_pii not importable (security module broken) "
                        "AND templates were applied this cycle — skipping LLM polish to "
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
                # pipeline-side cap (``_LLM_POLISH_PIPELINE_TIMEOUT_S``)
                # is tuned for short utterances (a 1000-word transcript
                # round-trips in 2-4 s on a healthy endpoint). Long
                # transcripts (1500+ words ≈ 9-10 min @ 150 wpm)
                # typically take 8-20 s — well past the 4 s cap — so
                # on timeout the user pays for an LLM API call that
                # never produces output, and a leaked daemon thread
                # keeps running for up to 10 s. Skipping polish here
                # preserves the 4 s budget for short utterances where
                # polish is most valuable, and avoids the leaked-
                # thread overhead for long dictations where the raw
                # transcript is already useful. Word count is a cheap
                # proxy for token count (no tokenizer needed) —
                # ``str.split()`` on whitespace is ~1 µs per 1k words.
                _word_count = len(text.split())
                if _word_count > self._LLM_POLISH_WORD_LIMIT:
                    log.info(
                        "[LLM_POLISH] Skipping polish for long transcript "
                        "(word_count=%d > limit=%d) — preserves 4s pipeline "
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
                # logging. LLM API errors can echo the request URL +
                # Authorization header (which carries the API key) back
                # in their body; ``redact_secret`` masks ``Bearer …`` /
                # ``sk-…`` / 20+ char bare tokens so the log line is
                # safe to surface in the tray / log file.
                from voice_typer.server._secrets import redact_secret

                log.warning("[LLM_POLISH] Polish failed: %s", redact_secret(str(exc)))
                # previously this except block only logged a
                # WARNING — the user paid for an LLM API call that never
                # produced output (or believed the feature was broken)
                # with NO diagnostic. Mirror the ``_apply_vocabulary``
                # notify-once pattern (tray notification on the FIRST
                # failure per session) AND publish a ``llm_polish_failed``
                # event to the in-process event bus so the renderer can
                # surface a one-time toast. The push event shape is a
                # bare ``{"type": "llm_polish_failed"}`` frame (no
                # payload) — see ``LLMPolishFailedEvent`` in
                # ``voice_typer/client/src/renderer/src/types/ipc/push_events.ts``.
                # The transcription itself is still delivered to the
                # user UN-polished (the original ``text`` is returned
                # below), so the event is purely informational.
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
            log.info("[LLM_POLISH] llm_polish is enabled but llm_polish_consent is False — skipping polish.")
            self._app._llm_consent_warned = True
            # Surface the silent skip: publish a ``consent_required``
            # event so the renderer's unified point-of-use consent
            # dialog can offer to grant ``llm_polish_consent`` in
            # place (previously the skip was invisible — the user had
            # no idea the polish toggle was doing nothing). Once
            # granted, polish applies to the NEXT transcription; there
            # is no re-runnable action from here, so no retry is
            # attached.
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
        """Step 7b: Apply rule-based AI enhancement (P4).

        Delegates to ``voice_typer.server.ai_enhancement.enhance_transcription``,
        which reads the four ``ai_enhancement_*`` / ``auto_*`` /
        ``fix_grammar_basics`` flags off the config. The master
        toggle (``ai_enhancement_enabled``) defaults OFF — when off,
        ``enhance_transcription`` returns the text unchanged.

         hardening: failures here are logged at WARNING
        level but do NOT abort the pipeline. The original text is
        returned so the dictation completes and the user sees their
        (un-enhanced) transcription rather than an error.
        """
        try:
            from voice_typer.server.ai_enhancement import enhance_transcription

            return enhance_transcription(text, self._app.config)
        except Exception:
            log.warning("[AI_ENHANCE] Enhancement failed", exc_info=True)
            from voice_typer.server import event_bus

            event_bus.publish({"type": "llm_polish_failed"})
            return text

    def _analyze_vocabulary(self, text: str) -> None:
        """Step 7c: Analyze transcription for vocabulary suggestions (P5).

        Delegates to the app's ``VocabularyAutomation`` instance. The
        master toggle (``vocabulary_automation_enabled``) defaults
        OFF — when off, this method is a no-op.

        Suggestions above ``vocabulary_auto_apply_threshold`` are
        auto-applied (added to the user's vocabulary); the rest are
        queued for the user to review via the IPC handlers in
        ``vocabulary_automation_handlers.py``.

         hardening: failures here are logged at WARNING
        level but do NOT abort the pipeline. The transcription is
        already complete; vocabulary suggestions are a side-channel
        for future improvements.
        """
        if not getattr(self._app.config, "vocabulary_automation_enabled", False):
            return
        try:
            automation = getattr(self._app, "_vocabulary_automation", None)
            if automation is None:
                # Lazy-init on first use. The VocabularyAutomation
                # constructor needs the existing VocabularyManager
                # (so it can read the user's current vocabulary and
                # apply suggestions to it) and the config (for the
                # thresholds).
                from voice_typer.server.vocabulary_automation import VocabularyAutomation

                vm = self._app._vocabulary_manager
                if vm is None:
                    from voice_typer.server.vocabulary import VocabularyManager

                    vm = VocabularyManager()
                    self._app._vocabulary_manager = vm
                automation = VocabularyAutomation(vm, self._app.config)
                self._app._vocabulary_automation = automation

            # Faster-whisper exposes segment-level avg_logprob, not
            # per-word confidence. We pass an empty segment list and
            # a sentinel confidence; the analyzer degrades gracefully
            # (treats the whole text as one segment with the given
            # confidence). When the transcription engine exposes
            # richer segment data in the future, we can plumb it
            # through here without changing the analyzer's API.
            # the previous ``getattr(self, "_segments", None) or []``
            # and ``getattr(self, "_confidence", 0.9)`` fell back to a
            # fabricated confidence of ``0.9`` when the attributes were
            # absent — that fed vocabulary-automation with a confident
            # empty segment list, causing the analyzer to consider
            # every word as high-confidence. Replaced with explicit
            # module-level sentinels (no ``self.*`` reads, no
            # fabricated confidence). The analyzer's degrade-gracefully
            # path now sees honest empty data.
            segments: list = _EMPTY_SEGMENTS
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
                # changed since the last publish. Previously, every
                # cycle where the pending list was non-empty re-
                # serialized and re-published ALL pending suggestions
                # (up to MAX_PENDING=200, ~50 KB per event). For 1000
                # cycles with a full pending list, that was ~50 MB of
                # redundant IPC traffic. The signature is
                # ``(count, sha256_of_serialized_items)`` — count is a
                # cheap short-circuit for the common case (suggestion
                # accepted / dismissed → count changes); the hash
                # catches the rare case where the count is the same but
                # the contents changed (e.g. a suggestion's confidence
                # was bumped by a re-analysis). The signature lives on
                # ``self._app`` (session-scoped) via ``getattr`` /
                # ``setattr`` so this fix does NOT touch ``app.py``
                # (owned by another file group). ``None`` sentinel =
                # "never published" → first non-empty list always
                # publishes.
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
                    # No change since last publish — skip the redundant
                    # IPC event. Logged at DEBUG so the delta-publish
                    # behavior is observable without spamming the log
                    # at the default level.
                    log.debug(
                        "[VOCAB_AUTO] pending list unchanged (count=%d, sig=%s…) — "
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
