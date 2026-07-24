"""Dictation domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(ARCH-005 split). Toggle / undo / repaste / force-cancel the
active dictation.
"""

import logging

from voice_typer.server._secrets import redact_secret, redact_url

log = logging.getLogger(__name__)


class DictationMixin:
    """Dictation-domain service methods.

    Thin wrappers over ``self._app`` that start/stop/undo dictation
    and force-cancel a stuck transcription thread.
    """

    # ── Dictation ───────────────────────────────────────────────

    def toggle_dictation(self) -> None:
        """Start or stop dictation."""
        self._app.toggle_dictation()

    def undo_last(self) -> None:
        """Undo the last transcription via backspace keystrokes."""
        self._app.undo_last()

    def repaste_last(self) -> None:
        """Re-paste the last transcription."""
        self._app.repaste_last()

    # ── Force cancel transcription (PR-2 Finding #3) ─────────────

    def force_cancel_transcription(self) -> "ForceCancelResult":  # noqa: F821
        """Force-cancel a stuck transcription.

        PR-2 Finding #3: invokes ``_force_recover_from_stuck_transcription``
        with ``force=True`` so the busy flag and tray state are reset
        even if the transcription thread is still alive.  This gives
        the user a manual escape hatch when the 3×90s=4.5min auto-
        recovery is too slow.

        Returns ``{"success": bool, "message": str}``.
        """
        try:
            self._app.recording._force_recover_from_stuck_transcription(force=True)
            return {"success": True, "message": "Transcription cancelled."}
        except Exception as exc:
            log.warning("[SERVICE] force_cancel_transcription failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}
