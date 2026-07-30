"""Vocabulary-automation IPC handler mixin (STUB — UE-15, 2026-07-30).

Historically this module defined three IPC handler methods —
``_handle_get_vocabulary_suggestions``,
``_handle_apply_vocabulary_suggestion``, and
``_handle_dismiss_vocabulary_suggestion`` — that exposed the app's
``VocabularyAutomation`` instance (confidence-score-based correction
suggestions) to the renderer.

UE-15 (2026-07-30): all three handler methods were REMOVED — the
feature was deferred pending UX redesign and the renderer's
``allowed-commands.ts`` dropped the three entries in lockstep with
the matching ``_COMMAND_REGISTRY`` cleanup. The service-layer
``VocabularyAutomation`` class still exists (constructed lazily by
the dictation pipeline); only the IPC dispatch routes were deleted.

This module is retained as a near-empty stub rather than deleted
outright because :mod:`voice_typer.server.ipc_server` imports
``VocabularyAutomationHandlersMixin`` from here and lists it as a
base class of :class:`IPCServer`. Removing the module would break
that import (which is owned by another agent's disjoint file set).
The mixin is now an empty subclass of :class:`HandlerBase` — no
behavior, no IPC surface.

If the feature is re-wired in the future, the handlers should land
here following the same mixin pattern as the other handler modules
in this package.
"""

from voice_typer.server.handlers._base import HandlerBase  # noqa: F401


class VocabularyAutomationHandlersMixin(HandlerBase):
    """Mixin: vocabulary-automation IPC handlers (STUB — UE-15).

    UE-15 (2026-07-30): the three handler methods that previously
    lived here (``_handle_get_vocabulary_suggestions``,
    ``_handle_apply_vocabulary_suggestion``,
    ``_handle_dismiss_vocabulary_suggestion``) were removed — the
    feature was deferred pending UX redesign and the renderer
    allowlist + ``_COMMAND_REGISTRY`` entries were dropped in
    lockstep. The class is retained as an empty placeholder so the
    ``IPCServer`` MRO and the
    ``from voice_typer.server.handlers import VocabularyAutomationHandlersMixin``
    re-export continue to resolve.
    """


__all__ = ["VocabularyAutomationHandlersMixin"]
