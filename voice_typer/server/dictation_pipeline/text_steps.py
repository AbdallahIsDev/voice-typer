"""Text cleanup / vocabulary / template / punctuation step mixin.

Holds the four middle-pipeline text-transformation steps that run
between ``TranscribeStage`` and ``LLMPolishStage``:

  * :meth:`_clean_text` — Step 3: whitespace / self-correction /
    capitalization cleanup via ``text_cleanup.clean_transcribed_text``.
  * :meth:`_apply_vocabulary` — Step 4: vocabulary corrections via
    the lazily-initialized ``VocabularyManager``.
  * :meth:`_apply_templates` — Step 5: template matching via the
    lazily-initialized ``TemplateManager``. Sets
    ``self._templates_applied`` so the LLM polish step can log a
    privacy NOTICE for template-substituted (e.g. ``{clipboard}``)
    content.
  * :meth:`_apply_punctuation` — Step 6: auto-punctuation via
    ``text_cleanup._add_safe_terminal_punctuation``.

All four methods are wrapped in try/except with the same notify-once
pattern: ``log.warning`` + a session-scoped flag on ``self._app`` +
tray notification on the FIRST occurrence, then return the original
text so the dictation completes with the un-transformed transcription
instead of aborting the whole cycle.

Originally inline methods on ``DictationPipeline`` in the 2077-LOC
monolith; extracted as a mixin with NO behavior change.
"""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


class _TextStepsMixin:
    """Mixin: text cleanup, vocabulary, template, punctuation steps."""

    def _clean_text(self, text: str) -> str:
        """Step 3: Apply text cleanup (spacing, self-corrections, capitalization).

        previously the only two middle-pipeline steps NOT
        wrapped in try/except (this method and ``_apply_punctuation``).
        If either threw, the exception propagated to the outer
        ``run()`` ``except Exception`` block — the tray flipped to
        ERROR, the dictation was aborted, and the transcription was
        NEVER saved to crash recovery because ``_store_result()``
        runs AFTER these steps. Wrap in try/except matching the
        ``_apply_vocabulary`` pattern: ``log.warning(...)`` + notify-once
        + return the original text so the user sees their (uncleaned)
        transcription and the cycle completes normally.
        """
        try:
            from voice_typer.server.text_cleanup import clean_transcribed_text

            if self._app.config.text_cleanup_enabled:
                vocab_enabled = getattr(self._app.config, "vocabulary_enabled", True)
                raw = text
                text = clean_transcribed_text(
                    text,
                    auto_punctuation=False,
                    skip_corrections=vocab_enabled,
                )
                if text != raw:
                    log.info("[CLEANUP] Text cleaned: len %d -> %d", len(raw), len(text))
            else:
                log.info("[CLEANUP] Text cleanup disabled (raw mode)")
        except Exception:
            log.warning("[PIPELINE] Text cleanup failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            # (session-scoped) — see ``_apply_vocabulary`` for rationale.
            if not getattr(self._app, "_clean_text_fail_notified", False):
                self._app._clean_text_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Text cleanup failed. Check the log file for details.",
                    )
        return text

    def _apply_vocabulary(self, text: str) -> str:
        """Step 4: Apply vocabulary corrections.

        previously failures here were ``log.debug`` (invisible
        at default log level). User saw wrong text with no clue why.
        Promoted to ``log.warning`` + tray notify on first occurrence.
        """
        try:
            if self._app._vocabulary_manager is None:
                from voice_typer.server.vocabulary import VocabularyManager

                self._app._vocabulary_manager = VocabularyManager()
            text = self._app._vocabulary_manager.apply_to_text(text)
        except Exception:
            log.warning("[PIPELINE] Vocabulary correction failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            # (session-scoped) — a fresh DictationPipeline is built per
            # transcription cycle, so flags on ``self`` reset every cycle
            # and the user got a tray notification on EVERY cycle where
            # the failure occurred. /'s "notify once"
            # design depends on the flag surviving across cycles.
            if not getattr(self._app, "_vocab_fail_notified", False):
                self._app._vocab_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Vocabulary correction failed. Check the log file for details.",
                    )
        return text

    def _apply_templates(self, text: str) -> str:
        """Step 5: Apply template matching.

        promoted ``log.debug`` to ``log.warning`` + tray notify.

         (defense-in-depth observability): when a template
        match modifies the text, set ``self._templates_applied = True``
        so the downstream ``_apply_llm_polish`` step can log a privacy
        NOTICE. Templates may substitute ``{clipboard}`` with the
        user's current clipboard content (which can contain passwords,
        2FA codes, private messages) — if LLM polish is then enabled,
        that content would flow toward the third-party LLM API. The
         fix in ``llm_polish._call_api`` applies ``redact_pii``
        before the API send; this flag does NOT change that redaction
        behavior — it only makes the substituted-content flow visible
        in the log so operators can audit when template-substituted
        text is reaching the LLM redaction gate, and triggers a
        fail-closed sanity check in ``_apply_llm_polish``.
        """
        try:
            if getattr(self._app.config, "templates_enabled", True):
                if self._app._template_manager is None:
                    from voice_typer.server.templates import TemplateManager

                    self._app._template_manager = TemplateManager()
                expanded = self._app._template_manager.match(text)
                if expanded is not None:
                    log.info("[TEMPLATE] Matched template, expanded %d -> %d chars", len(text), len(expanded))
                    # mark that templates modified the text
                    # this cycle. The downstream LLM polish step uses
                    # this flag to log a privacy NOTICE and to gate a
                    # fail-closed sanity check on ``redact_pii`` — it
                    # does NOT gate or modify the polish call itself
                    # (the redaction is already applied by  inside
                    # ``llm_polish._call_api``).
                    self._templates_applied = True
                    text = expanded
        except Exception:
            log.warning("[PIPELINE] Template matching failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            # (session-scoped) — see ``_apply_vocabulary`` for rationale.
            if not getattr(self._app, "_template_fail_notified", False):
                self._app._template_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Template matching failed. Check the log file for details.",
                    )
        return text

    def _apply_punctuation(self, text: str) -> str:
        """Step 6: Apply auto-punctuation.

        previously NOT wrapped in try/except — see
        ``_clean_text`` for the rationale. ``_add_safe_terminal_punctuation``
        is a pure string operation but can still raise on malformed
        input (e.g. a ``text`` containing a surrogate that breaks
        ``str.endswith``). Return the original text on failure so the
        dictation completes.
        """
        try:
            if self._app.config.auto_punctuation:
                from voice_typer.server.text_cleanup import _add_safe_terminal_punctuation

                text = _add_safe_terminal_punctuation(text)
        except Exception:
            log.warning("[PIPELINE] Auto-punctuation failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            # (session-scoped) — see ``_apply_vocabulary`` for rationale.
            if not getattr(self._app, "_punct_fail_notified", False):
                self._app._punct_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Auto-punctuation failed. Check the log file for details.",
                    )
        return text
