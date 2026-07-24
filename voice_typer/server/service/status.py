"""Status / health-check domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(ARCH-005 split). Read-only queries that surface app state
(tray status, xruns, audio filter chain, volume backend).
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # WR-14: ``StatusResponse`` is a TypedDict defined in
    # ``voice_typer/server/service/__init__.py`` (which imports this
    # mixin via ``from voice_typer.server.service.status import
    # StatusMixin``). Importing it at runtime would create a circular
    # import, so we resolve the forward-reference annotation only under
    # ``TYPE_CHECKING`` (pyrefly / mypy) and leave the runtime annotation
    # as a string.
    from voice_typer.server.service import StatusResponse

log = logging.getLogger(__name__)


class StatusMixin:
    """Status / health-check service methods.

    These are read-only queries over ``self._app`` state; they don't
    mutate config or trigger side effects.
    """

    # ── Status ──────────────────────────────────────────────────

    def get_status(self) -> "StatusResponse":  # noqa: F821 (forward ref resolved in __init__)
        """Return the current app state plus audio-quality telemetry.

        ERR-021: previously returned only the tray state string. The
        xrun counter was tracked in the recorder but never reached the
        IPC layer, so the UI couldn't warn the user of degraded audio.
        We now return a dict with ``status``, ``xruns_since_start``,
        and other useful fields.
        """
        app = self._app
        status_str = app.tray.state.value
        # Best-effort: xruns counter exists on the Recorder instance.
        xruns = 0
        try:
            xruns = int(getattr(app.recorder, "_xruns", 0) or 0)
        except Exception:
            log.debug("[SERVICE] could not read xrun counter", exc_info=True)
        # NEW-UX-038: read the active engine's loaded_via property.
        loaded_via = ""
        try:
            active = app.models._registry.get_active() if hasattr(app, "models") and app.models else None
            if active is not None and hasattr(active, "loaded_via"):
                loaded_via = str(active.loaded_via)
        except Exception:
            log.debug("[SERVICE] could not read loaded_via", exc_info=True)
        return {
            "status": status_str,
            "xruns_since_start": xruns,
            "loaded_via": loaded_via,
        }

    # ── Volume / Model status (ARCH-005) ────────────────────────

    def get_volume_backend_status(self) -> dict[str, object]:
        """Return the volume ducking backend status."""
        ducker = getattr(self._app, "_volume_ducker", None)
        if ducker is None:
            return {
                "available": False,
                "name": "disabled",
                "supports_per_session": False,
            }
        try:
            # Trigger initialize() so the backend name reflects
            # the actual platform backend (not "disabled"
            # merely because nothing has ducked yet).
            try:
                ducker.initialize()
            except Exception:
                log.debug("volume_ducker.initialize failed", exc_info=True)
            return {
                "available": bool(ducker.is_available),
                "name": ducker.backend_name,
                "supports_per_session": bool(ducker.supports_per_session),
                "backend": type(ducker).__name__,
            }
        except Exception as exc:
            return {
                "available": False,
                "name": "disabled",
                "supports_per_session": False,
                "reason": str(exc),
            }

    def get_audio_status(self) -> dict:
        """Return the audio filter chain status (ADR 0007).

        Wraps access to ``self._app._audio_processor`` so the IPC
        ``get_audio_status`` handler doesn't tunnel through two
        private attributes (``self.service._app._audio_processor``).

        Returns a dict with ``filter_chain``, ``degraded``,
        ``degraded_reasons``, ``latency_ms``, ``vad_backend``, and
        ``sample_rate``.  When the audio processor is absent (e.g.
        during early startup or in test fixtures), a safe default
        status is returned.
        """
        app = self._app
        processor = getattr(app, "_audio_processor", None)
        if processor is not None:
            return {
                "filter_chain": processor.filter_names,
                "degraded": processor.is_degraded,
                "degraded_reasons": processor.degraded_reasons,
                "latency_ms": processor.total_latency_ms,
                "vad_backend": "silero" if getattr(app.config, "use_silero_vad", True) else "rms",
                "sample_rate": getattr(app.config, "sample_rate", 16000),
            }
        return {
            "filter_chain": [],
            "degraded": False,
            "degraded_reasons": [],
            "latency_ms": 0.0,
            "vad_backend": "rms",
            "sample_rate": 16000,
        }
