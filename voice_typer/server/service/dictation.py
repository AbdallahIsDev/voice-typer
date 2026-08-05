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

    # Force cancel transcription ( Finding #3) ─────────────

    def force_cancel_transcription(self) -> "ForceCancelResult":  # noqa: F821
        """Force-cancel a stuck transcription.

        Finding #3: invokes ``_force_recover_from_stuck_transcription``
                with ``force=True`` so the busy flag and tray state are reset
                even if the transcription thread is still alive.  This gives
                the user a manual escape hatch when the 3×90s=4.5min auto-
                recovery is too slow.

                Returns ``{"success": bool, "message": str}``.

        Layering-violation follow-up:
                This method reaches into a PRIVATE method of
                ``RecordingController`` (``self._app.recording.
                _force_recover_from_stuck_transcription``), violating the
                service-layer's contract (ADR-0008-§3.1 — the service layer
                should only touch public surface of the app/controller
                layer). The correct fix is to add a PUBLIC
                ``RecordingController.force_recover(self, *, force: bool =
                False) -> None`` method that delegates to the private one,
                and have this service method call the public wrapper.

                The public-method extraction is deferred to a follow-up:
                ``voice_typer/server/controllers/recording_controller.py``
                was being edited by a parallel worker at the time this
                service-layer change was made. Editing it concurrently
                would cause a file conflict per the HARD RULES
                ("STAY IN LANE"). The follow-up either:
                  (a) adds ``force_recover()`` to the controller, OR
                  (b) restructures the controller / service split so the
                      private method moves to a service-owned module.

                Until then, this private-method call stays — it works at
                runtime (Python doesn't enforce encapsulation) but is a
                known layering smell flagged for follow-up. A ``# noqa:
                SLF001`` is NOT added because we want static-analysis
                tools (pyrefly/ruff) to keep flagging this line so the
                smell is not silently forgotten.
        """
        try:
            self._app.recording._force_recover_from_stuck_transcription(force=True)
            return {"success": True, "message": "Transcription cancelled."}
        except Exception as exc:
            log.warning("[SERVICE] force_cancel_transcription failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}
