"""Vocabulary automation IPC handler mixin.

ARCH-REFAC-002: follows the same mixin pattern as the other handler
modules in this package.  The methods are mixed into
:class:`IPCServer` via multiple inheritance and access
``self.app`` / ``self.service`` as before.

Three IPC commands:

* ``get_vocabulary_suggestions`` — returns the pending suggestions.
* ``apply_vocabulary_suggestion`` — applies a single suggestion.
* ``dismiss_vocabulary_suggestion`` — dismisses a single suggestion.

The suggestions are managed by the app's ``VocabularyAutomation``
instance, which is lazily created by the dictation pipeline on the
first transcription after ``vocabulary_automation_enabled`` is
turned on.  If the user opens the Vocabulary page before any
dictation has occurred, the handler returns an empty list (the
automation instance doesn't exist yet, but there are also no
suggestions to show).
"""

from typing import Any

from voice_typer.server.ipc_server import log


class VocabularyAutomationHandlersMixin:
    """Mixin: vocabulary-automation IPC handlers."""

    # ARCH-REFAC-002 / TASK-10: pyrefly null-safety fix.
    # These attributes are provided at runtime by the IPCServer host
    # class via multiple inheritance. Declaring them as ``Any`` here
    # lets pyrefly type-check the mixin methods in isolation without
    # requiring a Protocol that would couple the mixin to a specific
    # service/app implementation (MagicMock fixtures in tests rely on
    # the loose typing).
    service: "Any"
    app: "Any"
    _send: "Any"

    def _handle_get_vocabulary_suggestions(self, data, resp) -> dict | None:
        """Handle the ``get_vocabulary_suggestions`` IPC command.

        Returns the pending (not-yet-applied, not-yet-dismissed)
        suggestions from the app's VocabularyAutomation instance.
        If the automation instance doesn't exist yet (no dictation
        has occurred since the feature was enabled), returns an
        empty list.
        """
        try:
            automation = getattr(self.app, "_vocabulary_automation", None)
            if automation is None:
                resp["type"] = "vocabulary_suggestions"
                resp["data"] = {"suggestions": []}
                return resp
            pending = automation.get_pending_suggestions()
            resp["type"] = "vocabulary_suggestions"
            resp["data"] = {
                "suggestions": [s.to_dict() for s in pending],
            }
        except Exception as e:
            log.error("[IPC] get_vocabulary_suggestions failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_apply_vocabulary_suggestion(self, data, resp) -> dict | None:
        """Handle the ``apply_vocabulary_suggestion`` IPC command.

        Applies a single suggestion to the user's vocabulary.  The
        ``data`` payload must contain ``original``, ``corrected``,
        ``confidence``, ``context``, and ``timestamp`` fields
        matching a suggestion previously returned by
        ``get_vocabulary_suggestions``.
        """
        try:
            if not isinstance(data, dict):
                resp["type"] = "error"
                resp["data"] = {"message": "apply_vocabulary_suggestion requires data: object"}
                return resp

            automation = getattr(self.app, "_vocabulary_automation", None)
            if automation is None:
                resp["type"] = "error"
                resp["data"] = {"message": "vocabulary automation is not initialized"}
                return resp

            # Find the matching pending suggestion.  We match on the
            # tuple (original, corrected, timestamp) which is unique
            # per suggestion within a single dictation cycle.
            original = data.get("original")
            corrected = data.get("corrected")
            timestamp = data.get("timestamp")

            if not isinstance(original, str) or not isinstance(corrected, str):
                resp["type"] = "error"
                resp["data"] = {"message": "original and corrected must be strings"}
                return resp

            from voice_typer.server.vocabulary_automation import CorrectionSuggestion

            pending = automation.get_pending_suggestions()
            target: CorrectionSuggestion | None = None
            for s in pending:
                if (
                    s.original == original
                    and s.corrected == corrected
                    and (
                        timestamp is None
                        or s.timestamp == float(timestamp)
                    )
                ):
                    target = s
                    break

            if target is None:
                resp["type"] = "error"
                resp["data"] = {"message": "suggestion not found in pending list"}
                return resp

            automation.apply_suggestion(target)
            resp["type"] = "ack"
            resp["data"] = {
                "applied": True,
                "original": target.original,
                "corrected": target.corrected,
            }
        except Exception as e:
            log.error("[IPC] apply_vocabulary_suggestion failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_dismiss_vocabulary_suggestion(self, data, resp) -> dict | None:
        """Handle the ``dismiss_vocabulary_suggestion`` IPC command.

        Dismisses a single suggestion (user rejected it).  The
        suggestion is removed from the pending queue without being
        added to the vocabulary.
        """
        try:
            if not isinstance(data, dict):
                resp["type"] = "error"
                resp["data"] = {"message": "dismiss_vocabulary_suggestion requires data: object"}
                return resp

            automation = getattr(self.app, "_vocabulary_automation", None)
            if automation is None:
                resp["type"] = "error"
                resp["data"] = {"message": "vocabulary automation is not initialized"}
                return resp

            original = data.get("original")
            corrected = data.get("corrected")
            timestamp = data.get("timestamp")

            if not isinstance(original, str) or not isinstance(corrected, str):
                resp["type"] = "error"
                resp["data"] = {"message": "original and corrected must be strings"}
                return resp

            from voice_typer.server.vocabulary_automation import CorrectionSuggestion

            pending = automation.get_pending_suggestions()
            target: CorrectionSuggestion | None = None
            for s in pending:
                if (
                    s.original == original
                    and s.corrected == corrected
                    and (
                        timestamp is None
                        or s.timestamp == float(timestamp)
                    )
                ):
                    target = s
                    break

            if target is None:
                resp["type"] = "error"
                resp["data"] = {"message": "suggestion not found in pending list"}
                return resp

            automation.dismiss_suggestion(target)
            resp["type"] = "ack"
            resp["data"] = {
                "dismissed": True,
                "original": target.original,
                "corrected": target.corrected,
            }
        except Exception as e:
            log.error("[IPC] dismiss_vocabulary_suggestion failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
