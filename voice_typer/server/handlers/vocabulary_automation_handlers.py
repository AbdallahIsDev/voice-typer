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

R4-F4 (IMPROVE-mode run, 2026-07-19): extracted
:func:`_find_pending_suggestion` from the duplicated lookup+validation
block in ``_handle_apply_vocabulary_suggestion`` and
``_handle_dismiss_vocabulary_suggestion`` (was ~63 LOC of copy-paste
across the two handlers). Both handlers now call the helper, then
dispatch to ``automation.apply_suggestion`` / ``automation.dismiss_suggestion``
based on which handler they are. The helper does NOT mutate the
automation state — it returns the matched suggestion (or an error
message) and leaves the action to the caller, preserving the original
behavioral split (``apply`` and ``dismiss`` are separate IPC commands
on the wire).

Inline validation-error responses now
route through :func:`_error_response` with explicit ``code`` values
(``not_initialized`` / ``invalid_payload`` / ``invalid_field`` /
``not_found``) so clients can branch on ``code`` rather than
pattern-matching the message text. The catch-all ``except Exception``
blocks call :meth:`HandlerBase._respond_with_error` (CR-20 generic
WS-path envelope — no ``str(e)`` leak).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.ipc.validation import ErrorCodes, _error_response

if TYPE_CHECKING:
    # Typed parameters for :func:`_find_pending_suggestion`.
    # Imported under ``TYPE_CHECKING`` to avoid a runtime cycle (the
    # ``vocabulary_automation`` module is constructed lazily by the
    # dictation pipeline; importing it eagerly here would pull in a
    # heavier dependency graph at IPC-server boot).
    from voice_typer.server.vocabulary_automation import (
        CorrectionSuggestion,
        VocabularyAutomation,
    )


def _find_pending_suggestion(
    automation: VocabularyAutomation, data: object
) -> tuple[CorrectionSuggestion | None, str | None]:
    """Look up a pending suggestion matching the client-supplied fields.

    R4-F4: extracted from the duplicated validation+lookup block that
    previously lived inline in both ``_handle_apply_vocabulary_suggestion``
    and ``_handle_dismiss_vocabulary_suggestion``.

    Validates the ``data`` payload shape (non-dict, ``original`` and
    ``corrected`` must be strings) and searches ``automation``'s
    pending list for a suggestion matching ``(original, corrected,
    optional timestamp)``. The match logic mirrors the prior inline
    implementation:

    * ``original`` and ``corrected`` are compared by equality.
    * ``timestamp`` is optional. When absent from ``data`` (or ``None``),
      the timestamp comparison is skipped — the matcher accepts the
      first suggestion whose ``original``/``corrected`` match. When
      present, the matcher coerces it to ``float`` and compares
      against ``s.timestamp`` (a float on the suggestion dataclass).

    Parameters
    ----------
    automation : VocabularyAutomation
        The lazily-created automation instance from
        ``app._vocabulary_automation``. The caller has already verified
        it's not None.
    data : Any
        The ``data`` field from the IPC message.

    Returns
    -------
    tuple[CorrectionSuggestion | None, str | None]
        ``(target, None)`` on a successful lookup — ``target`` is the
        matching ``CorrectionSuggestion`` instance ready to be passed
        to ``automation.apply_suggestion`` / ``automation.dismiss_suggestion``.
        ``(None, error_message)`` on a validation or lookup failure —
        the caller should stamp the error_message onto ``resp["data"]``
        and return. The helper does NOT mutate ``resp`` so the caller
        retains full control of the response envelope (e.g. for the
        ``code: "handler_error"`` stamp added by R13-F3).
    """
    if not isinstance(data, dict):
        return None, "requires data: object"

    original = data.get("original")
    corrected = data.get("corrected")
    timestamp = data.get("timestamp")

    if not isinstance(original, str) or not isinstance(corrected, str):
        return None, "original and corrected must be strings"

    pending = automation.get_pending_suggestions()
    for s in pending:
        if (
            s.original == original
            and s.corrected == corrected
            and (timestamp is None or s.timestamp == float(timestamp))
        ):
            return s, None

    return None, "suggestion not found in pending list"


# Map ``_find_pending_suggestion``'s error messages to
# structured ``code`` values so the renderer can branch on ``code``
# rather than pattern-matching the message text. Used by both the
# ``apply`` and ``dismiss`` handlers.
# EC-10 / XS-11: use the namespaced ``ErrorCodes`` registry (single
# source of truth) instead of legacy un-prefixed strings, so the wire
# contract is consistent across every handler. The keys here are the
# helper's error messages; the values are the canonical
# ``ErrorCodes.*`` constants.
_SUGGESTION_ERROR_CODES = {
    "original and corrected must be strings": ErrorCodes.INVALID_FIELD,
    "suggestion not found in pending list": ErrorCodes.NOT_FOUND,
}


class VocabularyAutomationHandlersMixin(HandlerBase):
    """Mixin: vocabulary-automation IPC handlers.

    CR-20: this mixin's ``except Exception`` catch-alls call
    :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
    no ``str(e)`` leak).
    """

    def _handle_get_vocabulary_suggestions(self, data: dict | None, resp: dict) -> dict | None:
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
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_vocabulary_suggestions")
        return resp

    def _handle_apply_vocabulary_suggestion(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``apply_vocabulary_suggestion`` IPC command.

        Applies a single suggestion to the user's vocabulary.  The
        ``data`` payload must contain ``original``, ``corrected``,
        ``confidence``, ``context``, and ``timestamp`` fields
        matching a suggestion previously returned by
        ``get_vocabulary_suggestions``.
        """
        try:
            automation = getattr(self.app, "_vocabulary_automation", None)
            if automation is None:
                # Stamp the structured ``code`` so the
                # renderer can branch on ``not_initialized`` rather
                # than pattern-matching the message text.
                # EC-10 / XS-11: use the namespaced
                # ``ErrorCodes.NOT_INITIALIZED`` registry value instead
                # of the legacy un-prefixed string.
                return _error_response(
                    resp,
                    "vocabulary automation is not initialized",
                    code=ErrorCodes.NOT_INITIALIZED,
                )

            # R4-F4: delegate validation + lookup to the shared helper.
            target, error_message = _find_pending_suggestion(automation, data)
            if target is None:
                # Narrow ``error_message`` from
                # ``str | None`` to ``str`` before passing to
                # ``_error_response(message: str)`` and
                # ``dict.get(key: str, ...)``. ``_find_pending_suggestion``
                # only returns ``(target, None)`` on success (target is
                # non-None), so in this ``target is None`` branch
                # ``error_message`` is always a non-None str — but
                # pyrefly can't infer that, so we add the explicit
                # guard with a defensive fallback.
                msg = error_message if error_message is not None else "suggestion lookup failed"
                # Pre-R4-F4 the non-dict path emitted a handler-specific
                # message ("apply_vocabulary_suggestion requires data:
                # object"). The helper returns the generic "requires
                # data: object" — preserve the handler-specific prefix
                # by prepending the command name so existing tests that
                # assert "data: object" in resp["data"]["message"] keep
                # passing.
                #
                # Route through ``_error_response`` with
                # a structured ``code`` (``invalid_payload`` for the
                # non-dict path; ``invalid_field`` / ``not_found`` for
                # the lookup-failure paths — see
                # ``_SUGGESTION_ERROR_CODES``).
                # EC-10 / XS-11: use the namespaced ``ErrorCodes``
                # registry value for the code parameter.
                if msg == "requires data: object":
                    return _error_response(
                        resp,
                        f"apply_vocabulary_suggestion {msg}",
                        code=ErrorCodes.INVALID_PAYLOAD,
                    )
                return _error_response(
                    resp,
                    msg,
                    code=_SUGGESTION_ERROR_CODES.get(msg, ErrorCodes.HANDLER_ERROR),
                )

            automation.apply_suggestion(target)
            resp["type"] = "ack"
            resp["data"] = {
                "applied": True,
                "original": target.original,
                "corrected": target.corrected,
            }
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "apply_vocabulary_suggestion")
        return resp

    def _handle_dismiss_vocabulary_suggestion(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``dismiss_vocabulary_suggestion`` IPC command.

        Dismisses a single suggestion (user rejected it).  The
        suggestion is removed from the pending queue without being
        added to the vocabulary.
        """
        try:
            automation = getattr(self.app, "_vocabulary_automation", None)
            if automation is None:
                # Stamp the structured ``code`` (same as
                # the apply path above).
                # EC-10 / XS-11: use the namespaced
                # ``ErrorCodes.NOT_INITIALIZED`` registry value.
                return _error_response(
                    resp,
                    "vocabulary automation is not initialized",
                    code=ErrorCodes.NOT_INITIALIZED,
                )

            # R4-F4: delegate validation + lookup to the shared helper.
            target, error_message = _find_pending_suggestion(automation, data)
            if target is None:
                # Narrow ``error_message`` from
                # ``str | None`` to ``str`` — see the apply handler
                # above for the full rationale.
                msg = error_message if error_message is not None else "suggestion lookup failed"
                # Same code-mapping logic as the apply
                # handler above — see the comment there for the
                # handler-specific message-prefix preservation.
                # EC-10 / XS-11: use the namespaced ``ErrorCodes``
                # registry value.
                if msg == "requires data: object":
                    return _error_response(
                        resp,
                        f"dismiss_vocabulary_suggestion {msg}",
                        code=ErrorCodes.INVALID_PAYLOAD,
                    )
                return _error_response(
                    resp,
                    msg,
                    code=_SUGGESTION_ERROR_CODES.get(msg, ErrorCodes.HANDLER_ERROR),
                )

            automation.dismiss_suggestion(target)
            resp["type"] = "ack"
            resp["data"] = {
                "dismissed": True,
                "original": target.original,
                "corrected": target.corrected,
            }
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "dismiss_vocabulary_suggestion")
        return resp
