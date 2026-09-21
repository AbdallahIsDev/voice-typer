"""Undo/repaste helpers for VoiceTyperApp."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardCopyError

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (``app`` imports
    pass

# Use the same logger name as ``voice_typer.server.app`` so any future
log = logging.getLogger("voice_typer.server.app")


class UndoRepasteController:
    """itself (``app``) so ``UndoRepasteController`` can:
    - Read ``app._last_transcription`` (memory fallback for repaste;
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    def repaste_last(self) -> None:
        """ADR-0010 §7.1 / DP6 / DP4.
        Reads from ``history_db.get_latest_text()`` (primary, survives
        """
        app = self._app
        # ① READ FROM DB (primary, survives restart)
        text = ""
        try:
            # ADR-0010 §6.2: the dictation pipeline's
            app.history_db.flush()
            text = app.history_db.get_latest_text()
        except Exception as e:
            log.warning("[REPASTE] DB read failed, falling back to memory: %s", e)
            text = app._last_transcription

        if not text:
            # i18n key for localized notification.
            app.tray.notify(APP_NAME, i18n.t("notify.app.repaste_no_previous"))
            return

        # ② COPY (snapshot + empty + pyperclip.copy + verify).
        snapshot = None
        try:
            snapshot = app.clipboard.copy(text)
            pasted_seq = app.clipboard._clipboard_seq
        except ClipboardCopyError as e:
            log.warning("[REPASTE] Clipboard copy failed: %s", e)
            # i18n key for localized notification.
            app.tray.notify(
                APP_NAME,
                i18n.t("notify.app.repaste_copy_failed"),
            )
            return

        # ③ PASTE (keystroke + delayed restore scheduled inside paste()).
        pasted = app.clipboard.paste(snapshot, pasted_text=text, force=True, pasted_seq=pasted_seq)
        if pasted:
            log.info("[REPASTE] Repasted transcription (%d chars)", len(text))
            # Use the i18n key so the tray notification renders in the
            app.tray.notify(APP_NAME, i18n.t("notify.app.repaste_done"))
        else:
            log.warning("[REPASTE] Paste keystroke was skipped/blocked")
            app.tray.notify(
                APP_NAME,
                i18n.t("notify.app.repaste_blocked"),
            )

    def undo_last(self) -> None:
        """Sends one backspace per character in the last transcription.
        Works by simulating keyboard input via the hotkey backend's
        """
        app = self._app
        # keyboard-ownership check, mirror _cancel_dictation.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[UNDO] skipping undo, frontend hotkey capture active")
                return
        except Exception:
            log.debug("[UNDO] keyboard ownership check failed", exc_info=True)
        if not app._last_transcription:
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_nothing"))
            return
        text = app._last_transcription
        # count grapheme clusters using the regex library
        try:
            import regex as _regex

            char_count = len(_regex.findall(r"\X", text))
        except ImportError:
            # Fallback: code-point count (pre- behavior, buggy
            char_count = len(text)
        log.info("[UNDO] Undoing last transcription (%d chars)", char_count)
        try:
            # (cont.): use ``import pynput.keyboard as _pk_keyboard``
            import pynput.keyboard as _pk_keyboard

            kb = _pk_keyboard.Controller()
            # Select all text in the current field first (Ctrl+A), then
            _undo_chunk_size = 10
            # Clear ``_last_transcription`` BEFORE the backspace
            app._last_transcription = ""
            for _i in range(char_count):
                kb.press("\x08")
                kb.release("\x08")
                if (_i + 1) % _undo_chunk_size == 0 and (_i + 1) < char_count:
                    time.sleep(0.01)
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_done", char_count=char_count))
        except ImportError:
            log.warning("[UNDO] pynput not available for undo")
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_no_pynput"))
        except Exception as e:
            log.warning("[UNDO] Failed: %s", e)
            # do NOT interpolate the raw exception (or even
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_failed"))
