"""Undo controller: undoes the last paste.

Extracted from :meth:`voice_typer.server.app.VoiceTyperApp.undo_last`.
The controller holds a reference to the owning app (``app``) and
delegates to the existing undo/repaste infrastructure
(``app.undo.undo_last``) so behaviour is preserved verbatim — only
the class boundary moved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class UndoController:
    """Handles undoing the last transcription.

    The app passes itself (``app``) so the controller can reach the
    keyboard-ownership check, last-transcription memory, and tray
    notification surface via the existing ``app.undo``
    :class:`UndoRepasteController`.
    """

    def __init__(self, app: VoiceTyperApp) -> None:
        self._app = app

    def undo_last(self) -> None:
        """Undo the most recent transcription.

        Delegates to ``app.undo.undo_last()`` so the grapheme-cluster
        backspace batching, keyboard-ownership gate, and localized
        toasts all behave identically to the pre-extraction method.
        """
        return self._app.undo.undo_last()
