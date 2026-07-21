"""CR-24: UndoController — extracted from ``VoiceTyperApp.undo_last``.

Owns the "undo last transcription" feature (UX-003 / FIX-10): sends
one backspace keystroke per character in the last transcription via
``pynput.keyboard.Controller``.

The actual logic lived on ``VoiceTyperApp.undo_last`` (656-693 in the
pre-CR-24 ``app.py``).  The behaviour is preserved verbatim — only
the class boundary moved.  ``VoiceTyperApp`` keeps a thin 1-line
delegation (``def undo_last(self): return self.undo.undo_last()``) so
tests that do ``monkeypatch.setattr("voice_typer.server.app.undo_last",
...)`` or ``app.undo_last()`` keep working unchanged.

UX-003 / FIX-10.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME

if TYPE_CHECKING:
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class UndoController:
    """Owns the undo-last-transcription feature.

    CR-24: extracted from ``VoiceTyperApp.undo_last``.  The app passes
    itself (``app``) so the controller can read
    ``app._last_transcription`` (the in-memory copy of the most recent
    transcription, cleared after a successful undo) and surface
    notifications via ``app.tray.notify``.
    """

    def __init__(self, app: VoiceTyperApp | Any) -> None:
        self._app = app

    def undo_last(self) -> None:
        """UX-003: Undo last transcription by sending backspace keystrokes.

        Sends one backspace per character in the last transcription.
        Works by simulating keyboard input via the hotkey backend's
        keyboard controller (pynput on all platforms).
        """
        app = self._app
        if not app._last_transcription:
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_nothing"))
            return
        text = app._last_transcription
        char_count = len(text)
        log.info("[UNDO] Undoing last transcription (%d chars)", char_count)
        try:
            # Use pynput to send backspace keystrokes
            from pynput.keyboard import Controller as KeyboardController

            kb = KeyboardController()
            # Select all text in the current field first (Ctrl+A), then
            # Delete — this is more reliable than sending N backspaces
            # because it handles multi-line text and doesn't leave
            # partial characters.
            # However, Ctrl+A selects ALL text in the field, which may
            # be more than just our transcription.  So we send N
            # backspaces instead — this is the standard "undo paste"
            # behavior.
            for _ in range(char_count):
                kb.press("\x08")  # Backspace
                kb.release("\x08")
            app._last_transcription = ""
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_done", char_count=char_count))
        except ImportError:
            log.warning("[UNDO] pynput not available for undo")
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_no_pynput"))
        except Exception as e:
            log.warning("[UNDO] Failed: %s", e)
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_failed", error=e))
