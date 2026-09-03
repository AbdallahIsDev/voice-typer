"""Dictation domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( split). Toggle / undo / repaste / force-cancel the
active dictation.
"""

import logging
from typing import TYPE_CHECKING

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.service._base import ServiceMixinBase

if TYPE_CHECKING:
    # ``ForceCancelResult`` is a TypedDict defined in
    # ``voice_typer/server/service/__init__.py`` (which imports this
    # mixin via ``from voice_typer.server.service.dictation import
    # DictationMixin``). Importing it at runtime would create a circular
    # import, so we resolve the forward-reference annotation only under
    # ``TYPE_CHECKING`` (pyrefly / mypy) and leave the runtime annotation
    # as a string.
    from voice_typer.server.service import ForceCancelResult

log = logging.getLogger(__name__)


class DictationMixin(ServiceMixinBase):
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

    # Force cancel transcription ─────────────────────────────

    def force_cancel_transcription(self) -> "ForceCancelResult":  # noqa: F821
        """Force-cancel a stuck transcription.

        Invokes ``force_recover`` with ``force=True`` so the
                busy flag and tray state are reset even if the
                transcription thread is still alive.  This gives the
                user a manual escape hatch when the 3×90s=4.5min auto-
                recovery is too slow.

                Returns ``{"success": bool, "message": str}``.

        This method calls the PUBLIC
                ``RecordingController.force_recover`` wrapper (the
                sanctioned surface per ADR-0008 §3.1 layering — the
                service layer never reaches into controller-private
                methods). The wrapper delegates to the watchdog's
                force-recover exactly as the private delegator does.
        """
        try:
            self._app.recording.force_recover(force=True)
            return {"success": True, "message": "Transcription cancelled."}
        except Exception as exc:
            log.warning("[SERVICE] force_cancel_transcription failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}
