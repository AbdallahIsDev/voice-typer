"""Text cleanup/vocabulary stages."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


class _TextStepsMixin:
    """Mixin: text cleanup, vocabulary, template, punctuation steps."""

    # Set by ``_OrchestratorMixin.__init__`` (``app: Any``). Declared on
    _app: Any

    def _clean_text(self, text: str) -> str:
        """Step 3: Apply text cleanup (spacing, self-corrections, capitalization)."""
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
            if not getattr(self._app, "_clean_text_fail_notified", False):
                self._app._clean_text_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Text cleanup failed. Check the log file for details.",
                    )
        return text

    def _apply_vocabulary(self, text: str) -> str:
        """Step 4: Apply vocabulary corrections."""
        try:
            if self._app._vocabulary_manager is None:
                from voice_typer.server.vocabulary import VocabularyManager

                self._app._vocabulary_manager = VocabularyManager()
            text = self._app._vocabulary_manager.apply_to_text(text)
        except Exception:
            log.warning("[PIPELINE] Vocabulary correction failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            if not getattr(self._app, "_vocab_fail_notified", False):
                self._app._vocab_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Vocabulary correction failed. Check the log file for details.",
                    )
        return text

    def _apply_templates(self, text: str) -> str:
        """Step 5: Apply template matching."""
        try:
            if getattr(self._app.config, "templates_enabled", True):
                if self._app._template_manager is None:
                    from voice_typer.server.templates import TemplateManager

                    self._app._template_manager = TemplateManager()
                expanded = self._app._template_manager.match(text)
                if expanded is not None:
                    log.info("[TEMPLATE] Matched template, expanded %d -> %d chars", len(text), len(expanded))
                    # mark that templates modified the text
                    self._templates_applied = True
                    text = expanded
        except Exception:
            log.warning("[PIPELINE] Template matching failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            if not getattr(self._app, "_template_fail_notified", False):
                self._app._template_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Template matching failed. Check the log file for details.",
                    )
        return text

    def _apply_punctuation(self, text: str) -> str:
        """Step 6: Apply auto-punctuation."""
        try:
            if self._app.config.auto_punctuation:
                from voice_typer.server.text_cleanup import _add_safe_terminal_punctuation

                text = _add_safe_terminal_punctuation(text)
        except Exception:
            log.warning("[PIPELINE] Auto-punctuation failed", exc_info=True)
            # a-review Finding 2: notify-once flag lives on ``self._app``
            if not getattr(self._app, "_punct_fail_notified", False):
                self._app._punct_fail_notified = True
                with contextlib.suppress(Exception):
                    self._app.tray.notify(
                        APP_NAME,
                        "Auto-punctuation failed. Check the log file for details.",
                    )
        return text
