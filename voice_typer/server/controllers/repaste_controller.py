"""Repaste controller: re-pastes the last transcription.

Extracted from :meth:`voice_typer.server.app.VoiceTyperApp.repaste_last`.
The controller holds a reference to the owning app (``app``) and
delegates to the existing undo/repaste infrastructure
(``app.undo.repaste_last``) so behaviour is preserved verbatim — only
the class boundary moved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class RepasteController:
    """Handles re-pasting the last transcription.

    The app passes itself (``app``) so the controller can reach the
    clipboard, history DB, and tray notification surface via the
    existing ``app.undo`` :class:`UndoRepasteController`.
    """

    def __init__(self, app: VoiceTyperApp) -> None:
        self._app = app

    def repaste_last(self) -> None:
        """Re-paste the most recent transcription.

        Delegates to ``app.undo.repaste_last()`` so the snapshot/restore
        clipboard mechanism, history-DB primary source, and localized
        toasts all behave identically to the pre-extraction method.
        """
        return self._app.undo.repaste_last()
