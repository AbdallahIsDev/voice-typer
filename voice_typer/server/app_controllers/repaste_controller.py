"""CR-24: RepasteController — extracted from ``VoiceTyperApp.repaste_last``.

Owns the "repaste last transcription" feature (tray menu + hotkey):
reads the latest text from ``history_db`` (with a fallback to the
in-memory ``_last_transcription``), copies it to the clipboard, and
sends a Ctrl+V keystroke.  The clipboard snapshot/restore machinery
in :class:`voice_typer.server.clipboard.ClipboardManager` preserves
the user's original clipboard content.

The actual logic lived on ``VoiceTyperApp.repaste_last`` (581-655 in
the pre-CR-24 ``app.py``).  The behaviour is preserved verbatim — only
the class boundary moved.  ``VoiceTyperApp`` keeps a thin 1-line
delegation (``def repaste_last(self): return self.repaste.repaste_last()``)
so tests that do ``monkeypatch.setattr("voice_typer.server.app.
repaste_last", ...)`` or ``app.repaste_last()`` keep working unchanged.

ADR-0010 §7.1 / DP6 / DP4 / ERR-018.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardCopyError

if TYPE_CHECKING:
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class RepasteController:
    """Owns the repaste-last-transcription feature.

    CR-24: extracted from ``VoiceTyperApp.repaste_last``.  The app
    passes itself (``app``) so the controller can read
    ``app.history_db``, ``app._last_transcription``, ``app.clipboard``,
    and surface notifications via ``app.tray.notify``.
    """

    def __init__(self, app: VoiceTyperApp | Any) -> None:
        self._app = app

    def repaste_last(self) -> None:
        """Feature: Repaste last transcription (tray menu + hotkey).

        ADR-0010 §7.1 / DP6 / DP4.

        Reads from ``history_db.get_latest_text()`` (primary — survives
        app restart), falling back to ``self._last_transcription`` if
        the DB read fails. Uses the same snapshot/restore mechanism as
        auto-paste so the user's clipboard is preserved.

        ``paste(force=True)`` bypasses the ``paste_enabled`` gate (§2.12)
        so a manual repaste works regardless of the auto-paste
        (``paste_on_stop``) setting.

        ERR-018: previously a single try/except collapsed clipboard-copy
        failures and paste-keystroke failures into one generic toast.
        We now split them so the user knows which step failed.

        Fallback chain:
          1. ``history_db.get_latest_text()``  (primary — survives restart)
          2. ``self._last_transcription``        (fallback if DB read fails)
          3. "No previous transcription" toast  (both empty)
        """
        app = self._app
        # ① READ FROM DB (primary — survives restart)
        text = ""
        try:
            text = app.history_db.get_latest_text()
        except Exception as e:
            log.warning("[REPASTE] DB read failed, falling back to memory: %s", e)
            text = app._last_transcription

        if not text:
            app.tray.notify(APP_NAME, "No previous transcription to re-paste.")
            return

        # ② COPY (snapshot + empty + pyperclip.copy + verify).
        # copy() returns None when save/restore is disabled; it raises
        # ClipboardCopyError only on a genuine copy failure.
        snapshot = None
        try:
            snapshot = app.clipboard.copy(text)
            pasted_seq = app.clipboard._clipboard_seq
        except ClipboardCopyError as e:
            log.warning("[REPASTE] Clipboard copy failed: %s", e)
            app.tray.notify(
                APP_NAME,
                "Could not copy the transcription to the clipboard. Another app may be holding the clipboard lock.",
            )
            return

        # ③ PASTE (keystroke + delayed restore scheduled inside paste()).
        # paste() schedules the restore of the user's ORIGINAL clipboard
        # at its top, before any early return (DP1). It returns False
        # (does not raise) when the keystroke is skipped/blocked/rate-
        # limited — and the restore is still scheduled. We therefore do
        # NOT call restore_now() here: that would be redundant and would
        # remove the transcription from the clipboard. The transcription
        # is safely stored in the DB. ``force=True`` bypasses the
        # ``paste_enabled`` gate (§2.12) so a manual repaste works
        # regardless of the auto-paste (``paste_on_stop``) setting.
        # pasted_seq is threaded per-request (CRIT-3) so a concurrent
        # copy() can't clobber the seq validated in paste().
        pasted = app.clipboard.paste(snapshot, pasted_text=text, force=True, pasted_seq=pasted_seq)
        if pasted:
            log.info("[REPASTE] Repasted transcription (%d chars)", len(text))
            app.tray.notify(APP_NAME, "Last transcription re-pasted")
        else:
            log.warning("[REPASTE] Paste keystroke was skipped/blocked")
            app.tray.notify(
                APP_NAME,
                "Re-paste was blocked (unsafe target or rate-limited). "
                "Your previous clipboard was preserved. Use the repaste "
                "hotkey again to try pasting.",
            )
